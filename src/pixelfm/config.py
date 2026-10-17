"""User configuration.

Configuration is read from ``$PIXELFM_CONFIG`` or
``$XDG_CONFIG_HOME/pixelfm/config.toml`` (default ``~/.config/pixelfm``).
A missing file is fine; an invalid one falls back to defaults with a warning.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

UnicodeMode = Literal["auto", "always", "never"]
ProviderName = Literal["youtube", "mock"]
ArtworkModeName = Literal["color", "mono", "glyph"]
ColorMode = Literal["auto", "truecolor", "256"]
EffectsMode = Literal["auto", "full", "reduced"]
LayoutMode = Literal["auto", "full", "compact"]

_UNICODE_MODES: tuple[UnicodeMode, ...] = ("auto", "always", "never")
_PROVIDERS: tuple[ProviderName, ...] = ("youtube", "mock")
_ARTWORK_MODES: tuple[ArtworkModeName, ...] = ("color", "mono", "glyph")
_COLOR_MODES: tuple[ColorMode, ...] = ("auto", "truecolor", "256")
_EFFECTS: tuple[EffectsMode, ...] = ("auto", "full", "reduced")
_LAYOUTS: tuple[LayoutMode, ...] = ("auto", "full", "compact")


class ConfigError(ValueError):
    """Raised for invalid configuration values."""


@dataclass(frozen=True, slots=True)
class Config:
    """Validated user preferences.

    Attributes:
        theme: ``music`` (colours from the current track's artwork, the
            default) or the name of a registered Textual theme.
        unicode: ``auto`` detects terminal support; ``never`` forces ASCII glyphs.
        provider: ``youtube`` for real search, ``mock`` for offline sample data.
        search_limit: Maximum number of search results to show.
        search_timeout: Seconds before a search is abandoned.
        mock_latency: Simulated search delay in seconds (mock provider only).
        artwork_mode: ``color``, ``mono`` (ASCII), or ``glyph`` (command glyphs).
        artwork_width: Artwork width in terminal cells.
        artwork_height: Artwork height in terminal rows.
        color: ``auto`` detects 24-bit support; ``256`` forces reduced colour.
        volume: Initial playback volume, 0-100.
        ffplay_path: ffplay executable name or path (empty: ``ffplay`` on PATH).
        effects: ``full`` animations and the level meter; ``reduced``
            turns them off; ``auto`` follows macOS "Reduce motion".
        visualizer: Show the real-time audio level meter (when effects allow).
        layout: ``full``, ``compact``, or ``auto`` (compact on small terminals).
        downloads_dir: Folder for downloaded audio (empty: ``~/Music/PixelFM``).
    """

    theme: str = "music"
    unicode: UnicodeMode = "auto"
    provider: ProviderName = "youtube"
    search_limit: int = 20
    search_timeout: float = 20.0
    mock_latency: float = 0.35
    artwork_mode: ArtworkModeName = "color"
    artwork_width: int = 16
    artwork_height: int = 8
    color: ColorMode = "auto"
    volume: int = 80
    ffplay_path: str = ""
    effects: EffectsMode = "auto"
    visualizer: bool = True
    layout: LayoutMode = "auto"
    downloads_dir: str = ""


_DEFAULTS = Config()


def _choice(data: Mapping[str, Any], key: str, choices: tuple[str, ...]) -> str:
    value = data.get(key, getattr(_DEFAULTS, key))
    if value not in choices:
        raise ConfigError(f"{key} must be one of: {', '.join(choices)}")
    return value


def _int(data: Mapping[str, Any], key: str, low: int, high: int) -> int:
    value = data.get(key, getattr(_DEFAULTS, key))
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not low <= value <= high
    ):
        raise ConfigError(f"{key} must be an integer between {low} and {high}")
    return value


def _float(data: Mapping[str, Any], key: str, low: float, high: float) -> float:
    value = data.get(key, getattr(_DEFAULTS, key))
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not low <= value <= high
    ):
        raise ConfigError(f"{key} must be a number between {low:g} and {high:g}")
    return float(value)


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, getattr(_DEFAULTS, key))
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value.strip()


def _bool(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key, getattr(_DEFAULTS, key))
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


def parse_config(data: Mapping[str, Any]) -> Config:
    """Build a :class:`Config` from parsed TOML, validating every field."""
    unknown = sorted(set(data) - set(Config.__dataclass_fields__))
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")

    theme = data.get("theme", _DEFAULTS.theme)
    if not isinstance(theme, str) or not theme.strip():
        raise ConfigError("theme must be a non-empty string")

    return Config(
        theme=theme.strip(),
        unicode=_choice(data, "unicode", _UNICODE_MODES),  # type: ignore[arg-type]
        provider=_choice(data, "provider", _PROVIDERS),  # type: ignore[arg-type]
        search_limit=_int(data, "search_limit", 1, 100),
        search_timeout=_float(data, "search_timeout", 1, 120),
        mock_latency=_float(data, "mock_latency", 0, 10),
        artwork_mode=_choice(data, "artwork_mode", _ARTWORK_MODES),  # type: ignore[arg-type]
        artwork_width=_int(data, "artwork_width", 4, 64),
        artwork_height=_int(data, "artwork_height", 2, 32),
        color=_choice(data, "color", _COLOR_MODES),  # type: ignore[arg-type]
        volume=_int(data, "volume", 0, 100),
        ffplay_path=_string(data, "ffplay_path"),
        effects=_choice(data, "effects", _EFFECTS),  # type: ignore[arg-type]
        visualizer=_bool(data, "visualizer"),
        layout=_choice(data, "layout", _LAYOUTS),  # type: ignore[arg-type]
        downloads_dir=_string(data, "downloads_dir"),
    )


def parse_config_lenient(data: Mapping[str, Any]) -> tuple[Config, list[str]]:
    """Like :func:`parse_config`, but keeps every valid setting.

    Each invalid or unknown key is dropped (falling back to its default) and
    reported, so one typo does not discard the rest of the file.
    """
    valid: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in data.items():
        try:
            parse_config({key: value})
        except ConfigError as exc:
            problems.append(str(exc))
        else:
            valid[key] = value
    return parse_config(valid), problems


def config_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the config file location for the given environment."""
    env = os.environ if env is None else env
    if override := env.get("PIXELFM_CONFIG"):
        return Path(override).expanduser()
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "pixelfm" / "config.toml"


