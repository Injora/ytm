"""SQLite library: schema, migrations, favorites, history, playlists, prefs."""

from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path

import pytest

from pixelfm.paths import cache_dir, data_dir, library_path
from pixelfm.services.library import (
    MIGRATIONS,
    SCHEMA_VERSION,
    Library,
    LibraryError,
    migrate,
    schema_version,
)
from tests.conftest import make_song

A, B, C = make_song(1), make_song(2), make_song(3)


def clock():  # type: ignore[no-untyped-def]
    ticks = itertools.count(1000)
    return lambda: float(next(ticks))


@pytest.fixture
def lib() -> Library:
    return Library.memory(clock=clock())


def tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in rows if not r[0].startswith("sqlite_")}


# ------------------------------------------------------------------- paths


def test_storage_locations_are_separate() -> None:
    env = {"PIXELFM_DATA_DIR": "/d", "PIXELFM_CACHE_DIR": "/c"}
    assert data_dir(env) == Path("/d") and cache_dir(env) == Path("/c")
    assert library_path(env) == Path("/d/library.sqlite3")


def test_default_macos_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    assert data_dir({}) == Path.home() / "Library/Application Support/PixelFM"
    assert cache_dir({}) == Path.home() / "Library/Caches/PixelFM"


# -------------------------------------------------------------- migrations


def test_create_from_empty_database(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "library.sqlite3"
    library = Library.open(path)
    conn = sqlite3.connect(path)
    assert schema_version(conn) == SCHEMA_VERSION
    assert {
        "tracks",
        "favorites",
        "history",
        "playlists",
        "playlist_tracks",
        "preferences",
    } <= tables(conn)
    assert not list(tmp_path.glob("nested/*.bak"))  # nothing to back up
    library.close()
    conn.close()


def test_reopening_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    Library.open(path).add_favorite(A)
    library = Library.open(path)
    assert library.favorite_ids() == {A.id}


def test_upgrade_v1_to_latest_keeps_data_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    conn = sqlite3.connect(path, isolation_level=None)
    # Build a version-1 database by hand, as an older PixelFM would have.
    conn.execute("BEGIN")
    for statement in MIGRATIONS[0][2]:
        conn.execute(statement)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?)",
        (A.id, A.title, A.artist, A.duration, A.source_url, A.thumbnail_url, 1.0),
    )
    conn.execute("INSERT INTO favorites VALUES (?, ?)", (A.id, 2.0))
    conn.execute(
        "INSERT INTO playlists (name, created_at, updated_at) VALUES ('Old', 1, 1)"
    )
    conn.execute("INSERT INTO playlist_tracks VALUES (1, 0, ?, 1)", (A.id,))
    conn.execute("COMMIT")
    conn.close()

    library = Library.open(path)
    assert library.favorite_ids() == {A.id}
    assert [t.song for t in library.playlist_tracks(1)] == [A]
    assert library.unavailable_ids() == set()  # new v2 column works

    backup = path.with_name("library.sqlite3.v1.bak")
    assert backup.exists()
    with sqlite3.connect(backup) as old:
        assert schema_version(old) == 1
        assert old.execute("SELECT count(*) FROM favorites").fetchone()[0] == 1


def test_failed_migration_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = sqlite3.connect(tmp_path / "x.sqlite3", isolation_level=None)
    broken = (
        *MIGRATIONS,
        (SCHEMA_VERSION + 1, "broken", ("CREATE TABLE ok (x)", "NOT SQL")),
    )
    monkeypatch.setattr("pixelfm.services.library.MIGRATIONS", broken)
    monkeypatch.setattr("pixelfm.services.library.SCHEMA_VERSION", SCHEMA_VERSION + 1)
    with pytest.raises(sqlite3.Error):
        migrate(conn)
    assert schema_version(conn) == SCHEMA_VERSION  # earlier migrations kept
    assert "ok" not in tables(conn)  # the broken one fully rolled back


