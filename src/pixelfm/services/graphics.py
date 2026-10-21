"""Real cover images in terminals that can draw pictures.

Most terminals, including macOS Terminal.app, can only draw character cells;
there the cover is drawn with quadrant blocks (see :mod:`artwork`). Terminals
with an image protocol can show the actual picture, via ``textual-image``:

* Kitty graphics protocol: Kitty, Ghostty, WezTerm.
* Sixel: iTerm2 3.5+, WezTerm, foot, VS Code (with images enabled).

Detection asks the terminal and reads its reply from stdin, so it must run
before Textual starts: ``__main__`` calls :func:`detect` once and passes the
result to the app. ``PIXELFM_GRAPHICS=off`` (or ``--images off``) disables it;
``PIXELFM_GRAPHICS=sixel`` or ``kitty`` forces a protocol for a terminal that
supports one but doesn't say so.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)

KINDS = ("sixel", "kitty")
#: Terminals known to have no image protocol: skip the probe entirely.
NO_IMAGES = {"Apple_Terminal"}


def _is_tty() -> bool:
    out, inp = sys.__stdout__, sys.__stdin__
    return bool(out and inp and out.isatty() and inp.isatty())


def detect(env: Mapping[str, str] | None = None) -> str | None:
    """``"sixel"``, ``"kitty"``, or ``None`` (draw covers with characters)."""
    env = os.environ if env is None else env
    choice = env.get("PIXELFM_GRAPHICS", "auto").strip().lower()
    if choice == "off" or "NO_COLOR" in env:
        return None
    if choice in KINDS:
        return choice
    if not _is_tty() or env.get("TERM_PROGRAM") in NO_IMAGES:
        return None
    try:
        from textual_image import renderable  # probes the terminal once
    except Exception:
        log.exception("terminal image support could not be checked")
        return None
    if renderable.Image is renderable.SixelImage:
        kind = "sixel"
    elif renderable.Image is renderable.TGPImage:
        kind = "kitty"
    else:
        kind = None
    log.info("terminal images: %s", kind or "not supported (character covers)")
    return kind


def widget_class(kind: str) -> Any:
    """The ``textual-image`` widget for ``kind``."""
    from textual_image import widget

    return {"sixel": widget.SixelImage, "kitty": widget.TGPImage}[kind]


def cell_pixels() -> tuple[int, int]:
    """Terminal cell size in pixels (as probed; 10x20 if unknown)."""
    try:
        from textual_image._terminal import get_cell_size

        size = get_cell_size()
        return max(1, size.width), max(1, size.height)
    except Exception:
        return 10, 20
