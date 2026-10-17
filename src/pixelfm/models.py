"""Domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SongValidationError(ValueError):
    """Raised when a :class:`Song` is constructed with invalid data."""


def format_duration(seconds: int) -> str:
    """Format seconds as ``m:ss``, or ``h:mm:ss`` for an hour or more."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@dataclass(frozen=True, slots=True)
class Song:
    """A playable track.

    Attributes:
        id: Stable identifier from the source (URL-safe, max 64 chars).
        title: Track title.
        artist: Performing artist or channel name.
        duration: Length in whole seconds. ``0`` means unknown (e.g. a live stream).
        source_url: Canonical http(s) URL of the track at its source.
        thumbnail_url: Optional http(s) URL of the cover art.
    """

    id: str
    title: str
    artist: str
    duration: int
    source_url: str
    thumbnail_url: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_PATTERN.match(self.id):
            raise SongValidationError(f"invalid song id: {self.id!r}")
        for name in ("title", "artist"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SongValidationError(f"{name} must be a non-empty string")
        # bool is a subclass of int; reject it explicitly.
        if (
            not isinstance(self.duration, int)
            or isinstance(self.duration, bool)
            or self.duration < 0
        ):
            raise SongValidationError(
                f"duration must be a non-negative int, got {self.duration!r}"
            )
        if not isinstance(self.source_url, str) or not _is_http_url(self.source_url):
            raise SongValidationError(f"invalid source_url: {self.source_url!r}")
        if self.thumbnail_url is not None and (
            not isinstance(self.thumbnail_url, str)
            or not _is_http_url(self.thumbnail_url)
        ):
            raise SongValidationError(f"invalid thumbnail_url: {self.thumbnail_url!r}")

    @property
    def duration_label(self) -> str:
        """Human-readable duration, or ``--:--`` when unknown."""
        return format_duration(self.duration) if self.duration else "--:--"