def test_newer_schema_is_refused_untouched(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    before = path.read_bytes()
    with pytest.raises(LibraryError, match="newer PixelFM"):
        Library.open(path)
    assert path.read_bytes() == before


def test_corrupt_file_is_refused_untouched(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(LibraryError):
        Library.open(path)
    assert path.read_bytes() == b"this is not a database" * 100


# --------------------------------------------------------------- favorites


def test_favorites(lib: Library) -> None:
    assert lib.add_favorite(A) is True
    assert lib.add_favorite(A) is False  # duplicate addition
    lib.add_favorite(B)
    assert [t.song for t in lib.favorites()] == [B, A]  # newest first
    assert lib.toggle_favorite(A) is False
    assert lib.favorite_ids() == {B.id}
    assert lib.toggle_favorite(A) is True
    assert lib.is_favorite(A.id)
    assert lib.remove_favorite(C.id) is False


def test_metadata_is_refreshed_but_not_degraded(lib: Library) -> None:
    lib.add_favorite(A)
    renamed = make_song(1, duration=0, thumbnail=False)
    lib.record_play(renamed)
    stored = lib.favorites()[0].song
    assert stored.duration == A.duration  # unknown duration doesn't erase it
    assert stored.thumbnail_url == A.thumbnail_url


# ----------------------------------------------------------------- history


def test_recent_is_distinct_and_newest_first(lib: Library) -> None:
    for song in (A, B, A, C):
        lib.record_play(song)
    recent = lib.recent()
    assert [t.song for t in recent] == [C, A, B]
    assert recent[0].timestamp is not None
    assert lib.clear_history() == 4
    assert lib.recent() == []


def test_history_is_capped(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pixelfm.services.library.HISTORY_LIMIT", 3)
    for n in range(6):
        lib.record_play(make_song(n + 10))
    rows = lib._conn.execute("SELECT count(*) FROM history").fetchone()[0]
    assert rows == 3


# --------------------------------------------------------------- playlists


def test_playlist_crud(lib: Library) -> None:
    mix = lib.create_playlist("  Night   Drive ")
    assert mix.name == "Night Drive"
    with pytest.raises(LibraryError, match="already exists"):
        lib.create_playlist("night drive")  # case-insensitive unique
    with pytest.raises(LibraryError):
        lib.create_playlist("   ")
    with pytest.raises(LibraryError):
        lib.create_playlist("x" * 81)
    renamed = lib.rename_playlist(mix.id, "Late Drive")
    assert renamed.name == "Late Drive"
    assert [p.name for p in lib.playlists()] == ["Late Drive"]
    assert lib.delete_playlist(mix.id) is True
    assert lib.playlists() == []
    with pytest.raises(LibraryError):
        lib.rename_playlist(mix.id, "Gone")


def test_playlist_tracks_order_and_duplicates(lib: Library) -> None:
    pid = lib.create_playlist("Mix").id
    assert lib.add_to_playlist(pid, A)
    assert lib.add_to_playlist(pid, B)
    assert lib.add_to_playlist(pid, A) is False  # duplicate addition
    assert lib.add_many_to_playlist(pid, [B, C, C]) == 1
    order = lambda: [t.song.id for t in lib.playlist_tracks(pid)]  # noqa: E731
    assert order() == [A.id, B.id, C.id]
    assert lib.move_in_playlist(pid, C.id, -2) == 0
    assert order() == [C.id, A.id, B.id]
    assert lib.move_in_playlist(pid, C.id, -1) is None  # already first
    assert lib.remove_from_playlist(pid, A.id)
    assert order() == [C.id, B.id]
    assert lib.playlist(pid).count == 2


def test_playlist_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    library = Library.open(path)
    pid = library.create_playlist("Keep").id
    for song in (C, A, B):
        library.add_to_playlist(pid, song)
    library.move_in_playlist(pid, B.id, -1)
    library.close()
    reopened = Library.open(path)
    assert [t.song for t in reopened.playlist_tracks(pid)] == [C, B, A]


def test_failed_update_rolls_back(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = lib.create_playlist("Mix").id
    lib.add_to_playlist(pid, A)

    def explode(*args: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(lib, "_write_order", explode)
    with pytest.raises(RuntimeError):
        lib.add_to_playlist(pid, B)
    assert [t.song for t in lib.playlist_tracks(pid)] == [A]
    assert B.id not in {r[0] for r in lib._conn.execute("SELECT id FROM tracks")}


def test_deleting_playlist_keeps_favorites(lib: Library) -> None:
    pid = lib.create_playlist("Mix").id
    lib.add_to_playlist(pid, A)
    lib.add_favorite(A)
    lib.delete_playlist(pid)
    assert lib.favorite_ids() == {A.id}


def test_missing_playlist_errors(lib: Library) -> None:
    with pytest.raises(LibraryError):
        lib.add_to_playlist(999, A)
    with pytest.raises(LibraryError):
        lib.playlist(999)


# ----------------------------------------------------- availability, prefs


def test_unavailable_tracks(lib: Library) -> None:
    lib.add_favorite(A)
    lib.mark_unavailable(B, "This video is unavailable.")
    assert lib.unavailable_ids() == {B.id}
    lib.add_favorite(B)
    flags = {t.song.id: t.available for t in lib.favorites()}
    assert flags == {A.id: True, B.id: False}
    lib.record_play(B)  # it played again: available
    assert lib.unavailable_ids() == set()


def test_preferences(lib: Library) -> None:
    assert lib.get_pref("volume", 80) == 80
    lib.set_pref("volume", 35)
    lib.set_pref("repeat", "all")
    lib.set_pref("volume", 40)
    assert lib.get_pref("volume") == 40 and lib.get_pref("repeat") == "all"
    lib._conn.execute("UPDATE preferences SET value = '{bad' WHERE key = 'repeat'")
    assert lib.get_pref("repeat", "off") == "off"


def test_malformed_rows_are_skipped(lib: Library) -> None:
    lib.add_favorite(A)
    # Simulate a hand-edited or corrupted row.
    lib._conn.execute(
        "INSERT INTO tracks (id, title, artist, duration, source_url, updated_at) "
        "VALUES ('bad id!', '', 'x', -5, 'not a url', 0)"
    )
    lib._conn.execute("INSERT INTO favorites VALUES ('bad id!', 99999)")
    assert [t.song for t in lib.favorites()] == [A]


def test_stream_urls_are_never_stored(lib: Library) -> None:
    lib.add_favorite(A)
    lib.record_play(B)
    dump = "\n".join(lib._conn.iterdump())
    assert "googlevideo" not in dump and "videoplayback" not in dump
    assert A.source_url in dump  # the stable source URL is kept


def test_queries_are_parameterized(lib: Library) -> None:
    evil = "Robert'); DROP TABLE favorites;--"
    pid = lib.create_playlist(evil).id
    assert lib.playlist(pid).name == evil
    lib.add_favorite(A)
    assert lib.favorite_ids() == {A.id}


# ------------------------------------------------------------ schema v3


def _build_v2(path: Path) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("BEGIN")
    for _version, _, statements in MIGRATIONS[:2]:
        for statement in statements:
            conn.execute(statement)
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO tracks (id, title, artist, duration, source_url, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (A.id, A.title, A.artist, A.duration, A.source_url),
    )
    for at in (10.0, 20.0, 30.0):
        conn.execute(
            "INSERT INTO history (track_id, played_at) VALUES (?, ?)", (A.id, at)
        )
    conn.execute("INSERT INTO favorites VALUES (?, 5)", (A.id,))
    conn.execute("COMMIT")
    conn.close()


def test_upgrade_v2_to_current_backfills_play_counts(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    _build_v2(path)
    library = Library.open(path)
    assert schema_version(library._conn) == SCHEMA_VERSION == 6
    [recent] = library.recent()
    assert recent.play_count == 3 and recent.timestamp == 30.0
    assert library.favorite_ids() == {A.id}
    assert (tmp_path / "library.sqlite3.v2.bak").exists()
    assert library.downloads() == [] and library.load_queue().songs == []


def test_play_counts_increment(lib: Library) -> None:
    lib.record_play(A)
    lib.record_play(A)
    lib.record_play(B)
    assert lib.play_count(A.id) == 2 and lib.play_count(C.id) == 0
    assert [(t.song, t.play_count) for t in lib.recent()] == [(B, 1), (A, 2)]
    lib.clear_history()
    assert lib.recent() == [] and lib.play_count(A.id) == 0


def test_liked_orders(lib: Library) -> None:
    for song in (B, C, A):
        lib.add_favorite(song)
    assert [t.song for t in lib.favorites("added")] == [A, C, B]
    assert [t.song for t in lib.favorites("title")] == [A, B, C]
    with pytest.raises(LibraryError):
        lib.favorites("random; DROP TABLE favorites")


def test_downloads_are_independent_of_likes_and_playlists(
    lib: Library, tmp_path: Path
) -> None:
    path = tmp_path / "a.m4a"
    lib.add_download(A, path, 42, "m4a")
    lib.add_download(A, path, 43, "m4a")  # same track: updated, not duplicated
    [record] = lib.downloads()
    assert (record.song, record.path, record.size) == (A, path, 43)
    assert lib.favorite_ids() == set()
    pid = lib.create_playlist("Mix").id
    lib.add_to_playlist(pid, A)
    lib.delete_playlist(pid)
    assert lib.download_paths() == {A.id: path}
    assert lib.relink_download(A.id, tmp_path / "b.m4a", 7)
    assert lib.downloads()[0].size == 7
    assert lib.remove_download(A.id) and lib.downloads() == []
    assert not lib.remove_download(A.id)


def test_queue_save_and_load(lib: Library) -> None:
    lib.save_queue([A, B, A, C], current=1, position=42.37)
    saved = lib.load_queue()
    assert saved.songs == [A, B, A, C]  # duplicates allowed in a queue
    assert saved.current == 1 and saved.position == 42.4
    lib.save_queue([C], current=None, position=99)
    saved = lib.load_queue()
    assert saved.songs == [C] and saved.current is None and saved.position == 0.0


def test_queue_with_malformed_rows_survives(lib: Library) -> None:
    lib.save_queue([A, B], current=1, position=5)
    lib._conn.execute("UPDATE tracks SET source_url = 'nope' WHERE id = ?", (A.id,))
    saved = lib.load_queue()
    assert saved.songs == [B] and saved.current == 0


@pytest.mark.parametrize(
    "data",
    ["not json{", "[1, 2]", '{"position": "soon"}', '{"position": NaN}', "null"],
)
def test_corrupt_session_snapshot_keeps_the_queue(lib: Library, data: str) -> None:
    lib.save_queue([A, B], current=1, position=5, session={"view": "queue-view"})
    lib._conn.execute("UPDATE session SET data = ?", (data,))
    saved = lib.load_queue()
    assert saved.songs == [A, B] and saved.current == 1  # the queue survives
    assert saved.position == 0.0
    assert "position" not in saved.session
