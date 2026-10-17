"""Music-derived theme and Party Mode lighting.

Theme changes go through Textual's theme system, which restyles every
widget (~25 ms plus a repaint here), so they are rare: a short crossfade when
the track changes, and in Party Mode at most one step every
:data:`~pixelfm.services.party.THEME_STEP_SECONDS`. Party lighting itself
animates only a handful of booth widgets (panel borders, the light bar, the
timeline, the meters), at most :data:`~pixelfm.services.party.MAX_FPS` times
a second.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

from textual import work
from textual.color import Color
from textual.css.query import NoMatches

from pixelfm.services.artwork import ArtworkMode
from pixelfm.services.palette import (
    DEFAULT_PALETTE,
    Palette,
    blend,
    palette_from_theme,
    theme_from_palette,
    to_hex,
    with_accents,
)
from pixelfm.services.party import PartyFrame, PartyLights, PartyMode
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import BoothHeader, LightBar, Spectrum, StereoMeter, Timeline

if TYPE_CHECKING:
    from pixelfm.models import Song

#: The "Theme" setting value that follows the current track's artwork.
MUSIC = "music"
#: Name of the Textual theme that the music palette is registered under.
DYNAMIC_THEME = "pixelfm-music"
TRANSITION_STEPS = 6
TRANSITION_INTERVAL = 0.15
#: Booth panels whose borders carry Party Mode lighting, left to right.
PARTY_PANELS = ("#deck", "#signal", "#mixer", "#booth-queue-panel")
PARTY_STYLES = tuple(m for m in PartyMode if m is not PartyMode.OFF)


class ThemingMixin:
    """Mixed into :class:`~pixelfm.app.PixelFMApp`."""

    # -------------------------------------------------------- theme choice

    def _setup_themes(self, choice: str) -> None:
        self._paint(DEFAULT_PALETTE)
        self._apply_theme_choice(choice)

    def _apply_theme_choice(self, choice: str) -> bool:
        """Switch the Theme setting. Returns ``False`` for an unknown theme."""
        if choice == MUSIC:
            self._theme_choice = MUSIC
            self.theme = DYNAMIC_THEME
            self._refresh_palette()
            return True
        if choice in self.available_themes and choice != DYNAMIC_THEME:
            self._theme_choice = choice
            self._stop_palette_transition()
            if not self._party_running():
                self.theme = choice
            return True
        return False

    def _music_theme(self) -> bool:
        return self._theme_choice == MUSIC and not self.no_color

    def _base_palette(self) -> Palette:
        """What the UI shows with Party Mode off."""
        if self._theme_choice == MUSIC:
            return self._palette_target
        theme = self.get_theme(self._theme_choice)
        try:
            return palette_from_theme(theme) if theme is not None else DEFAULT_PALETTE
        except (KeyError, AttributeError):  # third-party theme without our roles
            return DEFAULT_PALETTE

    # ------------------------------------------------------ music palette

    def _refresh_palette(self, song: Song | None = None) -> None:
        """Follow the newest track (called whenever its artwork loads)."""
        if self._music_theme():
            self.load_palette(song if song is not None else self._newest_song())

    @work(exclusive=True, group="palette")
    async def load_palette(self, song: Song | None) -> None:
        """Extract (or fetch from cache) the palette off the UI thread."""
        palette = await self.palettes.palette_for(song)
        if song != self._newest_song() or not self._music_theme():
            return  # superseded by a newer track, or the theme changed
        if palette == self._palette_target:
            return
        self._palette_target = palette
        if not self._party_running():
            self._transition_to(palette)

    def _paint(self, palette: Palette) -> None:
        """Apply ``palette`` to the whole UI immediately."""
        self._palette_now = palette
        self.register_theme(theme_from_palette(palette, DYNAMIC_THEME))
        if self.theme == DYNAMIC_THEME:
            self.refresh_css(animate=False)

    def _stop_palette_transition(self) -> None:
        if self._palette_timer is not None:
            self._palette_timer.stop()
            self._palette_timer = None

    def _transition_to(self, palette: Palette) -> None:
        """Crossfade to ``palette`` (instantly with reduced effects)."""
        self._stop_palette_transition()
        start = self._palette_now
        if start == palette:
            return
        if not self.effects or self.no_color:
            self._paint(palette)
            self._after_palette()
            return
        step = 0

        def tick() -> None:
            nonlocal step
            step += 1
            t = step / TRANSITION_STEPS
            eased = t * t * (3 - 2 * t)  # smoothstep
            self._paint(blend(start, palette, eased))
            if step >= TRANSITION_STEPS:
                self._stop_palette_transition()
                self._after_palette()

        self._palette_timer = self.set_interval(
            TRANSITION_INTERVAL, tick, repeat=TRANSITION_STEPS
        )

    def _after_palette(self) -> None:
        # Mono and glyph artwork are tinted with the accent: re-render them.
        if self.artwork_mode is not ArtworkMode.COLOR:
            self.load_artwork(self._newest_song())

    # ---------------------------------------------------------- party mode

    def _party_allowed(self) -> str | None:
        """Why Party Mode can't run, or ``None``."""
        if self.no_color:
            return "Party Mode is off while NO_COLOR is set."
        if not self.effects:
            return (
                'Party Mode needs effects; set Effects to "full" in Settings (8). '
                "Reduced effects keep the screen still."
            )
        return None

    def _party_running(self) -> bool:
        return self._party is not None

    def action_toggle_party(self) -> None:
        """``P`` / the header button: party on (last style) or off, instantly."""
        if self._party_running():
            self._set_party(False)
            self._status("Party Mode off.")
            return
        if reason := self._party_allowed():
            self._status(reason)
            return
        self._set_party(True)
        self._status(
            f"Party Mode on: {self._party_style}. P turns it off; "
            "change the style in Settings (8)."
        )

    def _set_party(self, on: bool, *, save: bool = True) -> None:
        if save:  # unsaved switches (e.g. reduced effects) keep the preference
            self._save_pref("party", on)
            self._party_wanted = on
        self._stop_party()
        if on and self._party_allowed() is None:
            lights = PartyLights(
                self._party_style,
                panels=len(PARTY_PANELS),
                base=self._palette_now.background,
            )
            self._party = lights
            self._party_t0 = time.monotonic()
            self._party_accents = None
            self._party_timer = self.set_interval(1 / lights.fps, self._party_tick)
            self.screen.add_class("-party")
            if self.theme != DYNAMIC_THEME:
                self._paint(self._base_palette())
                self.theme = DYNAMIC_THEME
            self._party_tick()
        else:
            self._restore_after_party()
        self._render_party_indicator()
        self._refresh_settings()

    def _stop_party(self) -> None:
        if self._party_timer is not None:
            self._party_timer.stop()
            self._party_timer = None
        self._party = None

    def _restore_after_party(self) -> None:
        """Back to the normal theme, with every lighting override removed."""
        self.screen.remove_class("-party")
        with contextlib.suppress(NoMatches):
            for selector in (*PARTY_PANELS, "#now-playing"):
                widget = self.query_one(selector)
                for rule in (
                    "border_top",
                    "border_right",
                    "border_bottom",
                    "border_left",
                    "border_title_color",
                ):
                    widget.styles.clear_rule(rule)
                widget.refresh()
            self.query_one(Timeline).accent = None
            self.query_one(StereoMeter).accent = None
            self.query_one(Spectrum).accent = None
            self.query_one(LightBar).colours = ()
        if self._theme_choice == MUSIC:
            self._transition_to(self._palette_target)
        else:
            self._stop_palette_transition()
            self.theme = self._theme_choice
            self._palette_now = self._base_palette()

    def _party_tick(self) -> None:
        lights = self._party
        if lights is None:
            return
        state = self.playback
        level = (
            self.player.stereo_level()
            if state.status is PlaybackStatus.PLAYING
            else None
        )
        frame = lights.frame(
            time.monotonic() - self._party_t0, level.loudness if level else None
        )
        with contextlib.suppress(NoMatches):  # ticks can race shutdown
            self._apply_party_frame(frame)

    def _apply_party_frame(self, frame: PartyFrame) -> None:
        ascii_border = "ascii" if not self.unicode else "solid"
        if self._active_view() == "booth-view":
            for selector, colour in zip(PARTY_PANELS, frame.panels, strict=True):
                widget = self.query_one(selector)
                c = Color(*colour)
                widget.styles.border = (ascii_border, c)
                widget.styles.border_title_color = c
            self.query_one(LightBar).colours = tuple(to_hex(c) for c in frame.bar)
            self.query_one(Timeline).accent = to_hex(frame.accent)
            accents = (to_hex(frame.accent), to_hex(frame.accent2))
            self.query_one(StereoMeter).accent = accents
            self.query_one(Spectrum).accent = accents
        else:
            c = Color(*frame.panels[0])
            now_playing = self.query_one("#now-playing")
            now_playing.styles.border = (ascii_border, c)
            now_playing.styles.border_title_color = c
        if frame.theme_colours != self._party_accents:
            # Whole-UI accents: rate-limited by the engine (THEME_STEP_SECONDS).
            self._party_accents = frame.theme_colours
            self._paint(with_accents(self._base_palette(), *frame.theme_colours))
        self._render_party_indicator(frame)

    def _render_party_indicator(self, frame: PartyFrame | None = None) -> None:
        header = self.query_one(BoothHeader)
        if not self._party_running():
            header.set_party("PARTY MODE: OFF", on=False)
            return
        style = str(self._party_style).upper()
        note = ""
        if (
            frame is not None
            and self._party_style is not PartyMode.AMBIENT
            and not frame.reactive
        ):
            note = " · NO SIGNAL"  # nothing measured: lights stay calm
        header.set_party(f"PARTY MODE: ON · {style}{note}", on=True)
