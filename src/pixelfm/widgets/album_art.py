"""Widget that displays the cover: real image or pre-rendered terminal art."""

from __future__ import annotations

import contextlib
from typing import Any

from PIL import Image
from rich.text import Text
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.widget import Widget

from pixelfm.services.artwork import TerminalArt


class _ArtText(Widget):
    """The character-cell rendering (quadrant blocks, mono, or glyphs)."""

    def __init__(self) -> None:
        super().__init__(classes="cover-text")
        self.text = Text()

    def render(self) -> Text:
        return self.text


class AlbumArt(Widget):
    """Fixed-size box showing a :class:`TerminalArt`, or the actual picture
    when the terminal supports an image protocol (``image_widget``).

    Rendering happens in the artwork service; this widget only displays the
    cached result, so its render callbacks stay trivial.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        id: str | None = None,
        image_widget: Any = None,
    ) -> None:
        super().__init__(id=id)
        self.art_width = width
        self.art_height = height
        self.art: TerminalArt | None = None
        self.image: Image.Image | None = None
        self._image_widget = image_widget

    def compose(self) -> ComposeResult:
        yield _ArtText()
        if self._image_widget is not None:
            yield self._image_widget(classes="cover-image")

    def on_mount(self) -> None:
        self.styles.width = self.art_width
        self.styles.height = self.art_height

    @property
    def supports_images(self) -> bool:
        return self._image_widget is not None

    def set_size(self, width: int, height: int) -> bool:
        """Resize the box. Returns True if the size changed (re-render needed)."""
        if (width, height) == (self.art_width, self.art_height):
            return False
        self.art_width, self.art_height = width, height
        self.styles.width, self.styles.height = width, height
        return True

    def set_busy(self, busy: bool) -> None:
        """Loading spinner over the character art while a cover is fetched."""
        with contextlib.suppress(Exception):  # not mounted yet / shutting down
            self.query_one(_ArtText).loading = busy

    def show(self, art: TerminalArt) -> None:
        """Display ``art`` (already rendered at this widget's size)."""
        self.art = art
        self.image = None
        with contextlib.suppress(NoMatches):  # an artwork worker can outlive us
            text = self.query_one(_ArtText)
            text.text = art.to_text()
            text.refresh()
        self.remove_class("-image")

    def show_image(self, image: Image.Image) -> None:
        """Display the actual picture (already cropped to this box's shape)."""
        if self._image_widget is None:
            return
        try:
            widget = self.query_one(".cover-image")
        except NoMatches:  # being removed (shutdown)
            return
        self.image = image
        widget.image = image  # type: ignore[attr-defined]
        self.add_class("-image")
