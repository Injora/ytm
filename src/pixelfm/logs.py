"""Application log file. Diagnostics go here, never to the terminal UI."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path


def default_log_path(env: Mapping[str, str] | None = None) -> Path:
    """``$PIXELFM_LOG_FILE``, else ``~/Library/Logs/PixelFM/pixelfm.log`` on macOS,
    else ``$XDG_STATE_HOME/pixelfm/pixelfm.log``."""
    env = os.environ if env is None else env
    if override := env.get("PIXELFM_LOG_FILE"):
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "PixelFM" / "pixelfm.log"
    state = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state).expanduser() / "pixelfm" / "pixelfm.log"


def setup_logging(path: Path | None = None, level: str | None = None) -> Path | None:
    """Log ``pixelfm.*`` to a rotating file. Returns the path, or ``None`` on failure.

    The level comes from ``level``, ``$PIXELFM_LOG_LEVEL``, or defaults to INFO.
    """
    path = path or default_log_path()
    level_name = (level or os.environ.get("PIXELFM_LOG_LEVEL") or "INFO").upper()
    logger = logging.getLogger("pixelfm")
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    return path
