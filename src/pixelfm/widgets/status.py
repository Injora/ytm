"""One-line status bar for loading, errors, and general feedback."""

from __future__ import annotations

from enum import StrEnum

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.markup import escape
from textual.timer import Timer
from textual.widgets import Static

from pixelfm.styles import Glyphs


class StatusLevel(StrEnum):
    INFO = "info"
    LOADING = "loading"
    SUCCESS = "success"
    ERROR = "error"


_COLOURS = {
    StatusLevel.INFO: "$text-muted",
    StatusLevel.LOADING: "$accent",
    StatusLevel.SUCCESS: "$success",
    StatusLevel.ERROR: "$error",
}


class StatusBar(Horizontal):
    """Message on the left, summary counters on the right."""

    def __init__(self, glyphs: Glyphs, animate: bool = True) -> None:
        super().__init__(id="status")
        self._glyphs = glyphs
        self.animate = animate
        self._spinner: Timer | None = None
        self._frame = 0
        self.level = StatusLevel.INFO
        self.message = ""

    def compose(self) -> ComposeResult:
        yield Static(id="status-message")
        yield Static(id="status-meta")

    def show(self, message: str, level: StatusLevel = StatusLevel.INFO) -> None:
        """Display ``message``; LOADING animates a spinner until replaced."""
        self.level, self.message = level, message
        if level is StatusLevel.LOADING and self.animate:
            if self._spinner is None:
                self._spinner = self.set_interval(0.1, self._advance_spinner)
        elif self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        self._render_message()

    def set_meta(self, text: str) -> None:
        """Set the right-hand summary text."""
        self.query_one("#status-meta", Static).update(f"[$text-muted]{escape(text)}[/]")

    def _advance_spinner(self) -> None:
        self._frame = (self._frame + 1) % len(self._glyphs.spinner)
        self._render_message()

    def _render_message(self) -> None:
        colour = _COLOURS[self.level]
        if self.level is StatusLevel.LOADING:
            prefix = (
                self._glyphs.spinner[self._frame]
                if self.animate
                else self._glyphs.loading
            )
        elif self.level is StatusLevel.ERROR:
            prefix = "!"
        else:
            prefix = ">"
        self.query_one("#status-message", Static).update(
            f"[b {colour}]{prefix}[/] [{colour}]{escape(self.message)}[/]"
        )
