"""Resolve a song to a playable audio stream URL with yt-dlp.

Only stream metadata is fetched; audio is streamed later by the player and
never saved to disk. Formats yt-dlp marks as DRM-protected or unplayable are
never selected, and no sign-in, age, or region checks are bypassed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pixelfm.models import Song
from pixelfm.services.cache import LRUCache
from pixelfm.services.deps import js_runtimes
from pixelfm.services.threads import run_in_thread
from pixelfm.services.youtube import error_text

log = logging.getLogger(__name__)

#: Prefer AAC in MP4 (fast, reliable seeking), then any audio-only format.
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best[acodec!=none]"

_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")

#: ``source_url -> yt-dlp info dict``; swapped for fakes in tests.
StreamExtractor = Callable[[str], Mapping[str, Any]]


class StreamError(Exception):
    """A stream could not be resolved.

    Attributes:
        message: One-line, user-facing explanation.
        retryable: ``False`` when retrying cannot help (e.g. video removed).
    """

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """A resolved, directly playable audio stream."""

    url: str
    headers: tuple[tuple[str, str], ...] = ()
    duration: float | None = None
    is_live: bool = False
    expires_at: float | None = None
    description: str = ""
    local: bool = False  # a downloaded file on disk

    def expired(self, now: float | None = None, margin: float = 120.0) -> bool:
        """Whether the signed URL has expired (or will within ``margin`` s)."""
        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) >= self.expires_at - margin


class _Logger:
    """Route yt-dlp messages to the application log, never the terminal."""

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.info("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        log.warning("yt-dlp error: %s", msg)


def ytdlp_stream_info(source_url: str, socket_timeout: float = 15.0) -> Any:
    """Blocking yt-dlp metadata extraction for one video. Never downloads."""
    import yt_dlp

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "skip_download": True,
        "noplaylist": True,
        "format": AUDIO_FORMAT,
        "socket_timeout": socket_timeout,
        "logger": _Logger(),
    }
    if runtimes := js_runtimes():
        options["js_runtimes"] = runtimes
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(source_url, download=False)


def _clean_headers(raw: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Mapping):
        return ()
    headers = []
    for name, value in raw.items():
        if (
            isinstance(name, str)
            and isinstance(value, str)
            and _HEADER_NAME.match(name)
            and "\r" not in value
            and "\n" not in value
        ):
            headers.append((name, value))
    return tuple(headers)


def stream_from_info(info: object) -> StreamInfo:
    """Validate a yt-dlp info dict and extract the stream to play."""
    if not isinstance(info, Mapping):
        raise StreamError("No stream information returned.")
    url = info.get("url")
    if not isinstance(url, str) or urlparse(url).scheme not in {"https", "http"}:
        raise StreamError(
            "No playable audio format found. Try updating yt-dlp: "
            "pip install -U 'yt-dlp[default]'",
            retryable=False,
        )
    expires_at = None
    expire = parse_qs(urlparse(url).query).get("expire")
    if expire and expire[0].isdigit():
        expires_at = float(expire[0])
    duration = info.get("duration")
    if (
        not isinstance(duration, int | float)
        or isinstance(duration, bool)
        or duration <= 0
    ):
        duration = None
    is_live = bool(info.get("is_live"))
    parts = [str(info[k]) for k in ("ext", "acodec") if isinstance(info.get(k), str)]
    if isinstance(info.get("abr"), int | float):
        parts.append(f"{round(info['abr'])}k")
    return StreamInfo(
        url=url,
        headers=_clean_headers(info.get("http_headers")),
        duration=None if is_live else (float(duration) if duration else None),
        is_live=is_live,
        expires_at=expires_at,
        description=" ".join(parts),
    )


def classify_stream_error(exc: BaseException) -> StreamError:
    """Map a yt-dlp exception to a user-facing :class:`StreamError`."""
    if isinstance(exc, StreamError):
        return exc
    text = error_text(exc)
    lowered = text.casefold()
    if "javascript runtime" in lowered or "js runtime" in lowered:
        return StreamError(
            "yt-dlp needs a JavaScript runtime for YouTube. "
            "Install one: brew install deno",
            retryable=False,
        )
    if any(
        s in lowered
        for s in (
            "video unavailable",
            "video is unavailable",
            "video is private",
            "private video",
            "has been removed",
            "no longer available",
            "terminated",
        )
    ):
        return StreamError("This video is unavailable.", retryable=False)
    if "confirm your age" in lowered or "age-restricted" in lowered:
        return StreamError(
            "This video is age-restricted; PixelFM does not bypass sign-in.",
            retryable=False,
        )
    if "sign in" in lowered or "members-only" in lowered or "premium" in lowered:
        return StreamError(
            "YouTube requires sign-in for this video; PixelFM does not bypass that.",
            retryable=False,
        )
    if "country" in lowered or "geo" in lowered:
        return StreamError(
            "This video is not available in your region.", retryable=False
        )
    if "drm" in lowered:
        return StreamError("This video is DRM-protected and cannot be played.", False)
    if "requested format is not available" in lowered or "no video formats" in lowered:
        return StreamError(
            "No playable audio format found. Try updating yt-dlp: "
            "pip install -U 'yt-dlp[default]'",
            retryable=False,
        )
    if "429" in text or "too many requests" in lowered:
        return StreamError("YouTube is rate-limiting requests. Wait and retry.")
    if isinstance(exc, OSError) or any(
        s in lowered
        for s in (
            "urlopen error",
            "unable to download",
            "timed out",
            "connection",
            "ssl",
        )
    ):
        return StreamError("Network error while loading the stream.")
    return StreamError(f"Could not load the stream: {text[:100] or type(exc).__name__}")


class StreamResolver:
    """Resolve songs to streams in a background thread, with an expiring cache."""

    def __init__(
        self,
        extractor: StreamExtractor | None = None,
        *,
        timeout: float = 30.0,
        cache_size: int = 32,
    ) -> None:
        self._extract = extractor or ytdlp_stream_info
        self._timeout = timeout
        self.cache: LRUCache[str, StreamInfo] = LRUCache(cache_size)

    async def resolve(self, song: Song, *, refresh: bool = False) -> StreamInfo:
        """Return a playable stream for ``song``.

        Args:
            refresh: Ignore any cached URL (e.g. after an HTTP 403).

        Raises:
            StreamError: With a user-facing message.
        """
        cached = None if refresh else self.cache.get(song.id)
        if cached is not None and not cached.expired():
            return cached
        log.info(
            "resolving stream for %s (%s)", song.id, "refresh" if refresh else "new"
        )
        try:
            info = await asyncio.wait_for(
                run_in_thread(self._extract, song.source_url), self._timeout
            )
            stream = stream_from_info(info)
        except TimeoutError:
            raise StreamError("Timed out while loading the stream.") from None
        except Exception as exc:
            error = classify_stream_error(exc)
            log.warning("stream resolution failed for %s: %s", song.id, exc)
            raise error from exc
        log.info("resolved %s: %s", song.id, stream.description or "unknown format")
        self.cache.set(song.id, stream)
        return stream


class LocalFirstResolver:
    """Plays a downloaded copy when its file exists; otherwise streams.

    Works offline for downloaded tracks. ``on_missing`` is told when a
    recorded download has disappeared from disk (then streaming is tried).
    """

    def __init__(
        self,
        remote: StreamResolver,
        lookup: Callable[[str], Path | None],
        on_missing: Callable[[Song, Path], None] | None = None,
    ) -> None:
        self.remote = remote
        self._lookup = lookup
        self._on_missing = on_missing

    async def resolve(self, song: Song, *, refresh: bool = False) -> StreamInfo:
        path = self._lookup(song.id)
        if path is not None:
            if await asyncio.to_thread(path.is_file):
                return StreamInfo(
                    url=f"file:{path}",
                    duration=float(song.duration) or None,
                    description=f"local {path.suffix.lstrip('.') or 'file'}",
                    local=True,
                )
            log.warning("downloaded file missing for %s: %s", song.id, path)
            if self._on_missing is not None:
                self._on_missing(song, path)
        return await self.remote.resolve(song, refresh=refresh)
