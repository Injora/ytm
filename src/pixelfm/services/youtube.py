"""YouTube search via yt-dlp's Python API.

Only flat search metadata is requested: no video pages are resolved and
nothing is downloaded. yt-dlp is imported lazily inside the worker thread so
startup stays fast.
"""

from __future__ import annotations

import asyncio
import math
import re
import textwrap
from collections.abc import Callable, Mapping
from typing import Any

from pixelfm.models import Song, SongValidationError
from pixelfm.services.search import SearchError
from pixelfm.services.threads import run_in_thread

WATCH_URL = "https://www.youtube.com/watch?v={id}"

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_UNAVAILABLE_TITLES = {"[private video]", "[deleted video]"}

#: ``(query, limit) -> yt-dlp info dict``; swapped for fakes in tests.
Extractor = Callable[[str, int], Mapping[str, Any]]


class _SilentLogger:
    """Swallow yt-dlp output so nothing is written over the TUI."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def ytdlp_extract(query: str, limit: int, socket_timeout: float = 10.0) -> Any:
    """Blocking flat search with yt-dlp. Never downloads media."""
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": socket_timeout,
        "logger": _SilentLogger(),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(f"ytsearch{limit}:{query}", download=False)


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def pick_thumbnail(entry: Mapping[str, Any], min_width: int = 160) -> str | None:
    """Choose the smallest thumbnail at least ``min_width`` wide.

    Falls back to the widest available, then to the ``thumbnail`` field.
    """
    candidates: list[tuple[int, str]] = []
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list):
        for thumb in thumbnails:
            if not isinstance(thumb, Mapping):
                continue
            url = thumb.get("url")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                width = thumb.get("width")
                candidates.append((width if isinstance(width, int) else 0, url))
    if candidates:
        big_enough = [c for c in candidates if c[0] >= min_width]
        if big_enough:
            return min(big_enough)[1]
        return max(candidates)[1]
    url = entry.get("thumbnail")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url
    return None


def parse_entry(entry: object) -> Song | None:
    """Map one yt-dlp search entry to a :class:`Song`; ``None`` if unusable."""
    if not isinstance(entry, Mapping):
        return None
    if entry.get("_type") not in (None, "url", "video"):
        return None  # playlists, channels
    if entry.get("ie_key") not in (None, "Youtube"):
        return None
    video_id = entry.get("id")
    if not isinstance(video_id, str) or not _VIDEO_ID.match(video_id):
        return None

    title = _text(entry.get("title"))
    if title.casefold() in _UNAVAILABLE_TITLES:
        return None
    artist = next(
        (
            name
            for key in ("channel", "uploader", "artist", "creator")
            if (name := _text(entry.get(key)))
        ),
        "Unknown artist",
    )
    raw_duration = entry.get("duration")
    duration = 0
    if (
        isinstance(raw_duration, int | float)
        and not isinstance(raw_duration, bool)
        and math.isfinite(raw_duration)
        and raw_duration > 0
    ):
        duration = round(raw_duration)

    try:
        return Song(
            id=video_id,
            title=title or "Untitled",
            artist=artist,
            duration=duration,
            source_url=WATCH_URL.format(id=video_id),
            thumbnail_url=pick_thumbnail(entry),
        )
    except SongValidationError:
        return None


def parse_search_results(info: object) -> list[Song]:
    """Parse a yt-dlp search result, skipping malformed and duplicate entries."""
    if not isinstance(info, Mapping):
        return []
    entries = info.get("entries")
    if not isinstance(entries, list | tuple):
        return []
    songs: list[Song] = []
    seen: set[str] = set()
    for entry in entries:
        song = parse_entry(entry)
        if song is not None and song.id not in seen:
            seen.add(song.id)
            songs.append(song)
    return songs


def error_text(exc: BaseException) -> str:
    """First line of an exception message, without ANSI codes or ``ERROR:``."""
    text = _ANSI.sub("", str(exc)).strip()
    return text.removeprefix("ERROR:").strip().splitlines()[0] if text else ""


def friendly_error(exc: BaseException) -> str:
    """Turn a yt-dlp/network exception into a one-line message for users."""
    text = error_text(exc)
    lowered = text.casefold()
    if "429" in text or "too many requests" in lowered:
        return "YouTube is rate-limiting requests. Wait a minute and try again."
    if "sign in" in lowered:
        return "YouTube asked for sign-in; PixelFM does not bypass that."
    network_hints = (
        "urlopen error",
        "unable to download",
        "nodename nor servname",
        "name or service not known",
        "network is unreachable",
        "connection",
        "timed out",
        "ssl",
    )
    if isinstance(exc, OSError) or any(h in lowered for h in network_hints):
        return "Network error: couldn't reach YouTube. Check your connection."
    detail = (
        textwrap.shorten(text, 100, placeholder="…") if text else type(exc).__name__
    )
    return f"YouTube search failed: {detail}"


class YouTubeSearchProvider:
    """Searches YouTube through yt-dlp in a background thread."""

    name = "youtube"
    browsable = False

    def __init__(
        self, timeout: float = 20.0, extractor: Extractor | None = None
    ) -> None:
        self._timeout = timeout
        self._extract: Extractor = extractor or (
            lambda q, n: ytdlp_extract(q, n, socket_timeout=min(10.0, timeout))
        )

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        query = " ".join(query.split())
        if not query:
            raise SearchError("Type something to search for.")
        if limit < 1:
            raise SearchError("limit must be at least 1")
        try:
            info = await asyncio.wait_for(
                run_in_thread(self._extract, query, limit), self._timeout
            )
        except TimeoutError:
            raise SearchError(
                f"Search timed out after {self._timeout:g}s. Check your connection."
            ) from None
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(friendly_error(exc)) from exc
        return parse_search_results(info)[:limit]
