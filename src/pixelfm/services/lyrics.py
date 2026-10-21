"""Time-synced lyrics from LRCLIB (https://lrclib.net), a free, keyless API.

YouTube titles are messy ("Artist - Song (Official Video)", channels named
"ArtistVEVO" or "Artist - Topic"), so the title is cleaned before asking.
Synced (LRC) lyrics are preferred; plain lyrics are used when that is all
there is. Responses are only ever parsed as JSON text; nothing is executed.
"""

from __future__ import annotations

import bisect
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pixelfm import __version__
from pixelfm.models import Song
from pixelfm.services.artwork import _ssl_context
from pixelfm.services.cache import DiskCache, LRUCache
from pixelfm.services.threads import run_in_thread

log = logging.getLogger(__name__)

API = "https://lrclib.net/api"
MAX_RESPONSE_BYTES = 1024 * 1024
#: A match may differ from the track's length by this many seconds.
DURATION_SLACK = 4
#: Fewer words than this is a stub or test entry ("[00:00.00]probe"), not lyrics.
MIN_LINES = 4

_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_NOISE = re.compile(
    r"[\(\[【][^\)\]】]*(official|video|audio|visuali[sz]er|lyrics?|hd|hq|4k|mv|m/v|"
    r"remaster(ed)?|explicit|clean)[^\)\]】]*[\)\]】]",
    re.IGNORECASE,
)
_FEAT = re.compile(r"[\(\[]?\s*\b(feat|ft)\.?\s[^\)\]]*[\)\]]?", re.IGNORECASE)
_CHANNEL = re.compile(r"(\s*-\s*topic|vevo|\s+official)$", re.IGNORECASE)
_DASH = re.compile(r"\s+[-\u2013\u2014]\s+")  # hyphen, en and em dash

#: ``url -> parsed JSON`` (``None`` for 404); swapped for fakes in tests.
Fetcher = Callable[[str], Any]


class LyricsError(Exception):
    """Lyrics could not be fetched (network or server trouble)."""


@dataclass(frozen=True, slots=True)
class LyricLine:
    time: float  # seconds; 0 for every line of unsynced lyrics
    text: str


@dataclass(frozen=True, slots=True)
class Lyrics:
    lines: tuple[LyricLine, ...]
    synced: bool

    def index_at(self, position: float, duration: float = 0.0) -> int:
        """The line being sung at ``position``; -1 before the first one.

        Unsynced lyrics have no timing, so the line is estimated from how far
        through the track ``position`` is.
        """
        if not self.lines:
            return -1
        if not self.synced:
            if duration <= 0:
                return 0
            fraction = max(0.0, min(1.0, position / duration))
            return min(len(self.lines) - 1, int(fraction * len(self.lines)))
        times = [line.time for line in self.lines]
        return bisect.bisect_right(times, position) - 1

    def to_json(self) -> str:
        return json.dumps(
            {"synced": self.synced, "lines": [[ln.time, ln.text] for ln in self.lines]}
        )

    @classmethod
    def from_json(cls, data: bytes | str) -> Lyrics | None:
        try:
            raw = json.loads(data)
            lines = tuple(LyricLine(float(t), str(s)) for t, s in raw["lines"])
            return cls(lines, bool(raw["synced"])) if lines else None
        except (ValueError, TypeError, KeyError):
            return None


# ------------------------------------------------------------------ parsing


def parse_lrc(text: str) -> tuple[LyricLine, ...]:
    """Parse LRC ``[mm:ss.xx] words`` lines, sorted by time.

    A line may carry several timestamps (a repeated chorus). Metadata tags
    such as ``[ar: ...]`` and lines without a timestamp are skipped.
    """
    lines: list[LyricLine] = []
    for raw in text.splitlines():
        stamps = list(_TIMESTAMP.finditer(raw))
        if not stamps:
            continue
        words = raw[stamps[-1].end() :].strip()
        for stamp in stamps:
            seconds = stamp.group(2).replace(":", ".")
            lines.append(LyricLine(int(stamp.group(1)) * 60 + float(seconds), words))
    lines.sort(key=lambda line: line.time)
    return tuple(lines)


def parse_plain(text: str) -> tuple[LyricLine, ...]:
    return tuple(
        LyricLine(0.0, line.strip()) for line in text.splitlines() if line.strip()
    )


def from_record(record: object) -> Lyrics | None:
    """Lyrics from one LRCLIB record; ``None`` for instrumentals or no text."""
    if not isinstance(record, Mapping) or record.get("instrumental"):
        return None
    synced = record.get("syncedLyrics")
    if isinstance(synced, str) and _enough(lines := parse_lrc(synced)):
        return Lyrics(lines, synced=True)
    plain = record.get("plainLyrics")
    if isinstance(plain, str) and _enough(lines := parse_plain(plain)):
        return Lyrics(lines, synced=False)
    return None


