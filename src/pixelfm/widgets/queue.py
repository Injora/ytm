"""Up-next queue panel."""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.markup import escape
from textual.widgets import Static

from pixelfm.models import Song


class QueuePanel(VerticalScroll):
    """Read-only list of queued songs."""

    can_focus = False

    def __init__(self) -> None:
        super().__init__(id="queue")
        self.border_title = "up next"

    def compose(self) -> ComposeResult:
        yield Static(id="queue-list")

    def on_mount(self) -> None:
        self.update_queue([])

    def update_queue(self, songs: Sequence[Song]) -> None:
        """Re-render the list from ``songs``."""
        self.border_subtitle = str(len(songs)) if songs else ""
        body = self.query_one("#queue-list", Static)
        if not songs:
            body.update(
                "[$text-muted]Nothing up next.\n\n"
                "Highlight a track and\npress [b $primary]a[/] to queue it,\n"
                "[b $primary]2[/] to manage the queue.[/]"
            )
            return
        lines = []
        for number, song in enumerate(songs, start=1):
            lines.append(f"[$text-muted]{number:02d}[/] [b]{escape(song.title)}[/]")
            lines.append(
                f"   [$secondary]{escape(song.artist)}[/] "
                f"[$text-muted]{song.duration_label}[/]"
            )
        body.update("\n".join(lines))
