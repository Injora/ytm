"""Themes, glyph sets, and the Textual stylesheet."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.theme import Theme

STYLESHEET = Path(__file__).with_name("pixelfm.tcss")


def make_theme(
    name: str,
    *,
    primary: str,
    secondary: str,
    background: str,
    surface: str,
    panel: str,
    foreground: str,
    muted: str,
    blurred: str,
    cursor_blurred: str,
    hover: str,
) -> Theme:
    """A PixelFM theme from its colour roles (all ``#rrggbb``)."""
    return Theme(
        name=name,
        primary=primary,
        secondary=secondary,
        accent=primary,
        warning="#ffb020",
        error="#ff3b5c",
        success=primary,
        foreground=foreground,
        background=background,
        surface=surface,
        panel=panel,
        dark=True,
        variables={
            "border": primary,
            "border-blurred": blurred,
            "block-cursor-foreground": background,
            "block-cursor-background": primary,
            "block-cursor-text-style": "bold",
            "block-cursor-blurred-foreground": foreground,
            "block-cursor-blurred-background": cursor_blurred,
            "block-hover-background": hover,
            "footer-background": background,
            "footer-key-foreground": primary,
            "footer-description-foreground": muted,
            # Textual's default ("auto 60%") renders as plain white in markup;
            # use the palette's readable muted colour instead.
            "text-muted": muted,
            "input-cursor-background": primary,
            "input-cursor-foreground": background,
            "input-selection-background": f"{primary}40",
            "scrollbar": blurred,
            "scrollbar-hover": cursor_blurred,
            "scrollbar-active": primary,
            "scrollbar-background": background,
            "scrollbar-background-hover": background,
            "scrollbar-background-active": background,
            "scrollbar-corner-color": background,
        },
    )


#: Default: neon cyan and magenta on midnight charcoal.
PIXELFM_THEME = make_theme(
    "pixelfm",
    primary="#00e5ff",
    secondary="#ff2bd6",
    background="#07080f",
    surface="#0b0e18",
    panel="#11162a",
    foreground="#eaf4ff",
    muted="#7d8aa6",
    blurred="#1c2438",
    cursor_blurred="#12344a",
    hover="#0e1424",
)

#: The original 1.x look: phosphor green on black.
PHOSPHOR_THEME = make_theme(
    "pixelfm-phosphor",
    primary="#3cff7b",
    secondary="#a77bff",
    background="#060907",
    surface="#0a0f0c",
    panel="#111a14",
    foreground="#cfeedb",
    muted="#6f8f7b",
    blurred="#1f3328",
    cursor_blurred="#173322",
    hover="#0f1d15",
)

THEMES = (PIXELFM_THEME, PHOSPHOR_THEME)


def hex_to_rgb(
    colour: str | None, default: tuple[int, int, int]
) -> tuple[int, int, int]:
    """``#rrggbb`` -> (r, g, b); ``default`` for anything else."""
    if not colour or len(colour) != 7 or not colour.startswith("#"):
        return default
    try:
        return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Glyphs:
    """Characters that need an ASCII fallback."""

    playing: str
    paused: str
    stopped: str
    loading: str
    error: str
    favorite: str
    queued: str
    repeat: str
    shuffle: str
    download: str
    bar_fill: str
    bar_empty: str
    bar_head: str
    separator: str
    spinner: tuple[str, ...]
    ellipsis: str
    meter: str  # level ramp, quiet to loud


UNICODE_GLYPHS = Glyphs(
    playing="▶",
    paused="❚❚",
    stopped="■",
    loading="◌",
    error="✕",
    favorite="♥",
    queued="≡",
    repeat="↻",
    shuffle="⇄",
    download="↓",
    bar_fill="━",
    bar_empty="─",
    bar_head="●",
    separator="·",
    spinner=("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
    ellipsis="…",
    meter=" ▁▂▃▄▅▆▇█",
)

ASCII_GLYPHS = Glyphs(
    playing=">",
    paused="||",
    stopped="[]",
    loading="~",
    error="x",
    favorite="*",
    queued="+",
    repeat="rep",
    shuffle="shuf",
    download="v",
    bar_fill="=",
    bar_empty="-",
    bar_head="o",
    separator="|",
    spinner=("|", "/", "-", "\\"),
    ellipsis="...",
    meter=" .:-=+*#@",
)


def glyphs_for(unicode: bool) -> Glyphs:
    """Return the glyph set for the terminal's capabilities."""
    return UNICODE_GLYPHS if unicode else ASCII_GLYPHS
