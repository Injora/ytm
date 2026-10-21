"""DJ booth behaviour: deck rendering, transport, visualizer, mixer, lyrics."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING

from textual import work
from textual.markup import escape

from pixelfm.services.downloads import DownloadStatus, eligible_for_download
from pixelfm.services.playback import VOLUME_STEP, clamp_volume
from pixelfm.state import ACTIVE, AudioFx, PlaybackState, PlaybackStatus
from pixelfm.widgets import (
    BoothHeader,
    Fader,
    LevelMeter,
    LyricsView,
    Spectrum,
    StatusLevel,
    StereoMeter,
    Timeline,
    Toggle,
    Transport,
)

if TYPE_CHECKING:
    from pixelfm.models import Song

FX_STEP = 2
#: Seconds the meters keep falling after playback stops, then the timer sleeps.
DECAY_SECONDS = 1.5
#: Light each lyric line this many seconds early; positions arrive ~4x a second.
LYRICS_LEAD = 0.25


class BoothMixin:
    """Mixed into :class:`~pixelfm.app.PixelFMApp`."""

    # ------------------------------------------------------------- deck

    def _deck_song(self) -> Song | None:
        """What the deck shows: the loaded track, else the restored current one."""
        if self.playback.song is not None:
            return self.playback.song
        current = self.queue.current
        return current.song if current else None

    def _newest_song(self) -> Song | None:
        """The track most recently asked for. The player publishes it a moment
        later, so artwork and palette workers check against this, not the deck,
        to tell whether their result is still wanted."""
        if self._requested is not None:
            return self._requested.song
        return self._deck_song()

    def _status_badge(self, state: PlaybackState) -> str:
        g = self.glyphs
        spin = g.spinner[self._frame % len(g.spinner)] if self.effects else g.loading
        song = self._deck_song()
        match state.status:
            case PlaybackStatus.LOADING:
                detail = (state.detail or "buffering").upper()
                return f"[b $primary]{spin} {escape(detail)}[/]"
            case PlaybackStatus.PLAYING:
                return f"[b $primary]{g.playing} PLAYING[/]"
            case PlaybackStatus.PAUSED:
                return f"[b $warning]{g.paused} PAUSED[/]"
            case PlaybackStatus.STOPPED:
                return f"[b $text-muted]{g.stopped} STOPPED[/]"
            case PlaybackStatus.ENDED:
                return f"[b $text-muted]{g.stopped} COMPLETED[/]"
            case PlaybackStatus.ERROR:
                return f"[b $error]{g.error} FAILED[/]"
        if song is not None:
            return (
                "[b $secondary]◆ READY[/]" if self.unicode else "[b $secondary]READY[/]"
            )
        return f"[b $text-muted]{g.stopped} IDLE[/]"

    def _render_deck(self, state: PlaybackState) -> None:
        g = self.glyphs
        song = self._deck_song()
        # Not loaded into the player (e.g. it refused to cue): show the saved
        # position. A cued track is PAUSED with the position in its state.
        unloaded = state.song is None and song is not None
        cued = self._restored_uid is not None and state.status is PlaybackStatus.PAUSED
        title = self.query_one("#deck-title")
        artist = self.query_one("#deck-artist")
        meta = self.query_one("#deck-meta")
        status = self.query_one("#deck-state")
        if song is None:
            title.update("[b $text-muted]NO TRACK LOADED[/]")
            artist.update(
                "[$text-muted]press [b]2[/] to search, [b]5[/] for liked songs[/]"
            )
            meta.update("")
        else:
            title.update(f"[b $primary]{escape(song.title)}[/]")
            artist.update(f"[b $secondary]{escape(song.artist)}[/]")
            source = (
                "LOCAL FILE"
                if state.local
                else ("MOCK" if self.player.name == "simulated" else "YOUTUBE")
            )
            parts = [
                f"[$text-muted]SRC[/] {source}",
                f"[$text-muted]LEN[/] {song.duration_label}",
            ]
            if song.id in self._favorites:
                parts.append(f"[$secondary]{g.favorite} LIKED[/]")
            job = self.downloads.jobs.get(song.id)
            if (
                song.id in self._download_paths
                and song.id not in self._missing_downloads
            ):
                parts.append(f"[$primary]{g.download} SAVED[/]")
            elif job is not None and job.status is DownloadStatus.DOWNLOADING:
                pct = f"{job.fraction * 100:.0f}%" if job.fraction is not None else "…"
                parts.append(f"[$primary]{g.download} {pct}[/]")
            meta.update(f" [$border-blurred]{g.separator}[/] ".join(parts))
        badge = self._status_badge(state)
        position = state.position if cued else self._restored_position
        was = (
            " · was playing when PixelFM closed"
            if self._restored_status == "playing"
            else ""
        )
        if (cued or unloaded) and position:
            badge += (
                f"  [$text-muted]restored at {int(position) // 60}:"
                f"{int(position) % 60:02d}{was} · space to resume[/]"
            )
        elif cued or unloaded:
            badge += "  [$text-muted]restored from last session · space to play[/]"
        elif state.status is PlaybackStatus.ERROR and state.error:
            hint = " · space to retry" if state.retryable else ""
            badge += f"  [$error]{escape(state.error)}{hint}[/]"
        status.update(badge)

        timeline = self.query_one("#deck-timeline", Timeline)
        timeline.position = self._restored_position if unloaded else state.position
        timeline.duration = float(state.duration or (song.duration if song else 0) or 0)
        timeline.active = state.status is PlaybackStatus.PLAYING
        timeline.seekable = state.can_seek
        self._render_lyrics(song, timeline.position, timeline.duration)
        self._render_transport(state, song)
        self._render_header(state)

    def _render_transport(self, state: PlaybackState, song: Song | None) -> None:
        g = self.glyphs
        tx = self.query_one(Transport)
        playing = state.status in ACTIVE
        if self.unicode:
            tx.set_label(
                "play",
                f"{g.paused} PAUSE" if playing else f"{g.playing} PLAY",
                on=playing,
            )
            tx.set_label("shuffle", "⇄ SHUF", on=self.queue.shuffle)
            tx.set_label(
                "repeat",
                f"↻ {str(self.queue.repeat).upper()}",
                on=self.queue.repeat != "off",
            )
        else:
            tx.set_label("play", "|| PAUSE" if playing else "> PLAY", on=playing)
            tx.set_label("shuffle", "SHUF", on=self.queue.shuffle)
            tx.set_label(
                "repeat",
                f"REP {str(self.queue.repeat).upper()}",
                on=self.queue.repeat != "off",
            )
        liked = song is not None and song.id in self._favorites
        tx.set_label(
            "like",
            (f"{g.favorite} LIKED" if liked else "♡ LIKE")
            if self.unicode
            else ("LIKED" if liked else "LIKE"),
            on=liked,
        )
        saved = (
            song is not None
            and song.id in self._download_paths
            and song.id not in self._missing_downloads
        )
        job = self.downloads.jobs.get(song.id) if song else None
        if (
            job is not None
            and job.status is DownloadStatus.DOWNLOADING
            and job.fraction is not None
        ):
            label = f"{g.download} {job.fraction * 100:.0f}%"
        elif job is not None and job.status is DownloadStatus.QUEUED:
            label = f"{g.download} QUEUED"
        else:
            label = f"{g.download} SAVED" if saved else f"{g.download} SAVE"
        tx.set_label("download", label, on=saved)
        tx.set_enabled(
            "download", song is not None and eligible_for_download(song) is None
        )
        tx.set_enabled("like", song is not None)

    def _render_header(self, state: PlaybackState) -> None:
        header = self.query_one(BoothHeader)
        header.set_status(self._status_badge(state))
        if self.player.name == "simulated":
            engine = (
                "[$text-muted]ENGINE[/] simulated [$text-muted]· no audio output[/]"
            )
        else:
            source = "LOCAL FILE" if state.local else "STREAM"
            engine = (
                f"[$text-muted]ENGINE[/] {self.player.name} [$text-muted]·[/] {source} "
                "[$text-muted]· OUT[/] system default"
            )
        header.set_engine(engine)

    # ------------------------------------------------------- visualizer

    def _update_animation(self, state: PlaybackState) -> None:
        """Run the animation timer only while something actually moves."""
        loading = self.effects and state.status is PlaybackStatus.LOADING
        metering = self.visualizer and state.status is PlaybackStatus.PLAYING
        if (
            not metering
            and self.visualizer
            and state.status is not PlaybackStatus.LOADING
        ):
            self._decay_until = max(self._decay_until, time.monotonic() + DECAY_SECONDS)
        if loading or metering or time.monotonic() < self._decay_until:
            self._fx_timer.resume()
        else:
            self._fx_timer.pause()

    def _animate_frame(self) -> None:
        self._frame += 1
        state = self.playback
        if state.status is PlaybackStatus.LOADING and self.effects:
            self._render_playback(state)
        meter = self.query_one(StereoMeter)
        spectrum = self.query_one(Spectrum)
        if not self.visualizer:
            meter.feed(None, "off")
            spectrum.feed(None, "off")
        elif state.status is PlaybackStatus.PLAYING:
            if self._active_view() == "booth-view":
                level, bands = self.player.stereo_level(), self.player.spectrum()
                if self.player.name == "simulated":
                    mode = "sim"
                elif level is None:
                    # Real data arrives within ~0.5 s of audio starting.
                    mode = "wait" if state.position < 3 else "nodata"
                else:
                    mode = "live"
                meter.feed(level, mode)
                band_mode = mode if mode != "live" or bands is not None else "wait"
                spectrum.feed(bands, band_mode)
            for strip in self.query(LevelMeter):
                strip.levels = tuple(self.player.audio_levels(strip.capacity))
        else:
            meter.feed(None, "idle")
            spectrum.feed(None, "idle")
            if time.monotonic() >= self._decay_until:
                for strip in self.query(LevelMeter):
                    strip.levels = ()
                self._update_animation(state)

    def action_toggle_visualizer(self) -> None:
        if not self.effects:
            self.query_one("StatusBar").show(
                'Reduced effects are on; set effects to "full" in Settings (8) '
                "to use the meters."
            )
            return
        self.visualizer = not self.visualizer
        self._save_pref("visualizer", self.visualizer)
        if not self.visualizer:
            self.query_one(StereoMeter).feed(None, "off")
            self.query_one(Spectrum).feed(None, "off")
            for strip in self.query(LevelMeter):
                strip.levels = ()
        self._update_animation(self.playback)
        self.query_one("StatusBar").show(
            "Meters on." if self.visualizer else "Meters off."
        )
        self._refresh_settings()

    # ----------------------------------------------------------- lyrics

    def _render_lyrics(
        self, song: Song | None, position: float, duration: float
    ) -> None:
        view = self.query_one("#lyrics", LyricsView)
        song_id = song.id if song is not None else None
        if song_id != self._lyrics_song:
            self._lyrics_song = song_id
            view.show(None)
            view.border_title = "lyrics"
            if song is not None:
                self.load_lyrics(song)
            return
        view.follow(position + LYRICS_LEAD, duration)

    @work(exclusive=True, group="lyrics")
    async def load_lyrics(self, song: Song) -> None:
        view = self.query_one("#lyrics", LyricsView)
        view.border_title = "lyrics · searching"
        lyrics = await self.lyrics.lyrics_for(song)
        if self._lyrics_song != song.id:
            return  # a different track was loaded meanwhile
        unsynced = lyrics is not None and not lyrics.synced
        view.border_title = "lyrics · unsynced" if unsynced else "lyrics"
        view.show(lyrics)
        self._render_deck(self.playback)  # jump to the line being sung now

    # ------------------------------------------------------------ mixer

    def _sync_mixer(self) -> None:
        self.query_one("#fader-vol", Fader).value = self._volume
        self.query_one("#fader-bass", Fader).value = self._fx.bass
        self.query_one("#fader-treble", Fader).value = self._fx.treble
        self.query_one("#toggle-norm", Toggle).on = self._fx.normalize
        queued = len(self.queue.upcoming)
        flags = [f"{queued} up next"]
        if self.queue.repeat != "off":
            flags.append(f"repeat {self.queue.repeat}")
        if self.queue.shuffle:
            flags.append("shuffle")
        sep = f" {self.glyphs.separator} "
        self.query_one("#mixer-status").update(
            f"[$text-muted]QUEUE[/] {sep.join(flags)}\n"
            "[$text-muted]EQ/volume changes rebuffer briefly[/]"
        )
        self.query_one("#mixer-compact").update(
            f"[$text-muted]VOL[/] [b]{self._volume}[/]{sep}[$text-muted]BASS[/] "
            f"[b]{self._fx.bass:+d}[/]{sep}"
            f"[$text-muted]TREB[/] [b]{self._fx.treble:+d}[/]"
            f"{sep}[$text-muted]NORM[/] [b]{'on' if self._fx.normalize else 'off'}[/]"
            f"{sep}{queued} up next"
        )

    def _set_volume(self, value: int) -> None:
        self._volume = clamp_volume(value)
        self._control(self.player.set_volume(self._volume), "Volume")
        volume = self._volume
        self._save_pref("volume", volume)
        self._sync_mixer()

    def action_volume(self, direction: int) -> None:
        # Track the target locally so rapid key presses accumulate.
        self._set_volume(self._volume + direction * VOLUME_STEP)

    def _set_fx(self, **changes: object) -> None:
        self._fx = replace(self._fx, **changes).clamped()  # type: ignore[arg-type]
        self._control(self.player.set_fx(self._fx), "Effects")
        self._save_pref(
            "fx",
            {
                "bass": self._fx.bass,
                "treble": self._fx.treble,
                "normalize": self._fx.normalize,
            },
        )
        self._sync_mixer()

    def action_bass(self, direction: int) -> None:
        self._set_fx(bass=self._fx.bass + direction * FX_STEP)
        self.query_one("StatusBar").show(f"Bass {self._fx.bass:+d} dB.")

    def action_treble(self, direction: int) -> None:
        self._set_fx(treble=self._fx.treble + direction * FX_STEP)
        self.query_one("StatusBar").show(f"Treble {self._fx.treble:+d} dB.")

    def action_toggle_normalize(self) -> None:
        self._set_fx(normalize=not self._fx.normalize)
        self.query_one("StatusBar").show(
            "Loudness normalization on."
            if self._fx.normalize
            else "Loudness normalization off."
        )

    def _load_fx(self) -> AudioFx:
        raw = self._pref("fx", None)
        if isinstance(raw, dict):
            try:
                return AudioFx(
                    int(raw.get("bass", 0)),
                    int(raw.get("treble", 0)),
                    bool(raw.get("normalize")),
                ).clamped()
            except (TypeError, ValueError):
                pass
        return AudioFx()

    def _on_fader(self, fader_id: str, value: int) -> None:
        if fader_id == "fader-vol":
            if value != self._volume:
                self._set_volume(value)
        elif fader_id == "fader-bass":
            if value != self._fx.bass:
                self._set_fx(bass=value)
        elif fader_id == "fader-treble" and value != self._fx.treble:
            self._set_fx(treble=value)

    # --------------------------------------------------------- transport

    def _on_transport(self, button_id: str) -> None:
        actions = {
            "tx-prev": self.action_previous_track,
            "tx-play": self.action_toggle_playback,
            "tx-next": self.action_next_track,
            "tx-shuffle": self.action_toggle_shuffle,
            "tx-repeat": self.action_cycle_repeat,
            "tx-like": lambda: self.action_toggle_favorite(deck=True),
            "tx-download": lambda: self.action_download(deck=True),
        }
        if action := actions.get(button_id):
            action()

    def _on_timeline_seek(self, position: float) -> None:
        if not self.playback.can_seek:
            self.query_one("StatusBar").show(
                "Seeking is not available for this track (live or unknown length).",
                StatusLevel.INFO,
            )
            return
        self._seek_target = position
        self._control(self.player.seek(position), "Seek")
