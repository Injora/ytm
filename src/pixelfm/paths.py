"""Where PixelFM keeps persistent data and disposable caches.

Persistent library data and caches live in separate directories so a cache
cleanup can never touch the user's favorites or playlists.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

APP_NAME = "PixelFM"


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Persistent data: ``$PIXELFM_DATA_DIR``, else
    ``~/Library/Application Support/PixelFM`` on macOS, else
    ``$XDG_DATA_HOME/pixelfm``."""
    env = os.environ if env is None else env
    if override := env.get("PIXELFM_DATA_DIR"):
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base).expanduser() / "pixelfm"


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """Disposable caches: ``$PIXELFM_CACHE_DIR``, else ``~/Library/Caches/PixelFM``
    on macOS, else ``$XDG_CACHE_HOME/pixelfm``."""
    env = os.environ if env is None else env
    if override := env.get("PIXELFM_CACHE_DIR"):
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base).expanduser() / "pixelfm"


def display_path(path: Path | str) -> str:
    """``path`` for display, with the home folder shortened to ``~`` (shorter,
    and screenshots don't reveal the account name)."""
    text = str(path)
    home = str(Path.home())
    if home not in ("", os.sep) and (text == home or text.startswith(home + os.sep)):
        return "~" + text[len(home) :]
    return text


def library_path(env: Mapping[str, str] | None = None) -> Path:
    """The SQLite library database."""
    return data_dir(env) / "library.sqlite3"
