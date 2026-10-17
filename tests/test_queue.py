"""Play queue ordering, transitions, repeat, and shuffle."""

from __future__ import annotations

import random

from pixelfm.queue import PlayQueue, RepeatMode
from tests.conftest import make_song

S = [make_song(n) for n in range(1, 8)]


def titles(entries) -> list[str]:  # type: ignore[no-untyped-def]
    return [e.song.title for e in entries]


def queue_with(*songs, current: int | None = None) -> PlayQueue:  # type: ignore[no-untyped-def]
    q = PlayQueue(random.Random(7))
    for song in songs:
        q.add(song)
    if current is not None:
        q.select(q.entries[current].uid)
    return q


def test_add_skips_duplicates_of_upcoming() -> None:
    q = PlayQueue()
    assert q.add(S[0]) is not None
    assert q.add(S[0]) is None
    assert q.extend([S[0], S[1], S[1], S[2]]) == 2
    assert titles(q.upcoming) == ["Track 1", "Track 2", "Track 3"]


def test_play_now_inserts_after_current_and_keeps_upcoming() -> None:
    q = queue_with(S[0], S[1], S[2], current=0)
    entry = q.play_now(S[5])
    assert q.current == entry
    assert titles(q.entries) == ["Track 1", "Track 6", "Track 2", "Track 3"]
    assert titles(q.history) == ["Track 1"]
    assert titles(q.upcoming) == ["Track 2", "Track 3"]


def test_add_next_moves_existing_entry() -> None:
    q = queue_with(S[0], S[1], S[2], S[3], current=0)
    q.add_next(S[3])
    assert titles(q.upcoming) == ["Track 4", "Track 2", "Track 3"]
    q.add_next(S[6])
    assert titles(q.upcoming)[0] == "Track 7"


def test_play_many_starts_at_index() -> None:
    q = queue_with(S[0], current=0)
    q.add(S[6])
    entry = q.play_many(S[1:4], start=1)
    assert entry is not None and entry.song == S[2]
    assert titles(q.upcoming) == ["Track 4", "Track 7"]
    assert q.play_many([]) is None


def test_remove_cannot_remove_current() -> None:
    q = queue_with(S[0], S[1], S[2], current=1)
    current = q.current
    assert current is not None
    assert q.remove(current.uid) is False
    assert q.remove(q.entries[0].uid) is True  # history entry
    assert q.current == current
    assert q.remove(12345) is False


def test_move_within_upcoming_only() -> None:
    q = queue_with(S[0], S[1], S[2], S[3], current=0)
    last = q.entries[3]
    assert q.move(last.uid, -1)
    assert titles(q.upcoming) == ["Track 2", "Track 4", "Track 3"]
    assert q.move(last.uid, -10)  # clamped to top of upcoming
    assert titles(q.upcoming) == ["Track 4", "Track 2", "Track 3"]
    assert q.move(last.uid, -1) is False  # can't cross the current entry
    assert q.current is not None and q.move(q.current.uid, 1) is False


def test_mutations_preserve_current() -> None:
    q = queue_with(*S[:5], current=2)
    current = q.current
    q.move(q.entries[4].uid, -1)
    q.remove(q.entries[0].uid)
    q.add(S[6])
    q.add_next(S[5])
    q.set_shuffle(True)
    q.set_shuffle(False)
    assert q.current == current


def test_clear_keeps_current() -> None:
    q = queue_with(S[0], S[1], S[2], current=1)
    assert q.clear() == 2
    assert titles(q.entries) == ["Track 2"] and q.current is not None
    assert PlayQueue().clear() == 0


def test_advance_and_end() -> None:
    q = queue_with(S[0], S[1])
    assert q.advance(manual=True).song == S[0]  # nothing current: first
    assert q.advance(manual=False).song == S[1]
    assert q.advance(manual=False) is None
    assert q.current is not None and q.current.song == S[1]  # stays on last


def test_repeat_one_only_affects_natural_end() -> None:
    q = queue_with(S[0], S[1], current=0)
    q.repeat = RepeatMode.ONE
    assert q.advance(manual=False).song == S[0]
    assert q.advance(manual=True).song == S[1]


def test_repeat_all_wraps_both_ways() -> None:
    q = queue_with(S[0], S[1], S[2], current=2)
    q.repeat = RepeatMode.ALL
    assert q.advance(manual=False).song == S[0]
    assert q.previous().song == S[2]


def test_previous_without_repeat() -> None:
    q = queue_with(S[0], S[1], current=1)
    assert q.previous().song == S[0]
    assert q.previous() is None
    assert q.current is not None and q.current.song == S[0]


def test_cycle_repeat() -> None:
    q = PlayQueue()
    assert [q.cycle_repeat() for _ in range(3)] == [
        RepeatMode.ALL,
        RepeatMode.ONE,
        RepeatMode.OFF,
    ]


def test_shuffle_only_upcoming_and_restores_order() -> None:
    q = queue_with(*S, current=1)
    before = titles(q.entries)
    q.set_shuffle(True)
    assert q.shuffle
    assert titles(q.entries)[:2] == before[:2]  # history and current untouched
    assert sorted(titles(q.upcoming)) == sorted(before[2:])
    assert titles(q.upcoming) != before[2:]  # seeded RNG: deterministic shuffle
    q.set_shuffle(False)
    assert titles(q.entries) == before


def test_shuffle_is_deterministic_with_seed() -> None:
    a, b = queue_with(*S, current=0), queue_with(*S, current=0)
    a.set_shuffle(True)
    b.set_shuffle(True)
    assert titles(a.entries) == titles(b.entries)


def test_unshuffle_keeps_tracks_added_while_shuffled() -> None:
    q = queue_with(*S[:4], current=0)
    q.set_shuffle(True)
    q.add(S[6])
    q.set_shuffle(False)
    assert titles(q.upcoming) == ["Track 2", "Track 3", "Track 4", "Track 7"]


def test_track_end_for_stale_entry_is_ignored() -> None:
    """A late 'ended' for a track the user already skipped must not advance."""
    q = queue_with(S[0], S[1], S[2], current=0)
    first = q.current
    assert first is not None
    q.advance(manual=True)  # user pressed next: now on S[1]
    result = q.on_track_ended(first.uid)  # old track's end arrives late
    assert result.stale and result.entry is None
    assert q.current is not None and q.current.song == S[1]
    result = q.on_track_ended(q.current.uid)
    assert not result.stale and result.entry is not None
    assert result.entry.song == S[2]


def test_duplicate_songs_have_distinct_entries() -> None:
    q = queue_with(S[0], current=0)
    q.play_now(S[0])  # same song again, as a new entry
    uids = [e.uid for e in q.entries]
    assert len(set(uids)) == len(uids) == 2
