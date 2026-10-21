"""Finding a playable YouTube video for a Spotify track.

Spotify gives metadata, not audio, so each imported track needs a YouTube
match. This is a guess with evidence, never a certainty:

1. **Search**: ``"<artist> - <title>"`` through the existing YouTube search.
2. **Rank** (pure, :func:`score`): title words and artist names, duration as
   a secondary signal, official uploads ("<Artist> - Topic" channels are
   YouTube Music's studio recordings), and a strong penalty for versions the
   Spotify track isn't: lyrics, live, remix, sped up, slowed, cover, ...
3. **Decide**: ``strong`` and ``likely`` matches are used (and labelled as
   such); anything weaker stays unmatched for the user to review.

Searches run one or two at a time with a small pause between them, so a big
playlist never fires hundreds of requests at once, and can be cancelled.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from pixelfm.models import Song
from pixelfm.services.spotify import SpotifyTrack

STRONG = 0.8
LIKELY = 0.62
CANDIDATES = 8
CONCURRENCY = 2
PAUSE = 0.35  # seconds between searches (per worker)

#: Words that mark a different version of a song. If the Spotify title
#: doesn't contain them, a candidate that does is probably the wrong one.
VERSION_WORDS = (
    "live",
    "remix",
    "lyrics",
    "lyric",
    "sped up",
    "speed up",
    "slowed",
    "reverb",
    "nightcore",
    "8d",
    "cover",
    "karaoke",
    "instrumental",
    "acoustic",
    "mashup",
    "bass boosted",
    "1 hour",
    "extended",
    "edit",
    "reaction",
    "tutorial",
    "lesson",
    "version",
    "minutes",
    "hour",
    "hours",
    "loop",
    "fan made",
    "rehearsal",
    "performance",
    "concert",
    "session",
    "tour",
)
_FEAT = re.compile(r"[\(\[]\s*(feat|ft|with)\.?\s[^\)\]]*[\)\]]", re.IGNORECASE)
#: " - 2011 Remaster", " - Radio Edit", " - Mono Version": not part of the name.
_SUFFIX = re.compile(
    r"\s+-\s+(?=.*\b(remaster(ed)?|version|mix|edit)\b)", re.IGNORECASE
)
_NOISE = re.compile(
    r"[\(\[][^\)\]]*(official|video|audio|visualizer|hd|4k|mv|m/v)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Lowercase, accents stripped, punctuation to spaces, '&' as 'and'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("&", " and ").replace("\u2019", "'")
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def core_title(title: str) -> str:
    """The song name without "(feat. X)", "[Official Video]" and similar."""
    title = _FEAT.sub(" ", title)
    title = _NOISE.sub(" ", title)
    title = _SUFFIX.split(title)[0]
    return normalize(title)


def _words(text: str) -> set[str]:
    return set(text.split())


def same_song(
    title: str, artists: Sequence[str], other_title: str, other_artist: str
) -> bool:
    """Is the playlist entry ``other_title`` by ``other_artist`` this song?

    The same name, and the same artist when that is known. Entries played
    from YouTube often carry the artist in the title ("Artist - Song
    (Official Video)") and a channel as the artist ("Artist - Topic",
    "ArtistVEVO"). Other versions (live, remix, ...) are different songs.
    """
    want = core_title(title)
    if not want:
        return False
    have = core_title(other_title)
    main = normalize(artists[0]) if artists else ""
    if not main:
        return have == want
    channel = normalize(other_artist)
    squashed = main.replace(" ", "")
    by_artist = (
        main in channel
        or squashed in channel.replace(" ", "")
        or (bool(channel) and channel in main)
    )
    if have == want:
        return by_artist or main in normalize(other_title)
    # "Artist - Song": the title is the artist's name and the song's.
    rest = " ".join(have.replace(main, " ").split())
    return rest == want and main in have


@dataclass(frozen=True, slots=True)
class Candidate:
    song: Song
    score: float  # 0..1
    reasons: tuple[str, ...]
    rank_score: float = 0.0  # unclamped: breaks ties (e.g. official uploads)

    @property
    def label(self) -> str:
        return confidence_label(self.score)


def confidence_label(score: float) -> str:
    if score >= STRONG:
        return "strong"
    if score >= LIKELY:
        return "likely"
    return "weak"


def score(wanted: SpotifyTrack, song: Song) -> Candidate:
    """How well ``song`` (a YouTube result) fits ``wanted``; 0..1 with reasons."""
    reasons: list[str] = []
    want_title = core_title(wanted.title)
    have_title = normalize(song.title)
    channel = normalize(song.artist)
    title_words = _words(want_title)
    have_words = _words(have_title)

    # Title: every word of the song name present, or a close string match.
    overlap = len(title_words & have_words) / len(title_words) if title_words else 0.0
    ratio = difflib.SequenceMatcher(None, want_title, core_title(song.title)).ratio()
    title_score = max(overlap, ratio)
    if title_score >= 0.99:
        reasons.append("exact title")
    elif title_score >= 0.75:
        reasons.append("similar title")

    # Artist: in the video title or the channel name.
    artists = [normalize(a) for a in wanted.artists if a.strip()]
    main = artists[0] if artists else ""
    artist_score = 0.0
    official = False
    if main:
        if main in have_title or main in channel:
            artist_score = 1.0
            reasons.append("artist matches")
        elif any(a and (a in have_title or a in channel) for a in artists[1:]):
            artist_score = 0.6
            reasons.append("featured artist matches")
        squashed = channel.replace(" ", "")
        topic = channel.endswith(" topic") and main in channel
        vevo = "vevo" in squashed and main.replace(" ", "") in squashed
        if channel == main or topic or vevo:
            official = True
            reasons.append("official channel")

    # Duration: a strong hint when both sides know it.
    duration_score = 0.5
    length_penalty = 0.0
    if wanted.duration and song.duration:
        diff = abs(wanted.duration - song.duration)
        if diff <= 3:
            duration_score = 1.0
            reasons.append("same length")
        elif diff <= 8:
            duration_score = 0.75
        elif diff <= 20:
            duration_score = 0.35
        else:
            duration_score = 0.0
            reasons.append(f"length differs by {diff:.0f}s")
            if diff > 60:  # a different edit, a loop, or another song
                length_penalty = 0.25

    total = 0.5 * title_score + 0.3 * artist_score + 0.2 * duration_score
    total -= length_penalty
    if official:
        total += 0.08
    elif "official audio" in have_title or "official music video" in have_title:
        total += 0.04

    # Other versions: lyric videos, live, remixes, sped up/slowed, covers...
    wanted_norm = normalize(wanted.title)
    for word in VERSION_WORDS:
        pattern = rf"\b{re.escape(word)}\b"
        if re.search(pattern, have_title) and not re.search(pattern, wanted_norm):
            total -= 0.45  # never auto-picked over the original recording
            reasons.append(f'"{word}" version')
            break
    return Candidate(song, max(0.0, min(1.0, total)), tuple(reasons), total)


def rank(wanted: SpotifyTrack, results: Sequence[Song]) -> list[Candidate]:
    seen: set[str] = set()
    unique = [s for s in results if not (s.id in seen or seen.add(s.id))]  # type: ignore[func-returns-value]
    return sorted(
        (score(wanted, s) for s in unique), key=lambda c: c.rank_score, reverse=True
    )


def query_for(track: SpotifyTrack) -> str:
    artist = track.artists[0] if track.artists else ""
    title = _FEAT.sub(" ", track.title).strip()
    return f"{artist} - {title}".strip(" -")


Search = Callable[[str, int], Awaitable[list[Song]]]


class Matcher:
    """Runs searches politely and ranks the results."""

    def __init__(
        self,
        search: Search,
        *,
        concurrency: int = CONCURRENCY,
        pause: float = PAUSE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._search = search
        self._slots = asyncio.Semaphore(concurrency)
        self._pause = pause
        self._sleep = sleep

    async def candidates(
        self, track: SpotifyTrack, query: str | None = None
    ) -> list[Candidate]:
        async with self._slots:
            results = await self._search(query or query_for(track), CANDIDATES)
            if self._pause:
                await self._sleep(self._pause)
        return rank(track, results)

    async def best(self, track: SpotifyTrack) -> Candidate | None:
        """The best acceptable candidate, or ``None`` (leave it unmatched)."""
        ranked = await self.candidates(track)
        if ranked and ranked[0].score >= LIKELY:
            return ranked[0]
        return None
