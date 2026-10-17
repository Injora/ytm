"""The play queue: ordered entries, a current item, repeat, and shuffle.

Entries before the current one are history, entries after it are upcoming.
The current item is tracked by entry ``uid``, never by index, so adding,
removing, reordering, or shuffling never changes what is playing.

Queue transitions after a track finishes go through :meth:`PlayQueue.on_track_ended`,
which ignores events for an entry that is no longer current. That makes a late
"ended" event from the player harmless if the user has already skipped.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pixelfm.models import Song


class RepeatMode(StrEnum):
    OFF = "off"
    ALL = "all"
    ONE = "one"

    def next(self) -> RepeatMode:
        order = [RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE]
        return order[(order.index(self) + 1) % len(order)]


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One slot in the queue. ``uid`` distinguishes repeated songs."""

    uid: int
    song: Song


@dataclass(frozen=True, slots=True)
class Advance:
    """Result of a natural track end."""

    entry: QueueEntry | None  # what to play next, if anything
    stale: bool = False  # the event was for an entry that is no longer current


class PlayQueue:
    """Session play queue. Pure Python; the UI and player depend on it."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._entries: list[QueueEntry] = []
        self._current: int | None = None
        self._uids = itertools.count(1)
        self._rng = rng or random.Random()
        self._unshuffled: list[int] = []
        self.repeat = RepeatMode.OFF
        self.shuffle = False

    # ------------------------------------------------------------ views

    @property
    def entries(self) -> tuple[QueueEntry, ...]:
        return tuple(self._entries)

    @property
    def current(self) -> QueueEntry | None:
        index = self.current_index
        return None if index is None else self._entries[index]

    @property
    def current_index(self) -> int | None:
        if self._current is None:
            return None
        return self._index(self._current)

    @property
    def upcoming(self) -> list[QueueEntry]:
        return self._entries[self._start() :]

    @property
    def history(self) -> list[QueueEntry]:
        index = self.current_index
        return [] if index is None else self._entries[:index]

    def upcoming_ids(self) -> set[str]:
        return {entry.song.id for entry in self.upcoming}

    def find(self, uid: int) -> QueueEntry | None:
        index = self._index(uid)
        return None if index is None else self._entries[index]

    # --------------------------------------------------------- internals

    def _index(self, uid: int) -> int | None:
        for i, entry in enumerate(self._entries):
            if entry.uid == uid:
                return i
        return None

    def _start(self) -> int:
        """Index of the first upcoming entry."""
        index = self.current_index
        return 0 if index is None else index + 1

    def _new(self, song: Song, *, track: bool = True) -> QueueEntry:
        entry = QueueEntry(next(self._uids), song)
        if track:
            self._unshuffled.append(entry.uid)
        return entry

    def _upcoming_entry_for(self, song: Song) -> QueueEntry | None:
        return next((e for e in self.upcoming if e.song.id == song.id), None)

    # ---------------------------------------------------------- mutation

    def add(self, song: Song) -> QueueEntry | None:
        """Append ``song``. Returns ``None`` if it is already upcoming."""
        if self._upcoming_entry_for(song) is not None:
            return None
        entry = self._new(song)
        self._entries.append(entry)
        return entry

    def extend(self, songs: Iterable[Song]) -> int:
        """Append several songs, skipping ones already upcoming. Returns count."""
        return sum(self.add(song) is not None for song in songs)

    def add_next(self, song: Song) -> QueueEntry:
        """Put ``song`` right after the current item (moving it if queued)."""
        existing = self._upcoming_entry_for(song)
        if existing is not None:
            self._entries.remove(existing)
            entry = existing
        else:
            entry = self._new(song)
        self._entries.insert(self._start(), entry)
        return entry

    def play_now(self, song: Song) -> QueueEntry:
        """Insert ``song`` after the current item and make it current."""
        entry = self.add_next(song)
        self._current = entry.uid
        return entry

    def play_many(self, songs: Sequence[Song], start: int = 0) -> QueueEntry | None:
        """Insert a list (e.g. a playlist, possibly shuffled) after the current
        item and play ``songs[start]``.

        Upcoming entries for the same tracks are dropped first, so playing a
        list twice never queues its tracks twice. Other upcoming entries stay
        queued after the list. The list's order also becomes its "unshuffled"
        order, so turning global shuffle off later keeps it as given.
        """
        if not songs:
            return None
        start = max(0, min(start, len(songs) - 1))
        ids = {song.id for song in songs}
        position = self._start()
        self._entries[position:] = [
            e for e in self._entries[position:] if e.song.id not in ids
        ]
        new = [self._new(song, track=False) for song in songs]
        self._entries[position:position] = new
        anchor = (
            self._unshuffled.index(self._current) + 1
            if self._current in self._unshuffled
            else len(self._unshuffled)
        )
        self._unshuffled[anchor:anchor] = [e.uid for e in new]
        self._current = new[start].uid
        return new[start]

    def select(self, uid: int) -> QueueEntry | None:
        """Make an existing entry current."""
        entry = self.find(uid)
        if entry is not None:
            self._current = uid
        return entry

    def remove(self, uid: int) -> bool:
        """Remove an entry. The current entry cannot be removed."""
        if uid == self._current:
            return False
        index = self._index(uid)
        if index is None:
            return False
        del self._entries[index]
        return True

    def move(self, uid: int, delta: int) -> bool:
        """Move an upcoming entry by ``delta`` places within the upcoming list."""
        index = self._index(uid)
        start = self._start()
        if index is None or index < start:
            return False
        target = max(start, min(len(self._entries) - 1, index + delta))
        if target == index:
            return False
        self._entries.insert(target, self._entries.pop(index))
        return True

    def clear(self) -> int:
        """Remove everything except the current entry. Returns count removed."""
        current = self.current
        removed = len(self._entries) - (1 if current else 0)
        self._entries = [current] if current else []
        self._unshuffled = [current.uid] if current else []
        return removed

    def restore(self, songs: Sequence[Song], current: int | None) -> None:
        """Replace the contents (e.g. from the saved queue) with new entries."""
        self._entries = [self._new(song) for song in songs]
        self._unshuffled = [entry.uid for entry in self._entries]
        valid = current is not None and 0 <= current < len(self._entries)
        self._current = self._entries[current].uid if valid else None  # type: ignore[index]

    # ------------------------------------------------------- transitions

    def peek_next(self, *, manual: bool) -> QueueEntry | None:
        """What would play next, without changing anything."""
        index = self.current_index
        if index is None:
            return self._entries[0] if self._entries else None
        if not manual and self.repeat is RepeatMode.ONE:
            return self._entries[index]
        if index + 1 < len(self._entries):
            return self._entries[index + 1]
        if self.repeat is not RepeatMode.OFF and self._entries:
            return self._entries[0]
        return None

    def advance(self, *, manual: bool) -> QueueEntry | None:
        """Move to the next entry (``manual`` ignores repeat-one)."""
        entry = self.peek_next(manual=manual)
        if entry is not None:
            self._current = entry.uid
        return entry

    def on_track_ended(self, uid: int) -> Advance:
        """Handle a real end-of-track event for entry ``uid``."""
        if uid != self._current:
            return Advance(None, stale=True)
        return Advance(self.advance(manual=False))

    def previous(self) -> QueueEntry | None:
        """Move to the previous entry (wrapping with repeat-all)."""
        index = self.current_index
        if index is None:
            return None
        if index > 0:
            entry = self._entries[index - 1]
        elif self.repeat is not RepeatMode.OFF and len(self._entries) > 1:
            entry = self._entries[-1]
        else:
            return None
        self._current = entry.uid
        return entry

    # ----------------------------------------------------- repeat/shuffle

    def cycle_repeat(self) -> RepeatMode:
        self.repeat = self.repeat.next()
        return self.repeat

    def set_shuffle(self, enabled: bool) -> None:
        """Shuffle or restore the order of upcoming entries."""
        if enabled == self.shuffle:
            return
        start = self._start()
        upcoming = self._entries[start:]
        if enabled:
            self._unshuffled = [e.uid for e in self._entries]
            self._rng.shuffle(upcoming)
        else:
            rank = {uid: i for i, uid in enumerate(self._unshuffled)}
            upcoming.sort(key=lambda e: rank.get(e.uid, len(rank) + e.uid))
        self._entries[start:] = upcoming
        self.shuffle = enabled
