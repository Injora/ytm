"""One-time playlist shuffle: an unbiased Fisher-Yates permutation.

This never touches the saved playlist; it returns a new play order. The
global shuffle toggle (:meth:`pixelfm.queue.PlayQueue.set_shuffle`) is a
separate, ongoing mode and is not changed here.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from pixelfm.models import Song

#: Rejection-sampling budget. Every preference below can fail for at most
#: half of all orders once there are 2+ tracks, so 64 draws practically
#: always find an order that satisfies everything satisfiable.
MAX_ATTEMPTS = 64


def fisher_yates[T](items: Sequence[T], rng: random.Random) -> list[T]:
    """Return a uniformly random permutation of ``items`` (Durstenfeld's
    in-place Fisher-Yates: each of the n! orders has probability 1/n!)."""
    result = list(items)
    for i in range(len(result) - 1, 0, -1):
        j = rng.randrange(i + 1)  # 0 <= j <= i
        result[i], result[j] = result[j], result[i]
    return result


def unique_songs(songs: Sequence[Song]) -> list[Song]:
    """Drop repeated track ids, keeping the first occurrence."""
    seen: set[str] = set()
    result = []
    for song in songs:
        if song.id not in seen:
            seen.add(song.id)
            result.append(song)
    return result


def shuffle_songs(
    songs: Sequence[Song],
    rng: random.Random,
    *,
    avoid_first: str | None = None,
    previous: Sequence[str] = (),
) -> list[Song]:
    """Each track exactly once, in a fresh random order.

    When another order exists, these preferences apply (strongest first):

    1. don't start with ``avoid_first`` (the track playing now), so it is
       not heard twice in a row;
    2. differ from the playlist's own order;
    3. differ from ``previous`` (the last shuffle of this playlist).

    Orders are drawn with :func:`fisher_yates` and rejected until all
    preferences hold, so the result is uniform among the acceptable orders.
    If none is found (only possible for tiny playlists where the preferences
    conflict), the best order drawn wins: weaker preferences give way first.
    """
    songs = unique_songs(songs)
    if len(songs) < 2:
        return songs
    original = [s.id for s in songs]
    previous = list(previous)

    def violations(order: list[Song]) -> tuple[bool, bool, bool]:
        ids = [s.id for s in order]
        return (
            avoid_first is not None and ids[0] == avoid_first,
            ids == original,
            bool(previous) and ids == previous,
        )

    best: list[Song] | None = None
    best_score: tuple[bool, bool, bool] | None = None
    for _ in range(MAX_ATTEMPTS):
        candidate = fisher_yates(songs, rng)
        score = violations(candidate)
        if not any(score):
            return candidate
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    assert best is not None
    return best
