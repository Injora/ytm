"""Widget that displays pre-rendered terminal artwork."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

from pixelfm.services.artwork import TerminalArt


class AlbumArt(Widget):
    """Fixed-size box showing a :class:`TerminalArt`.

    Rendering happens in the artwork service; this widget only displays the
    cached result, so its render callback stays trivial.
    """

    def __init__(self, width: int, height: int, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.art_width = width
        self.art_height = height
        self.art: TerminalArt | None = None
        self._text = Text()

    def on_mount(self) -> None:
        self.styles.width = self.art_width
        self.styles.height = self.art_height

    def set_size(self, width: int, height: int) -> bool:
        """Resize the box. Returns True if the size changed (re-render needed)."""
        if (width, height) == (self.art_width, self.art_height):
            return False
        self.art_width, self.art_height = width, height
        self.styles.width, self.styles.height = width, height
        return True

    def show(self, art: TerminalArt) -> None:
        """Display ``art`` (already rendered at this widget's size)."""
        self.art = art
        self._text = art.to_text()
        self.refresh()

    def render(self) -> Text:
        return self._text