def _enough(lines: tuple[LyricLine, ...]) -> bool:
    return sum(1 for line in lines if line.text) >= MIN_LINES


def _clean(text: str) -> str:
    text = _NOISE.sub(" ", text)
    text = _FEAT.sub(" ", text)
    return " ".join(text.replace('"', " ").split())


def queries(song: Song) -> list[tuple[str, str]]:
    """``(artist, title)`` guesses for ``song``, most likely first."""
    channel = " ".join(_CHANNEL.sub("", song.artist).split()) or song.artist
    title = _clean(song.title)
    guesses: list[tuple[str, str]] = []
    parts = _DASH.split(title, maxsplit=1)
    if len(parts) == 2 and all(parts):
        guesses.append((parts[0], parts[1]))  # "Artist - Song (Official Video)"
    guesses.append((channel, title))
    return list(dict.fromkeys(guesses))


def _pick(records: object, duration: int) -> Lyrics | None:
    """The best search result: synced and the right length beats the rest."""
    if not isinstance(records, list):
        return None
    best: tuple[int, Lyrics] | None = None
    for record in records[:20]:
        lyrics = from_record(record)
        if lyrics is None:
            continue
        length = record.get("duration") if isinstance(record, Mapping) else None
        if duration and isinstance(length, int | float):
            if abs(length - duration) > DURATION_SLACK:
                continue
        elif duration:
            continue
        rank = 0 if lyrics.synced else 1
        if best is None or rank < best[0]:
            best = (rank, lyrics)
    return best[1] if best else None


# ------------------------------------------------------------------ network


def fetch_json(url: str, timeout: float = 8.0) -> Any:
    """Blocking GET of a JSON document; ``None`` for 404. Data only."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"PixelFM/{__version__} (terminal music player)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_ssl_context()
        ) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise LyricsError(f"lyrics request failed (HTTP {exc.code})") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise LyricsError("network error while fetching lyrics") from exc
    if len(data) > MAX_RESPONSE_BYTES:
        raise LyricsError("lyrics response too large")
    try:
        return json.loads(data)
    except ValueError as exc:
        raise LyricsError("unreadable lyrics response") from exc


class LyricsService:
    """Find lyrics for songs off the event loop, with memory and disk caches.

    Found lyrics persist on disk; "no lyrics" is remembered for this session
    only, since LRCLIB grows and a song may get lyrics later. Network errors
    are not cached at all.
    """

    def __init__(
        self,
        fetcher: Fetcher | None = None,
        *,
        timeout: float = 8.0,
        disk: DiskCache | None = None,
    ) -> None:
        self._fetch = fetcher or (lambda url: fetch_json(url, timeout=timeout))
        self.disk = disk
        self.found: LRUCache[str, Lyrics] = LRUCache(128)
        self.missing: set[str] = set()

    async def lyrics_for(self, song: Song) -> Lyrics | None:
        if song.id in self.missing:
            return None
        if (cached := self.found.get(song.id)) is not None:
            return cached
        data = self.disk.get(song.id) if self.disk is not None else None
        if data is not None and (cached := Lyrics.from_json(data)) is not None:
            self.found.set(song.id, cached)
            return cached
        try:
            lyrics = await run_in_thread(self._lookup, song)
        except LyricsError as exc:
            log.info("lyrics unavailable for %s: %s", song.id, exc)
            return None
        if lyrics is None:
            self.missing.add(song.id)
            return None
        self.found.set(song.id, lyrics)
        if self.disk is not None:
            self.disk.set(song.id, lyrics.to_json().encode())
        return lyrics

    def _lookup(self, song: Song) -> Lyrics | None:
        """Exact lookups first, then a search. Runs in a worker thread."""
        guesses = queries(song)
        fallback: Lyrics | None = None
        for artist, title in guesses:
            params = {"artist_name": artist, "track_name": title}
            if song.duration:
                params["duration"] = str(song.duration)
            lyrics = from_record(self._fetch(f"{API}/get?{urlencode(params)}"))
            if lyrics is not None and lyrics.synced:
                return lyrics
            fallback = fallback or lyrics
        for artist, title in guesses:
            found = _pick(
                self._fetch(f"{API}/search?{urlencode({'q': f'{artist} {title}'})}"),
                song.duration,
            )
            if found is not None and (found.synced or fallback is None):
                return found
        return fallback
