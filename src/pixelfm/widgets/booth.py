"""DJ booth widgets: header, deck timeline, transport, meters, spectrum, mixer.

Meter widgets only ever display measurements handed to them. Smoothing
(fall-off and peak hold) is a display convention applied to real values; when
no measurement exists they show an explicit idle state instead of motion.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.markup import escape
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Static

from pixelfm import __version__
from pixelfm.models import format_duration
from pixelfm.services.metering import BAND_LABELS, SILENCE_DB, StereoLevel
from pixelfm.styles import Glyphs
from pixelfm.widgets.brand import logo_markup

METER_FLOOR = -48.0
SPECTRUM_FLOOR = -60.0
SPECTRUM_CEILING = -6.0
FALL_DB_PER_S = 24.0  # display decay of a level that just dropped
PEAK_HOLD_S = 1.2


def _fraction(db: float, floor: float, ceiling: float = 0.0) -> float:
    return max(0.0, min(1.0, (db - floor) / (ceiling - floor)))


@dataclass
class _Smoothed:
    """Fall-off and peak-hold for one measured value (display only)."""

    value: float = SILENCE_DB
    peak: float = SILENCE_DB
    peak_at: float = 0.0

    def update(
        self, measured: float | None, peak: float | None, now: float, dt: float
    ) -> None:
        target = SILENCE_DB if measured is None else measured
        self.value = max(target, self.value - FALL_DB_PER_S * dt)
        candidate = target if peak is None else peak
        if candidate >= self.peak or now - self.peak_at > PEAK_HOLD_S:
            if candidate >= self.peak:
                self.peak, self.peak_at = candidate, now
            else:
                self.peak = max(candidate, self.peak - FALL_DB_PER_S * dt)


# ------------------------------------------------------------------ header


class BoothHeader(Horizontal):
    """Brand, playback status, engine, current view, and key hints."""

    def __init__(self, engine: str, unicode: bool = True) -> None:
        super().__init__(id="header")
        self._engine = engine
        self._unicode = unicode

    def compose(self) -> ComposeResult:
        mark = "▞▚" if self._unicode else "//"
        if self._unicode:
            yield Static(logo_markup(), id="logo")
        yield Static(
            f"[b $secondary]{mark}[/] [b $primary]PIXEL[/][b $secondary]FM[/] "
            f"[$text-muted]{'.'.join(__version__.split('.')[:2])}[/]",
            id="header-brand",
        )
        yield Static(id="header-status")
        yield Static(id="header-engine")
        yield Button(
            "PARTY MODE: OFF", id="party-toggle", compact=True, classes="party"
        )
        yield Static(id="header-hints")

    def set_status(self, markup: str) -> None:
        self.query_one("#header-status", Static).update(markup)

    def set_engine(self, markup: str) -> None:
        self.query_one("#header-engine", Static).update(markup)

    def set_logo_colours(self, colours: Sequence[str] | None) -> None:
        """Run RAVE lights through the logo (``None``: the theme colours)."""
        if not self._unicode:
            return
        colours = tuple(colours) if colours else None
        if colours == getattr(self, "_logo_colours", None):
            return
        self._logo_colours = colours
        # Same glyphs, new colours: the size can't change, so skip the layout
        # pass (a full one costs ~20 ms; RAVE recolours the logo every frame).
        self.query_one("#logo", Static).update(logo_markup(colours), layout=False)

    def set_party(self, label: str, *, on: bool) -> None:
        button = self.query_one("#party-toggle", Button)
        if str(button.label) != label:
            button.label = label
        button.set_class(on, "-on")

    def set_view(self, view_label: str) -> None:
        self.query_one("#header-hints", Static).update(
            f"[$text-muted]VIEW[/] [b]{escape(view_label)}[/]  "
            "[$text-muted]1-8 views · ? keys[/]"
        )


# ---------------------------------------------------------------- timeline


class Timeline(Widget, can_focus=True):
    """Elapsed, a seekable bar, and remaining time. Click or ←/→ to seek."""

    BINDINGS = [
        Binding("left,h", "nudge(-5)", "-5s", show=False),
        Binding("right,l", "nudge(5)", "+5s", show=False),
    ]

    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)
    active: reactive[bool] = reactive(False)
    seekable: reactive[bool] = reactive(False)
    accent: reactive[str | None] = reactive(None)  # Party Mode light colour
    gradient: reactive[tuple[str, ...]] = reactive(tuple)  # RAVE: colour per cell

    class Seek(Message):
        def __init__(self, position: float) -> None:
            super().__init__()
            self.position = position

    def __init__(self, glyphs: Glyphs, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._glyphs = glyphs

    def _labels(self) -> tuple[str, str]:
        elapsed = format_duration(int(self.position))
        if not self.duration:
            return elapsed, "--:--"
        remaining = max(0, int(self.duration) - int(self.position))
        return elapsed, f"-{format_duration(remaining)}"

    def _bar_span(self) -> tuple[int, int]:
        left, right = self._labels()
        start = len(left) + 1
        return start, max(0, self.content_size.width - start - len(right) - 1)

    def render(self) -> str:
        g = self._glyphs
        left, right = self._labels()
        _start, width = self._bar_span()
        if width <= 0:
            return f"{left} {right}"
        ratio = min(1.0, self.position / self.duration) if self.duration else 0.0
        filled = min(width - 1, int(ratio * width))
        colour = (self.accent or "$primary") if self.active else "$text-muted"
        head = g.bar_head if self.seekable or self.duration else g.bar_empty
        gradient = self.gradient
        if gradient and self.active and filled:
            # RAVE: the filled part runs through the light rig's colours.
            fill = "".join(
                f"[{gradient[i * len(gradient) // filled]}]{g.bar_fill}[/]"
                for i in range(filled)
            )
            bar = f"{fill}[{gradient[-1]}]{head}[/]"
        else:
            bar = f"[{colour}]{g.bar_fill * filled}{head}[/]"
        return (
            f"[b]{left}[/] {bar}"
            f"[$border-blurred]{g.bar_empty * (width - filled - 1)}[/] "
            f"[$text-muted]{right}[/]"
        )

    def on_click(self, event: events.Click) -> None:
        start, width = self._bar_span()
        if not self.seekable or width <= 0 or not self.duration:
            return
        offset = event.x - start
        if 0 <= offset < width:
            self.post_message(self.Seek(self.duration * offset / max(1, width - 1)))

    def action_nudge(self, seconds: int) -> None:
        if self.seekable and self.duration:
            target = max(0.0, min(self.duration - 1, self.position + seconds))
            self.post_message(self.Seek(target))


# --------------------------------------------------------------- transport


class Transport(Horizontal):
    """Real playback buttons. Each one triggers the same action as its key."""

    BUTTONS: tuple[tuple[str, str, str], ...] = (
        ("prev", "|◀ PREV", "|< PREV"),
        ("play", "▶ PLAY", "> PLAY"),
        ("next", "NEXT ▶|", "NEXT >|"),
        ("shuffle", "⇄ SHUF", "SHUF"),
        ("repeat", "↻ OFF", "REP OFF"),
        ("like", "♡ LIKE", "LIKE"),
        ("download", "↓ SAVE", "SAVE"),
    )

    def __init__(self, unicode: bool = True) -> None:
        super().__init__(id="transport")
        self._unicode = unicode

    def compose(self) -> ComposeResult:
        for key, fancy, plain in self.BUTTONS:
            yield Button(
                fancy if self._unicode else plain,
                id=f"tx-{key}",
                compact=True,
                classes="transport",
            )

    def set_label(self, key: str, label: str, *, on: bool | None = None) -> None:
        button = self.query_one(f"#tx-{key}", Button)
        if str(button.label) != label:
            button.label = label
        if on is not None:
            button.set_class(on, "-on")

    def set_enabled(self, key: str, enabled: bool) -> None:
        self.query_one(f"#tx-{key}", Button).disabled = not enabled


# ------------------------------------------------------------------ meters


#: Explicit non-live states: the meters never animate without measurements.
STATE_NOTES = {
    "idle": "NO SIGNAL",
    "off": "METERS OFF · V or Settings (8) to enable",
    "sim": "SIMULATED ENGINE · NO AUDIO TO MEASURE",
    "wait": "WAITING FOR MEASUREMENTS",
    "nodata": "NO MEASUREMENTS FROM THIS PLAYER (see log)",
}


class StereoMeter(Widget):
    """Two horizontal L/R bars with peak hold, from measured RMS/peak levels."""

    def __init__(self, glyphs: Glyphs, unicode: bool = True, *, id: str | None = None):
        super().__init__(id=id)
        self._glyphs = glyphs
        self._unicode = unicode
        self._left = _Smoothed()
        self._right = _Smoothed()
        self._last = time.monotonic()
        self.state = "idle"  # see STATE_NOTES
        self.accent: tuple[str, str] | None = None  # Party Mode colours

    def feed(self, level: StereoLevel | None, state: str = "live") -> None:
        now = time.monotonic()
        dt, self._last = min(0.5, now - self._last), now
        self.state = state
        if level is None:
            self._left.update(None, None, now, dt)
            self._right.update(None, None, now, dt)
        else:
            self._left.update(level.rms_l, level.peak_l, now, dt)
            self._right.update(level.rms_r, level.peak_r, now, dt)
        self.refresh()

    def _bar(self, label: str, channel: _Smoothed, width: int) -> str:
        fill, empty = ("▮", "·") if self._unicode else ("#", ".")
        lit = round(_fraction(channel.value, METER_FLOOR) * width)
        peak = min(width - 1, round(_fraction(channel.peak, METER_FLOOR) * (width - 1)))
        hot = round(_fraction(-6.0, METER_FLOOR) * width)  # top 6 dB in magenta
        primary, secondary = self.accent or ("$primary", "$secondary")
        cells = []
        for i in range(width):
            if i < lit:
                cells.append(
                    f"[{secondary}]{fill}[/]" if i >= hot else f"[{primary}]{fill}[/]"
                )
            elif i == peak and channel.peak > METER_FLOOR:
                cells.append(f"[b {secondary}]|[/]")
            else:
                cells.append(f"[$border-blurred]{empty}[/]")
        value = "  -∞" if channel.value <= METER_FLOOR else f"{channel.value:5.1f}"
        return f"[$text-muted]{label}[/] {''.join(cells)} [$text-muted]{value} dB[/]"

    def render(self) -> str:
        width = max(4, self.content_size.width - 12)
        quiet = max(self._left.value, self._right.value) <= METER_FLOOR
        if self.state != "live" or quiet:
            empty = "·" if self._unicode else "."
            blank = f"[$border-blurred]{empty * width}[/]"
            note = STATE_NOTES.get(self.state, "SILENCE")
            colour = "$warning" if note == "SILENCE" else "$text-muted"
            return (
                f"[$text-muted]L[/] {blank}\n[$text-muted]R[/] {blank}\n"
                f"[{colour}]{note}[/]"
            )
        left = self._bar("L", self._left, width)
        right = self._bar("R", self._right, width)
        note = "[$text-muted]RMS · PEAK HOLD · PRE-FADER[/]"
        return f"{left}\n{right}\n{note}"


class Spectrum(Widget):
    """Vertical bars of measured band levels (7 band-pass filters)."""

    def __init__(self, glyphs: Glyphs, unicode: bool = True, *, id: str | None = None):
        super().__init__(id=id)
        self._glyphs = glyphs
        self._unicode = unicode
        self._bands = [_Smoothed() for _ in BAND_LABELS]
        self._last = time.monotonic()
        self.state = "idle"
        self.accent: tuple[str, str] | None = None  # Party Mode colours

    def feed(self, bands: Sequence[float] | None, state: str = "live") -> None:
        now = time.monotonic()
        dt, self._last = min(0.5, now - self._last), now
        self.state = state
        for i, band in enumerate(self._bands):
            band.update(None if bands is None else bands[i], None, now, dt)
        self.refresh()

    def render(self) -> str:
        width, height = self.content_size.width, self.content_size.height
        rows = max(1, height - 1)
        count = len(self._bands)
        slot = max(2, width // count)
        bar = max(1, slot - 1)
        ramp = " ▁▂▃▄▅▆▇█" if self._unicode else " .:-=+*#@"
        idle = all(b.value <= SPECTRUM_FLOOR for b in self._bands)
        primary, secondary = self.accent or ("$primary", "$secondary")
        lines = []
        for row in range(rows):
            level_from_top = rows - row  # rows..1
            cells = []
            for band in self._bands:
                height_cells = (
                    _fraction(band.value, SPECTRUM_FLOOR, SPECTRUM_CEILING) * rows
                )
                peak_row = round(
                    _fraction(band.peak, SPECTRUM_FLOOR, SPECTRUM_CEILING) * rows
                )
                full = int(height_cells)
                if level_from_top <= full:
                    char = ramp[-1]
                elif level_from_top == full + 1:
                    char = ramp[int((height_cells - full) * (len(ramp) - 1))]
                else:
                    char = " "
                colour = secondary if level_from_top > rows * 0.75 else primary
                if (
                    char == " "
                    and level_from_top == peak_row
                    and band.peak > SPECTRUM_FLOOR
                ):
                    cells.append(
                        f"[{secondary}]{'▔' if self._unicode else '-'}[/]" * bar
                    )
                elif char == " ":
                    cells.append(" " * bar)
                else:
                    dim = " dim" if row % 2 else ""  # faint scanlines
                    cells.append(f"[{colour}{dim}]{char * bar}[/]")
                cells.append(" " * (slot - bar))
            lines.append("".join(cells))
        labels = "".join(f"{label:<{slot}}"[:slot] for label in BAND_LABELS)
        if idle or self.state != "live":
            note = STATE_NOTES.get(self.state, "SILENCE")
            lines[rows // 2] = f"[$text-muted]{note:^{slot * count}}[/]"
        lines.append(f"[$text-muted]{labels}[/]")
        return "\n".join(lines)


# --------------------------------------------------------------- lighting


class LightBar(Widget):
    """Party Mode light strip across the deck. Hidden unless the party is on;
    it only shows colours it is given and has no state of its own."""

    colours: reactive[tuple[str, ...]] = reactive(tuple)

    def __init__(self, unicode: bool = True, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._char = "▀" if unicode else "="

    def render(self) -> str:
        colours = self.colours
        width = self.content_size.width
        if not colours or width <= 0:
            return ""
        cells = []
        for i in range(width):
            colour = colours[min(len(colours) - 1, i * len(colours) // width)]
            cells.append(f"[{colour}]{self._char}[/]")
        return "".join(cells)


# ------------------------------------------------------------------- mixer


class Fader(Widget, can_focus=True):
    """A focusable horizontal fader. ←/→ (or h/l) adjust; click to set."""

    BINDINGS = [
        Binding("left,h", "step(-1)", "Down", show=False),
        Binding("right,l", "step(1)", "Up", show=False),
    ]

    value: reactive[int] = reactive(0)

    class Changed(Message):
        def __init__(self, fader: Fader, value: int) -> None:
            super().__init__()
            self.fader = fader
            self.value = value

        @property
        def control(self) -> Fader:
            return self.fader

    def __init__(
        self,
        label: str,
        low: int,
        high: int,
        step: int,
        unit: str = "",
        *,
        id: str | None = None,
        unicode: bool = True,
    ) -> None:
        super().__init__(id=id)
        self.label, self.low, self.high, self.step, self.unit = (
            label,
            low,
            high,
            step,
            unit,
        )
        self._unicode = unicode

    def _track(self) -> tuple[int, int]:
        start = 6
        return start, max(4, self.content_size.width - start - 7)

    def render(self) -> str:
        _start, width = self._track()
        ratio = (self.value - self.low) / (self.high - self.low)
        knob = round(ratio * (width - 1))
        centre = (
            round((0 - self.low) / (self.high - self.low) * (width - 1))
            if self.low < 0
            else -1
        )
        line, knob_char = ("─", "●") if self._unicode else ("-", "O")
        cells = []
        for i in range(width):
            if i == knob:
                cells.append(f"[b $primary]{knob_char}[/]")
            elif (self.low < 0 and min(centre, knob) <= i <= max(centre, knob)) or (
                self.low >= 0 and i < knob
            ):
                cells.append(f"[$primary]{line}[/]")
            elif i == centre:
                cells.append(
                    "[$text-muted]┊[/]" if self._unicode else "[$text-muted]:[/]"
                )
            else:
                cells.append(f"[$border-blurred]{line}[/]")
        sign = "+" if self.low < 0 and self.value > 0 else ""
        value = f"{sign}{self.value}{self.unit}"
        return f"[b $text-muted]{self.label:<5}[/] {''.join(cells)} [b]{value:>5}[/]"

    def _set(self, value: int) -> None:
        value = max(self.low, min(self.high, value))
        if value != self.value:
            self.value = value
            self.post_message(self.Changed(self, value))

    def action_step(self, direction: int) -> None:
        self._set(self.value + direction * self.step)

    def on_click(self, event: events.Click) -> None:
        start, width = self._track()
        offset = event.x - start
        if 0 <= offset < width:
            raw = self.low + (self.high - self.low) * offset / max(1, width - 1)
            self._set(round(raw / self.step) * self.step)


class Toggle(Widget, can_focus=True):
    """A focusable on/off switch (enter/space/click)."""

    BINDINGS = [Binding("enter,space", "flip", "Toggle", show=False)]

    on: reactive[bool] = reactive(False)

    class Changed(Message):
        def __init__(self, toggle: Toggle, on: bool) -> None:
            super().__init__()
            self.toggle = toggle
            self.on = on

        @property
        def control(self) -> Toggle:
            return self.toggle

    def __init__(self, label: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.label = label

    def render(self) -> str:
        state = "[b reverse $secondary] ON [/]" if self.on else "[$text-muted] OFF [/]"
        return f"[b $text-muted]{self.label:<5}[/] {state}"

    def action_flip(self) -> None:
        self.on = not self.on
        self.post_message(self.Changed(self, self.on))

    def on_click(self) -> None:
        self.action_flip()
