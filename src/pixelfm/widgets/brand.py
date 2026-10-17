"""Header bar with the PixelFM pixel-font logo."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from pixelfm import __version__

# 5-row pixel font; "#" is a lit pixel.
_FONT: dict[str, tuple[str, ...]] = {
    "P": ("###", "#.#", "###", "#..", "#.."),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "X": ("#.#", "#.#", ".#.", "#.#", "#.#"),
    "E": ("###", "#..", "##.", "#..", "###"),
    "L": ("#..", "#..", "#..", "#..", "###"),
    "F": ("###", "#..", "##.", "#..", "#.."),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#"),
}

_HALF_BLOCKS = {(False, False): " ", (True, False): "▀", (False, True): "▄"}


def pixel_text(word: str) -> list[str]:
    """Render ``word`` as rows of half-block characters (two pixels per row)."""
    rows = [" ".join(_FONT[ch][r] for ch in word) for r in range(5)]
    rows.append("." * len(rows[0]))  # pad to an even pixel height
    lines = []
    for top, bottom in zip(rows[::2], rows[1::2], strict=True):
        lines.append(
            "".join(
                _HALF_BLOCKS.get((t == "#", b == "#"), "█")
                for t, b in zip(top, bottom, strict=True)
            )
        )
    return lines


def logo_markup() -> str:
    """Two-tone logo: PIXEL in the primary colour, FM in the secondary."""
    left, right = pixel_text("PIXEL"), pixel_text("FM")
    return "\n".join(
        f"[$primary]{a}[/]  [$secondary]{b}[/]"
        for a, b in zip(left, right, strict=True)
    )


class BrandBar(Horizontal):
    """Logo, tagline, and source badge. Collapses to one line on short screens."""

    def __init__(self, source_name: str, unicode: bool = True) -> None:
        super().__init__(id="brand")
        self._source_name = source_name
        self._unicode = unicode

    def compose(self) -> ComposeResult:
        if self._unicode:
            yield Static(logo_markup(), id="logo")
        with Vertical(id="brand-text"):
            yield Static("[b $primary]PIXEL[/][b $secondary]FM[/]", id="logo-compact")
            yield Static("[$text-muted]retro terminal radio[/]", id="tagline")
        yield Static(
            f"[$accent]SRC[/] [$text]{self._source_name.upper()}[/]  "
            f"[$text-muted]v{__version__}[/]",
            id="badges",
        )
