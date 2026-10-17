"""Now-playing panel: cover, metadata, progress, and playback state."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from pixelfm.models import format_duration
from pixelfm.state import PlaybackState, PlaybackStatus
from pixelfm.styles import Glyphs
from pixelfm.widgets.album_art import AlbumArt


def progress_markup(
    position: int, duration: int, width: int, glyphs: Glyphs, active: bool
) -> str:
    """Render ``elapsed ━━━●─── total`` to fit exactly ``width`` cells."""
    elapsed = format_duration(position)
    total = format_duration(duration) if duration else "--:--"
    bar_width = max(0, width - len(elapsed) - len(total) - 2)
    if bar_width == 0:
        return f"{elapsed} {total}"
    ratio = min(1.0, position / duration) if duration else 0.0
    filled = min(bar_width - 1, int(ratio * bar_width))
    empty = bar_width - filled - 1
    colour = "$primary" if active else "$text-muted"
    return (
        f"[$text-muted]{elapsed}[/] "
        f"[{colour}]{glyphs.bar_fill * filled}{glyphs.bar_head}[/]"
        f"[$border-blurred]{glyphs.bar_empty * empty}[/] "
        f"[$text-muted]{total}[/]"
    )


class ProgressLine(Widget):
    """Width-aware progress bar that redraws on resize."""

    position: reactive[int] = reactive(0)
    duration: reactive[int] = reactive(0)
    active: reactive[bool] = reactive(False)

    def __init__(self, glyphs: Glyphs, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._glyphs = glyphs

    def render(self) -> str:
        return progress_markup(
            self.position,
            self.duration,
            self.content_size.width,
            self._glyphs,
            self.active,
        )


#: Display range for RMS levels. Mastered music mostly sits between about
#: -20 and -8 dBFS RMS, so this range keeps the meter lively without clipping.
METER_FLOOR_DB = -36.0
METER_CEILING_DB = -6.0
METER_MAX_WIDTH = 48


def meter_markup(levels: list[float] | tuple[float, ...], width: int, ramp: str) -> str:
    """A scrolling history of real RMS levels, newest on the right."""
    available = min(width - 4, METER_MAX_WIDTH)
    if not levels or available <= 0:
        return ""
    steps = len(ramp) - 1
    span = METER_CEILING_DB - METER_FLOOR_DB
    chars = []
    for db in levels[-available:]:
        clamped = max(METER_FLOOR_DB, min(METER_CEILING_DB, db))
        chars.append(ramp[round((clamped - METER_FLOOR_DB) / span * steps)])
    return f"[$text-muted]lvl[/] [$primary]{escape(''.join(chars))}[/]"


class LevelMeter(Widget):
    """Audio level meter fed with measured levels; blank without data."""

    levels: reactive[tuple[float, ...]] = reactive(tuple)

    def __init__(self, ramp: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._ramp = ramp

    @property
    def capacity(self) -> int:
        return max(0, min(self.content_size.width - 4, METER_MAX_WIDTH))

    def render(self) -> str:
        return meter_markup(self.levels, self.content_size.width, self._ramp)


class NowPlaying(Vertical):
    """Displays the engine's :class:`PlaybackState`."""

    def __init__(self, glyphs: Glyphs, art_size: tuple[int, int] = (16, 8)) -> None:
        super().__init__(id="now-playing")
        self._glyphs = glyphs
        self._art_size = art_size
        self.border_title = "now playing"

    def compose(self) -> ComposeResult:
        with Horizontal(id="np-row"):
            yield AlbumArt(*self._art_size, id="art")
            with Vertical(id="np-meta"):
                yield Static(id="np-title")
                yield Static(id="np-artist")
                yield LevelMeter(self._glyphs.meter, id="np-viz")
                yield ProgressLine(self._glyphs, id="np-progress")
                yield Static(id="np-state")

    def update_state(
        self,
        state: PlaybackState,
        queue_hint: str = "",
        simulated: bool = False,
        flags: str = "",
        loading_glyph: str | None = None,
    ) -> None:
        """Refresh every child from an engine snapshot. Cheap; called ~4x/s."""
        song = state.song
        g = self._glyphs
        progress = self.query_one(ProgressLine)
        title = self.query_one("#np-title", Static)
        artist = self.query_one("#np-artist", Static)

        if song is None:
            title.update("[b $text-muted]Nothing playing[/]")
            artist.update("[$text-muted]Pick a track and press [b]enter[/][/]")
        else:
            title.update(f"[b $primary]{escape(song.title)}[/]")
            artist.update(f"[$secondary]{escape(song.artist)}[/]")
        progress.position = int(state.position)
        progress.duration = int(state.duration or 0)
        progress.active = state.status is PlaybackStatus.PLAYING

        detail = (state.detail or "buffering").upper()
        labels = {
            PlaybackStatus.IDLE: f"[b $text-muted]{g.stopped} IDLE[/]",
            PlaybackStatus.LOADING: (
                f"[b $accent]{loading_glyph or g.loading} {escape(detail)}[/]"
            ),
            PlaybackStatus.PLAYING: f"[b $success]{g.playing} PLAYING[/]",
            PlaybackStatus.PAUSED: f"[b $warning]{g.paused} PAUSED[/]",
            PlaybackStatus.STOPPED: f"[b $text-muted]{g.stopped} STOPPED[/]",
            PlaybackStatus.ENDED: f"[b $text-muted]{g.stopped} ENDED[/]",
            PlaybackStatus.ERROR: f"[b $error]{g.error} ERROR[/]",
        }
        parts = [labels[state.status], f"[$text-muted]vol {state.volume}%[/]"]
        if flags:
            parts.append(flags)
        if simulated:
            parts.append("[$text-muted]simulated[/]")
        if queue_hint:
            parts.append(f"[$text-muted]{escape(queue_hint)}[/]")
        self.query_one("#np-state", Static).update(
            f" [$border-blurred]{g.separator}[/] ".join(parts)
        )
