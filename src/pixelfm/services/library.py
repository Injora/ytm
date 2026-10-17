"""Persistent music library: favorites, history, playlists, preferences.

SQLite via the standard library. The schema is versioned with
``PRAGMA user_version`` and upgraded by ordered migrations, each applied in a
single transaction. Before upgrading an existing database a backup copy is
written next to it, and a database from a newer PixelFM is refused rather than
modified.

Only stable source identifiers (YouTube video IDs) and display metadata are
stored. Stream URLs are temporary and are never persisted.

All queries are parameterized. Operations are small and run on the UI thread;
each is well under a millisecond on a local disk.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixelfm.models import Song, SongValidationError

log = logging.getLogger(__name__)

MAX_PLAYLIST_NAME = 80
HISTORY_LIMIT = 1000


class LibraryError(Exception):
    """A user-facing library problem (bad name, unreadable database, ...)."""


# ---------------------------------------------------------------- migrations

#: Ordered (version, description, statements). Never edit a released entry;
#: append a new one instead.
MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "initial schema",
        (
            """CREATE TABLE tracks (
                id            TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                artist        TEXT NOT NULL,
                duration      INTEGER NOT NULL DEFAULT 0,
                source_url    TEXT NOT NULL,
                thumbnail_url TEXT,
                updated_at    REAL NOT NULL
            )""",
            """CREATE TABLE favorites (
                track_id TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
                added_at REAL NOT NULL
            )""",
            """CREATE TABLE history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id  TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                played_at REAL NOT NULL
            )""",
            """CREATE TABLE playlists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE playlist_tracks (
                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                position    INTEGER NOT NULL,
                track_id    TEXT NOT NULL REFERENCES tracks(id),
                added_at    REAL NOT NULL,
                PRIMARY KEY (playlist_id, position),
                UNIQUE (playlist_id, track_id)
            )""",
            """CREATE TABLE preferences (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""",
        ),
    ),
    (
        2,
        "track availability and history index",
        (
            "ALTER TABLE tracks ADD COLUMN unavailable_reason TEXT",
            "ALTER TABLE tracks ADD COLUMN unavailable_at REAL",
            "CREATE INDEX history_played_at ON history(played_at)",
            "CREATE INDEX history_track ON history(track_id)",
        ),
    ),
    (
        3,
        "play counts, downloads, and the saved queue",
        (
            "ALTER TABLE tracks ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tracks ADD COLUMN last_played_at REAL",
            # Backfill from the existing (capped) history.
            """UPDATE tracks SET
                   play_count = (SELECT COUNT(*) FROM history h
                                 WHERE h.track_id = tracks.id),
                   last_played_at = (SELECT MAX(played_at) FROM history h
                                     WHERE h.track_id = tracks.id)""",
            """CREATE TABLE downloads (
                track_id      TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
                path          TEXT NOT NULL,
                size          INTEGER NOT NULL,
                format        TEXT,
                downloaded_at REAL NOT NULL
            )""",
            """CREATE TABLE queue_entries (
                position   INTEGER PRIMARY KEY,
                track_id   TEXT NOT NULL REFERENCES tracks(id),
                is_current INTEGER NOT NULL DEFAULT 0
            )""",
        ),
    ),
    (
        4,
        "session snapshot",
        (
            # One row: the playback session (position, view, selections) as
            # JSON. Written in the same transaction as queue_entries.
            """CREATE TABLE session (
                id       INTEGER PRIMARY KEY CHECK (id = 1),
                data     TEXT NOT NULL,
                saved_at REAL NOT NULL
            )""",
            """INSERT INTO session (id, data, saved_at)
               SELECT 1, json_object('position', json_extract(value, '$')), 0
               FROM preferences WHERE key = 'queue_position'
                 AND json_valid(value) AND json_type(value) IN ('integer', 'real')""",
            "DELETE FROM preferences WHERE key = 'queue_position'",
        ),
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1][0]


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def backup_database(conn: sqlite3.Connection, target: Path) -> None:
    """Consistent online copy of the database (SQLite backup API)."""
    with sqlite3.connect(target) as dest:
        conn.backup(dest)
    dest.close()


def migrate(conn: sqlite3.Connection, path: Path | None = None) -> list[int]:
    """Bring the schema up to :data:`SCHEMA_VERSION`. Returns versions applied.

    Raises:
        LibraryError: If the database is newer than this PixelFM supports.
    """
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise LibraryError(
            f"The library was written by a newer PixelFM (schema {current}; this "
            f"version supports {SCHEMA_VERSION}). Upgrade PixelFM; your data was "
            "not modified."
        )
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return []
    if current > 0 and path is not None:
        backup = path.with_name(f"{path.name}.v{current}.bak")
        backup_database(conn, backup)
        log.info("backed up library schema %s to %s", current, backup)
    applied = []
    for version, description, statements in pending:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                conn.execute(statement)
            # PRAGMA cannot take parameters; version is an int from MIGRATIONS.
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        log.info("library migrated to schema %s (%s)", version, description)
        applied.append(version)
    return applied


# -------------------------------------------------------------------- models


@dataclass(frozen=True, slots=True)
class LibraryTrack:
    """A song as stored in the library, with availability information."""

    song: Song
    unavailable_reason: str | None = None
    timestamp: float | None = None  # added_at or played_at, depending on view
    play_count: int = 0

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    """A completed download. ``path`` may have gone missing since."""

    track: LibraryTrack
    path: Path
    size: int
    format: str | None
    downloaded_at: float

    @property
    def song(self) -> Song:
        return self.track.song


@dataclass(frozen=True, slots=True)
class SavedQueue:
    """Queue contents persisted between sessions.

    ``session`` holds the rest of the snapshot (view, selections, playback
    status, ...) as plain JSON values; callers validate each field.
    """

    songs: list[Song]
    current: int | None
    position: float
    session: dict[str, Any] = field(default_factory=dict)


LIKED_ORDERS = {
    "added": "f.added_at DESC, t.title",
    "title": "t.title COLLATE NOCASE, t.artist COLLATE NOCASE",
    "artist": "t.artist COLLATE NOCASE, t.title COLLATE NOCASE",
}


@dataclass(frozen=True, slots=True)
class Playlist:
    id: int
    name: str
    count: int
    created_at: float
    updated_at: float


def clean_playlist_name(name: str) -> str:
    """Validate and normalise a playlist name."""
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise LibraryError("Playlist name cannot be empty.")
    if len(cleaned) > MAX_PLAYLIST_NAME:
        raise LibraryError(
            f"Playlist name is longer than {MAX_PLAYLIST_NAME} characters."
        )
    if any(ord(ch) < 32 for ch in cleaned):
        raise LibraryError("Playlist name contains control characters.")
    return cleaned


# ------------------------------------------------------------------- library

_TRACK_COLUMNS = (
    "t.id, t.title, t.artist, t.duration, t.source_url, t.thumbnail_url, "
    "t.unavailable_reason, t.play_count"
)


class Library:
    """Persistent library backed by one SQLite connection."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._conn = conn
        self.path = path
        self._clock = clock

    # -- opening -------------------------------------------------------

    @classmethod
    def open(cls, path: Path, clock: Callable[[], float] = time.time) -> Library:
        """Open (creating if needed) and migrate the library at ``path``.

        Raises:
            LibraryError: If the file is not a usable database. The file is
                left untouched.
        """
        conn: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, isolation_level=None)
            # Read-only checks first: nothing is written to a file we refuse.
            cls._configure(conn, wal=False)
            migrate(conn, path)
            conn.execute("PRAGMA journal_mode = WAL")
        except (LibraryError, sqlite3.Error, OSError) as exc:
            if conn is not None:
                conn.close()
            if isinstance(exc, LibraryError):
                raise
            raise LibraryError(f"Could not open the library at {path}: {exc}") from exc
        return cls(conn, path, clock)

    @classmethod
    def memory(cls, clock: Callable[[], float] = time.time) -> Library:
        """A throwaway in-memory library (tests, or when the file is unusable)."""
        conn = sqlite3.connect(":memory:", isolation_level=None)
        cls._configure(conn, wal=False)
        migrate(conn)
        return cls(conn, None, clock)

    @staticmethod
    def _configure(conn: sqlite3.Connection, wal: bool) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 3000")
        if wal:
            conn.execute("PRAGMA journal_mode = WAL")
        # Fail fast on a corrupt or non-SQLite file.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()

    def close(self) -> None:
        self._conn.close()

    @property
    def persistent(self) -> bool:
        return self.path is not None

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One atomic transaction for related updates."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    # -- tracks --------------------------------------------------------

    def _upsert(self, conn: sqlite3.Connection, song: Song) -> None:
        """Insert or refresh a track's metadata. Never stores stream URLs."""
        conn.execute(
            """INSERT INTO tracks
                   (id, title, artist, duration, source_url, thumbnail_url, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   title = excluded.title,
                   artist = excluded.artist,
                   duration = CASE WHEN excluded.duration > 0
                                   THEN excluded.duration ELSE tracks.duration END,
                   source_url = excluded.source_url,
                   thumbnail_url = COALESCE(excluded.thumbnail_url,
                                            tracks.thumbnail_url),
                   updated_at = excluded.updated_at""",
            (
                song.id,
                song.title,
                song.artist,
                song.duration,
                song.source_url,
                song.thumbnail_url,
                self._clock(),
            ),
        )

    def _track(
        self, row: sqlite3.Row, timestamp: float | None = None
    ) -> LibraryTrack | None:
        """Row -> LibraryTrack, skipping rows whose metadata is malformed."""
        try:
            song = Song(
                id=row["id"],
                title=row["title"],
                artist=row["artist"],
                duration=row["duration"],
                source_url=row["source_url"],
                thumbnail_url=row["thumbnail_url"],
            )
        except (SongValidationError, TypeError, IndexError, KeyError) as exc:
            log.warning("skipping malformed library track %r: %s", row["id"], exc)
            return None
        return LibraryTrack(
            song, row["unavailable_reason"], timestamp, row["play_count"] or 0
        )

    def _tracks(
        self, rows: Sequence[sqlite3.Row], ts: str | None = None
    ) -> list[LibraryTrack]:
        tracks = (self._track(row, row[ts] if ts else None) for row in rows)
        return [t for t in tracks if t is not None]

    def mark_unavailable(self, song: Song, reason: str) -> None:
        with self._tx() as conn:
            self._upsert(conn, song)
            conn.execute(
                "UPDATE tracks SET unavailable_reason = ?, unavailable_at = ? "
                "WHERE id = ?",
                (reason[:200], self._clock(), song.id),
            )

    def mark_available(self, song_id: str) -> None:
        self._conn.execute(
            "UPDATE tracks SET unavailable_reason = NULL, unavailable_at = NULL "
            "WHERE id = ? AND unavailable_reason IS NOT NULL",
            (song_id,),
        )

    def unavailable_ids(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT id FROM tracks WHERE unavailable_reason IS NOT NULL"
        ).fetchall()
        return {row["id"] for row in rows}

    # -- favorites -----------------------------------------------------

    def favorite_ids(self) -> set[str]:
        return {row[0] for row in self._conn.execute("SELECT track_id FROM favorites")}

    def is_favorite(self, song_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM favorites WHERE track_id = ?", (song_id,)
        ).fetchone()
        return row is not None

    def add_favorite(self, song: Song) -> bool:
        """Favorite ``song``. Returns ``False`` if it already was."""
        with self._tx() as conn:
            self._upsert(conn, song)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO favorites (track_id, added_at) VALUES (?, ?)",
                (song.id, self._clock()),
            )
        return cursor.rowcount == 1

    def remove_favorite(self, song_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM favorites WHERE track_id = ?", (song_id,)
        )
        return cursor.rowcount == 1

    def toggle_favorite(self, song: Song) -> bool:
        """Flip favorite status; returns the new status."""
        if self.remove_favorite(song.id):
            return False
        self.add_favorite(song)
        return True

    def favorites(self, order: str = "added") -> list[LibraryTrack]:
        """Liked tracks sorted by ``added`` (newest first), ``title``, or ``artist``."""
        if order not in LIKED_ORDERS:
            raise LibraryError(f"Unknown sort order {order!r}.")
        rows = self._conn.execute(
            f"""SELECT {_TRACK_COLUMNS}, f.added_at FROM favorites f
                JOIN tracks t ON t.id = f.track_id
                ORDER BY {LIKED_ORDERS[order]}"""  # order is from a fixed allow-list
        ).fetchall()
        return self._tracks(rows, "added_at")

    # -- history -------------------------------------------------------

    def record_play(self, song: Song) -> None:
        """Log that ``song`` started playing; keeps the newest entries only."""
        with self._tx() as conn:
            self._upsert(conn, song)
            now = self._clock()
            conn.execute(
                "INSERT INTO history (track_id, played_at) VALUES (?, ?)",
                (song.id, now),
            )
            conn.execute(
                "UPDATE tracks SET play_count = play_count + 1, last_played_at = ? "
                "WHERE id = ?",
                (now, song.id),
            )
            conn.execute(
                """DELETE FROM history WHERE id NOT IN (
                       SELECT id FROM history ORDER BY played_at DESC, id DESC LIMIT ?
                   )""",
                (HISTORY_LIMIT,),
            )
        self.mark_available(song.id)

    def recent(self, limit: int = 100) -> list[LibraryTrack]:
        """Distinct tracks, most recently played first, with play counts."""
        rows = self._conn.execute(
            f"""SELECT {_TRACK_COLUMNS}, t.last_played_at AS played_at
                FROM tracks t WHERE t.last_played_at IS NOT NULL
                ORDER BY t.last_played_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return self._tracks(rows, "played_at")

    def play_count(self, song_id: str) -> int:
        row = self._conn.execute(
            "SELECT play_count FROM tracks WHERE id = ?", (song_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def clear_history(self) -> int:
        """Forget play history and counts. Likes, playlists, downloads stay."""
        with self._tx() as conn:
            removed = conn.execute("DELETE FROM history").rowcount
            conn.execute("UPDATE tracks SET play_count = 0, last_played_at = NULL")
        return removed

    # -- downloads -----------------------------------------------------

    def add_download(
        self, song: Song, path: Path, size: int, format: str | None = None
    ) -> None:
        """Record a *completed* download (callers verify the file first)."""
        with self._tx() as conn:
            self._upsert(conn, song)
            conn.execute(
                """INSERT INTO downloads (track_id, path, size, format, downloaded_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(track_id) DO UPDATE SET path = excluded.path,
                       size = excluded.size, format = excluded.format,
                       downloaded_at = excluded.downloaded_at""",
                (song.id, str(path), int(size), format, self._clock()),
            )

    def downloads(self) -> list[DownloadRecord]:
        rows = self._conn.execute(
            f"""SELECT {_TRACK_COLUMNS}, d.path, d.size, d.format, d.downloaded_at
                FROM downloads d JOIN tracks t ON t.id = d.track_id
                ORDER BY d.downloaded_at DESC"""
        ).fetchall()
        records = []
        for row in rows:
            track = self._track(row, row["downloaded_at"])
            if track is not None:
                records.append(
                    DownloadRecord(
                        track,
                        Path(row["path"]),
                        row["size"],
                        row["format"],
                        row["downloaded_at"],
                    )
                )
        return records

    def download_paths(self) -> dict[str, Path]:
        """``song id -> recorded file path`` for every download."""
        rows = self._conn.execute("SELECT track_id, path FROM downloads")
        return {row[0]: Path(row[1]) for row in rows}

    def remove_download(self, song_id: str) -> bool:
        """Forget a download record. Never deletes files (callers decide)."""
        cursor = self._conn.execute(
            "DELETE FROM downloads WHERE track_id = ?", (song_id,)
        )
        return cursor.rowcount == 1

    def relink_download(self, song_id: str, path: Path, size: int) -> bool:
        cursor = self._conn.execute(
            "UPDATE downloads SET path = ?, size = ? WHERE track_id = ?",
            (str(path), int(size), song_id),
        )
        return cursor.rowcount == 1

    # -- saved queue ---------------------------------------------------

    def save_queue(
        self,
        songs: Sequence[Song],
        current: int | None,
        position: float,
        session: Mapping[str, Any] | None = None,
    ) -> None:
        """Replace the saved queue and session snapshot in one transaction,
        so a crash can never leave a half-written queue."""
        with self._tx() as conn:
            for song in songs:
                self._upsert(conn, song)
            conn.execute("DELETE FROM queue_entries")
            conn.executemany(
                "INSERT INTO queue_entries (position, track_id, is_current) "
                "VALUES (?, ?, ?)",
                [(i, song.id, int(i == current)) for i, song in enumerate(songs)],
            )
            self._write_session(conn, position, session)

    def save_session(
        self, position: float, session: Mapping[str, Any] | None = None
    ) -> None:
        """Update only the session snapshot (the queue is unchanged)."""
        with self._tx() as conn:
            self._write_session(conn, position, session)

    def _write_session(
        self,
        conn: sqlite3.Connection,
        position: float,
        session: Mapping[str, Any] | None,
    ) -> None:
        data = {**(session or {}), "position": round(max(0.0, float(position)), 1)}
        conn.execute(
            "INSERT INTO session (id, data, saved_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, "
            "saved_at = excluded.saved_at",
            (json.dumps(data), self._clock()),
        )

    def load_session(self) -> dict[str, Any]:
        """The saved session snapshot; ``{}`` if missing or unreadable."""
        row = self._conn.execute("SELECT data FROM session WHERE id = 1").fetchone()
        if row is None:
            return {}
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            log.warning("ignoring unreadable session snapshot")
            return {}
        return data if isinstance(data, dict) else {}

    def load_queue(self) -> SavedQueue:
        rows = self._conn.execute(
            f"""SELECT {_TRACK_COLUMNS}, q.is_current FROM queue_entries q
                JOIN tracks t ON t.id = q.track_id ORDER BY q.position"""
        ).fetchall()
        songs: list[Song] = []
        current = None
        for row in rows:
            track = self._track(row)
            if track is None:
                continue  # malformed rows are dropped, the rest survives
            if row["is_current"]:
                current = len(songs)
            songs.append(track.song)
        session = self.load_session()
        position = session.pop("position", 0.0)
        if not isinstance(position, int | float) or isinstance(position, bool):
            position = 0.0
        if not math.isfinite(position):
            position = 0.0
        return SavedQueue(
            songs,
            current,
            max(0.0, float(position)) if current is not None else 0.0,
            session,
        )

    # -- playlists -----------------------------------------------------

    def playlists(self) -> list[Playlist]:
        rows = self._conn.execute(
            """SELECT p.id, p.name, p.created_at, p.updated_at,
                      COUNT(pt.track_id) AS count
               FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
               GROUP BY p.id ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()
        return [
            Playlist(r["id"], r["name"], r["count"], r["created_at"], r["updated_at"])
            for r in rows
        ]

    def playlist(self, playlist_id: int) -> Playlist:
        for playlist in self.playlists():
            if playlist.id == playlist_id:
                return playlist
        raise LibraryError("That playlist no longer exists.")

    def create_playlist(self, name: str) -> Playlist:
        name = clean_playlist_name(name)
        now = self._clock()
        try:
            cursor = self._conn.execute(
                "INSERT INTO playlists (name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, now, now),
            )
        except sqlite3.IntegrityError:
            raise LibraryError(f"A playlist named “{name}” already exists.") from None
        return Playlist(int(cursor.lastrowid or 0), name, 0, now, now)

    def rename_playlist(self, playlist_id: int, name: str) -> Playlist:
        name = clean_playlist_name(name)
        try:
            cursor = self._conn.execute(
                "UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?",
                (name, self._clock(), playlist_id),
            )
        except sqlite3.IntegrityError:
            raise LibraryError(f"A playlist named “{name}” already exists.") from None
        if cursor.rowcount != 1:
            raise LibraryError("That playlist no longer exists.")
        return self.playlist(playlist_id)

    def delete_playlist(self, playlist_id: int) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM playlists WHERE id = ?", (playlist_id,)
        )
        return cursor.rowcount == 1

    def playlist_tracks(self, playlist_id: int) -> list[LibraryTrack]:
        rows = self._conn.execute(
            f"""SELECT {_TRACK_COLUMNS}, pt.added_at FROM playlist_tracks pt
                JOIN tracks t ON t.id = pt.track_id
                WHERE pt.playlist_id = ? ORDER BY pt.position""",
            (playlist_id,),
        ).fetchall()
        return self._tracks(rows, "added_at")

    def _playlist_ids(self, conn: sqlite3.Connection, playlist_id: int) -> list[str]:
        if (
            conn.execute(
                "SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
            is None
        ):
            raise LibraryError("That playlist no longer exists.")
        rows = conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? "
            "ORDER BY position",
            (playlist_id,),
        )
        return [row[0] for row in rows]

    def _write_order(
        self, conn: sqlite3.Connection, playlist_id: int, track_ids: Sequence[str]
    ) -> None:
        """Rewrite a playlist's order (inside the caller's transaction)."""
        added = dict(
            conn.execute(
                "SELECT track_id, added_at FROM playlist_tracks WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchall()
        )
        now = self._clock()
        conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
        )
        conn.executemany(
            "INSERT INTO playlist_tracks (playlist_id, position, track_id, added_at) "
            "VALUES (?, ?, ?, ?)",
            [
                (playlist_id, i, tid, added.get(tid, now))
                for i, tid in enumerate(track_ids)
            ],
        )
        conn.execute(
            "UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id)
        )

    def add_to_playlist(self, playlist_id: int, song: Song) -> bool:
        """Append ``song``. Returns ``False`` if it is already in the playlist."""
        with self._tx() as conn:
            ids = self._playlist_ids(conn, playlist_id)
            if song.id in ids:
                return False
            self._upsert(conn, song)
            self._write_order(conn, playlist_id, [*ids, song.id])
        return True

    def add_many_to_playlist(self, playlist_id: int, songs: Sequence[Song]) -> int:
        """Append several songs atomically, skipping duplicates. Returns count added."""
        with self._tx() as conn:
            ids = self._playlist_ids(conn, playlist_id)
            new = []
            for song in songs:
                if song.id not in ids and song.id not in new:
                    self._upsert(conn, song)
                    new.append(song.id)
            if new:
                self._write_order(conn, playlist_id, [*ids, *new])
        return len(new)

    def remove_from_playlist(self, playlist_id: int, song_id: str) -> bool:
        with self._tx() as conn:
            ids = self._playlist_ids(conn, playlist_id)
            if song_id not in ids:
                return False
            ids.remove(song_id)
            self._write_order(conn, playlist_id, ids)
        return True

    def move_in_playlist(
        self, playlist_id: int, song_id: str, delta: int
    ) -> int | None:
        """Move a track by ``delta`` places. Returns its new index, or ``None``."""
        with self._tx() as conn:
            ids = self._playlist_ids(conn, playlist_id)
            if song_id not in ids:
                return None
            old = ids.index(song_id)
            new = max(0, min(len(ids) - 1, old + delta))
            if new == old:
                return None
            ids.insert(new, ids.pop(old))
            self._write_order(conn, playlist_id, ids)
        return new

    # -- preferences ---------------------------------------------------

    def get_pref(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            log.warning("ignoring malformed preference %r", key)
            return default

    def set_pref(self, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT INTO preferences (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
