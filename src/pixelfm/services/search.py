"""Search providers.

The UI depends only on :class:`SearchProvider`; a network-backed provider can
replace :class:`MockSearchProvider` in a later milestone.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Sequence
from typing import Protocol

from pixelfm.models import Song
from pixelfm.services.cache import LRUCache


class SearchError(RuntimeError):
    """A recoverable search failure suitable for showing to the user."""


class SearchProvider(Protocol):
    """Anything that can turn a query into songs.

    ``browsable`` providers return useful results for an empty query, so the
    app can populate the list on startup.
    """

    name: str
    browsable: bool

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        """Return up to ``limit`` songs matching ``query``.

        Raises:
            SearchError: With a message suitable for display.
        """
        ...


class CachedSearchProvider:
    """Wraps a provider with an in-memory LRU cache of recent queries.

    Queries are normalised (case and whitespace) before lookup. Failures are
    not cached.
    """

    def __init__(
        self, provider: SearchProvider, maxsize: int = 32, ttl: float = 600.0
    ) -> None:
        self.inner = provider
        self.name = provider.name
        self.browsable = provider.browsable
        self.cache: LRUCache[tuple[str, int], tuple[Song, ...]] = LRUCache(
            maxsize, ttl=ttl
        )

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        key = (" ".join(query.casefold().split()), limit)
        cached = self.cache.get(key)
        if cached is not None:
            return list(cached)
        songs = await self.inner.search(query, limit)
        self.cache.set(key, tuple(songs))
        return songs


def stable_id(artist: str, title: str) -> str:
    """Derive an 11-character URL-safe ID that is stable across runs."""
    digest = hashlib.sha1(f"{artist}\x1f{title}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()[:11]


def _track(title: str, artist: str, duration: int) -> Song:
    song_id = stable_id(artist, title)
    return Song(
        id=song_id,
        title=title,
        artist=artist,
        duration=duration,
        source_url=f"https://pixelfm.invalid/track/{song_id}",
    )


# Fictional artists and titles, so the mock never resembles real catalogue data.
MOCK_CATALOG: tuple[Song, ...] = (
    _track("Neon Rain on Channel 9", "Vector Bloom", 247),
    _track("Phosphor Heart", "Kasette Kid", 213),
    _track("Midnight Compiler", "Null Pointer Club", 318),
    _track("Arcade Cathedral", "Vector Bloom", 276),
    _track("Signal Lost", "Lumen Drive", 194),
    _track("Chrome Lullaby", "Saturn Mall", 228),
    _track("Low Orbit Love Song", "Lumen Drive", 265),
    _track("8-Bit Sunrise", "Kasette Kid", 182),
    _track("Static Bloom", "Glass Modem", 301),
    _track("Dial-Up Dreams", "Glass Modem", 239),
    _track("Ghost in the Synth", "Null Pointer Club", 287),
    _track("Night Drive (Extended Mix)", "Saturn Mall", 412),
    _track("Pixel Fog", "Echo Terminal", 205),
    _track("Mainframe Romance", "Echo Terminal", 254),
    _track("Afterglow Protocol", "Violet Cache", 233),
    _track("Cyan Skyline", "Violet Cache", 198),
    _track("Tape Hiss Forever", "Kasette Kid", 221),
    _track("Lo-Fi Satellite", "Lumen Drive", 176),
    _track("Overclocked", "Null Pointer Club", 169),
    _track("Rainy Day Bulletin Board", "Glass Modem", 244),
    _track("Holo Boulevard", "Saturn Mall", 296),
    _track("Scanlines", "Echo Terminal", 187),
    _track("Ultraviolet Cassette", "Violet Cache", 262),
    _track("Last Train to Sector 7", "Vector Bloom", 335),
    _track("Soft Reset", "Lumen Drive", 208),
    _track("Floppy Disk Heart", "Kasette Kid", 159),
    _track("Terminal Velocity", "Null Pointer Club", 223),
    _track("Moonlight BIOS", "Echo Terminal", 271),
    _track("Polygon Garden", "Vector Bloom", 249),
    _track("Hyperlink Hymn", "Violet Cache", 3725),
)


class MockSearchProvider:
    """Deterministic in-memory search over :data:`MOCK_CATALOG`.

    Matching is case-insensitive; every whitespace-separated term must appear
    in the title or artist. Title matches rank above artist-only matches, and
    ties keep catalogue order. An empty query returns the whole catalogue.
    """

    name = "mock"
    browsable = True

    def __init__(
        self, catalog: Sequence[Song] = MOCK_CATALOG, latency: float = 0.0
    ) -> None:
        self._catalog = tuple(catalog)
        self._latency = latency

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        if limit < 1:
            raise SearchError("limit must be at least 1")
        if self._latency:
            # Simulates network latency without blocking the event loop.
            await asyncio.sleep(self._latency)

        terms = query.casefold().split()
        if not terms:
            return list(self._catalog[:limit])

        ranked: list[tuple[int, int, Song]] = []
        for index, song in enumerate(self._catalog):
            title = song.title.casefold()
            haystack = f"{title} {song.artist.casefold()}"
            if all(term in haystack for term in terms):
                title_hits = sum(term in title for term in terms)
                ranked.append((-title_hits, index, song))
        ranked.sort(key=lambda item: item[:2])
        return [song for _, _, song in ranked[:limit]]