def load_config(path: Path | None = None) -> tuple[Config, list[str]]:
    """Load configuration from disk.

    Returns the config and a list of human-readable warnings. Never raises:
    a missing file means defaults, an unreadable one means defaults plus a
    warning, and invalid individual settings fall back to their defaults.
    """
    path = config_path() if path is None else path
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return Config(), []
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Config(), [f"Could not read {path}: {exc}"]
    config, problems = parse_config_lenient(data)
    return config, [f"Ignored invalid setting in {path}: {p}" for p in problems]


def detect_unicode(
    mode: UnicodeMode = "auto",
    env: Mapping[str, str] | None = None,
    encoding: str | None = None,
) -> bool:
    """Decide whether to draw Unicode glyphs.

    ``PIXELFM_ASCII=1`` forces ASCII regardless of ``mode``.
    """
    env = os.environ if env is None else env
    if env.get("PIXELFM_ASCII", "") not in ("", "0"):
        return False
    if mode != "auto":
        return mode == "always"
    if env.get("TERM") in {"dumb", "linux", "vt100"}:
        return False
    if encoding is None:
        encoding = getattr(sys.stdout, "encoding", None) or ""
    return encoding.lower().replace("-", "").replace("_", "") == "utf8"


def detect_truecolor(
    mode: ColorMode = "auto", env: Mapping[str, str] | None = None
) -> bool:
    """Decide whether the terminal supports 24-bit colour."""
    if mode != "auto":
        return mode == "truecolor"
    env = os.environ if env is None else env
    if env.get("COLORTERM", "").lower() in {"truecolor", "24bit"}:
        return True
    return "direct" in env.get("TERM", "")


def detect_reduced_motion(env: Mapping[str, str] | None = None) -> bool:
    """Whether the user asked for reduced motion.

    ``PIXELFM_REDUCED_MOTION=1``/``0`` decides directly; otherwise the macOS
    "Reduce motion" accessibility setting is read (best effort).
    """
    env = os.environ if env is None else env
    override = env.get("PIXELFM_REDUCED_MOTION")
    if override is not None:
        return override not in ("", "0")
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "1"


def resolve_effects(mode: EffectsMode, env: Mapping[str, str] | None = None) -> bool:
    """``True`` for full effects (animations, meters)."""
    if mode != "auto":
        return mode == "full"
    return not detect_reduced_motion(env)
