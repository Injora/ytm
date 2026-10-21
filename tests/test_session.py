"""Crash-safe session persistence and paused, exact-position recovery."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from pixelfm import __main__ as entry
from pixelfm import app as app_module
from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.queue import RepeatMode
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.ffplay import FFplayPlayer
from pixelfm.services.library import Library
from pixelfm.services.playback import Player, SimulatedPlayer
from pixelfm.services.streams import LocalFirstResolver, StreamError
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import PlaylistNav, PlaylistTable, QueueTable, StatusBar
from tests.conftest import image_bytes, make_song
from tests.fakes import FakeReaper, FakeResolver, FakeSpawner

S = PlaybackStatus
#: Nine tracks; the seventh is a 5:13 track ("Sicko Mode" in the spec).
SONGS = [make_song(n, duration=313 if n == 7 else 200) for n in range(1, 10)]


class Provider:
    name = "youtube"
    browsable = False

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        return SONGS


def make_app(library: Library, player: Player | None = None, **kwargs) -> PixelFMApp:  # type: ignore[no-untyped-def]
    return PixelFMApp(
        config=Config(),
        provider=Provider(),
        artwork=ArtworkService(lambda url: image_bytes()),
        player=player or SimulatedPlayer(load_delay=0),
        library=library,
        unicode=True,
        truecolor=True,
        welcome=False,
        **kwargs,
    )


def fake_ffplay(resolver=None) -> tuple[FFplayPlayer, FakeSpawner]:  # type: ignore[no-untyped-def]
    spawner = FakeSpawner()
    player = FFplayPlayer(
        resolver or FakeResolver(),
        spawn=spawner,
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        retry_delays=(),
        notify_interval=0,
        volume_debounce=0,
    )
    return player, spawner


async def until(predicate: Callable[[], bool], pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


def saved(path: Path) -> tuple[list[Song], int | None, float, dict]:
    """Read the session through a separate connection, as a new process would."""
    library = Library.open(path)
    try:
        queue = library.load_queue()
        return queue.songs, queue.current, queue.position, queue.session
    finally:
        library.close()


def seed_session(
    path: Path,
    songs: Sequence[Song],
    current: int | None,
    position: float,
    session: dict | None = None,
) -> None:
    library = Library.open(path)
    library.save_queue(songs, current, position, session)
    library.close()


# ------------------------------------------------------- the spec example


async def test_ctrl_c_at_1_17_of_track_7_restores_exactly(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    app = make_app(Library.open(path))
    async with app.run_test(size=(140, 42)) as pilot:
        app.queue.extend(SONGS)
        seventh = app.queue.entries[6]
        app._play_entry(app.queue.select(seventh.uid))  # type: ignore[arg-type]
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await app.player.seek(77)
        await until(lambda: app.player.get_state().position >= 77, pilot)
        await pilot.press("ctrl+c")
    assert not app.is_running

    second = make_app(Library.open(path))
    async with second.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert [e.song for e in second.queue.entries] == SONGS  # same order
        assert second.queue.current_index == 6  # still the seventh item
        state = second.player.get_state()
        assert state.status is S.PAUSED  # ready, not playing
        assert state.song == SONGS[6] and state.duration == 313
        assert 77 <= state.position < 79  # about 1:17
        deck = str(second.query_one("#deck-state").render())
        assert "PAUSED" in deck and "restored at 1:17" in deck
        assert "was playing when PixelFM closed" in deck
        await pilot.pause(0.5)
        assert second.player.get_state().status is S.PAUSED  # never autoplays
        await pilot.press("space")
        await until(lambda: second.player.get_state().status is S.PLAYING, pilot)
        assert second.player.get_state().position >= 77
        assert second.queue.current_index == 6


async def test_play_resumes_ffplay_at_the_saved_position(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:3], 1, 77.0, {"song_id": SONGS[1].id})
    player, spawner = fake_ffplay()
    app = make_app(Library.open(path), player)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert spawner.processes == []  # nothing started on launch
        assert player.get_state().status is S.PAUSED
        await pilot.press("space")
        await until(lambda: bool(spawner.processes), pilot)
        assert spawner.last.arg("-ss") == "77.00"
        spawner.last.progress(77.5)
        await until(lambda: player.get_state().status is S.PLAYING, pilot)
        # Resuming does not count a second play of the same track.
        assert app.library.play_count(SONGS[1].id) == 0


async def test_seeking_while_restored_and_paused_moves_the_resume_point(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:2], 0, 30.0, {"song_id": SONGS[0].id})
    player, spawner = fake_ffplay()
    app = make_app(Library.open(path), player)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("right_square_bracket")  # +10 s while paused
        await until(lambda: player.get_state().position == 40.0, pilot)
        await pilot.press("space")
        await until(lambda: bool(spawner.processes), pilot)
        assert spawner.last.arg("-ss") == "40.00"


# ------------------------------------------------------------ ui state


async def test_view_playlist_and_selections_are_restored(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    library = Library.open(path)
    first_pid = library.create_playlist("A Mix").id
    pid = library.create_playlist("B Mix").id
    for song in SONGS[:5]:
        library.add_to_playlist(pid, song)
    library.add_to_playlist(first_pid, SONGS[8])
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        app.queue.extend(SONGS[:6])
        app._refresh_queue_views()
        await pilot.press("3", "j", "j", "j")  # queue row 3
        await pilot.press("4", "j")  # second playlist
        await pilot.press("l", "j", "j")  # its third track
        await pilot.pause()
        assert app.query_one(PlaylistTable).highlighted_song == SONGS[2]
        await pilot.press("q")

    second = make_app(Library.open(path))
    async with second.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert second._active_view() == "playlists-view"
        assert second.query_one(PlaylistNav).playlist_id == pid
        assert second.query_one(PlaylistTable).highlighted_song == SONGS[2]
        assert second.query_one("#queue-table", QueueTable).cursor_row == 3


async def test_settings_and_modes_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    app = make_app(Library.open(path), effects=True)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("r", "r", "z", "minus", "P")
        app.queue.extend(SONGS[:3])
        app._refresh_queue_views()
        await pilot.pause(0.2)
        await pilot.press("q")
    second = make_app(Library.open(path), effects=True)
    async with second.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert second.queue.repeat is RepeatMode.ONE
        assert second.queue.shuffle is True
        assert second.player.get_state().volume == 75
        assert second._party is not None  # Party Mode was on
        assert second._theme_choice == "music"
        await pilot.press("P")  # and turns off instantly


async def test_shuffled_playlist_source_is_restored(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    library = Library.open(path)
    pid = library.create_playlist("Mix").id
    for song in SONGS[:4]:
        library.add_to_playlist(pid, song)
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "Z")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        order = [e.song for e in app.queue.entries]
        await pilot.press("q")
    second = make_app(Library.open(path))
    async with second.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert [e.song for e in second.queue.entries] == order  # not reshuffled
        assert second._queue_source == {
            "id": pid,
            "name": "Mix",
            "shuffled": True,
        }
        assert second.query_one("#booth-source").has_class("-visible")


# ------------------------------------------------- saving while running


async def test_changes_reach_disk_without_quitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash or power loss after this point would lose nothing."""
    monkeypatch.setattr(app_module, "POSITION_SAVE_INTERVAL", 0.3)
    path = tmp_path / "library.sqlite3"
    app = make_app(Library.open(path))
    async with app.run_test(size=(140, 42)) as pilot:
        app.queue.extend(SONGS[:5])
        app._play_entry(app.queue.advance(manual=True))  # type: ignore[arg-type]
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("3", "j", "j", "d")  # remove the third entry
        await pilot.press("K")  # move the (new) third entry up
        await pilot.pause(app_module.QUEUE_SAVE_DELAY + 0.3)
        songs, current, _, session = saved(path)
        assert songs == [e.song for e in app.queue.entries]
        assert current == 0 and session["view"] == "queue-view"
        await until(lambda: saved(path)[2] >= 1.0, pilot, limit=4)  # periodic
        await pilot.press("space")  # pause: saved right away (debounced)
        await pilot.pause(app_module.QUEUE_SAVE_DELAY + 0.2)
        paused_at = app.player.get_state().position
        assert abs(saved(path)[2] - paused_at) <= 0.1
        assert saved(path)[3]["status"] == "paused"
        await pilot.press("q")


async def test_unchanged_state_is_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    library = Library.open(path)
    app = make_app(library)
    writes: list[str] = []
    save_queue, save_session = library.save_queue, library.save_session
    library.save_queue = lambda *a, **k: (writes.append("queue"), save_queue(*a, **k))[
        1
    ]  # type: ignore[method-assign]
    library.save_session = lambda *a, **k: (  # type: ignore[method-assign]
        writes.append("session"),
        save_session(*a, **k),
    )[1]
    async with app.run_test(size=(140, 42)) as pilot:
        app.queue.extend(SONGS[:3])
        app._refresh_queue_views()
        await pilot.pause(app_module.QUEUE_SAVE_DELAY + 0.2)
        count = len(writes)
        for _ in range(5):
            app.save_session()
        assert len(writes) == count  # identical snapshots are skipped
        await pilot.press("3")  # a view change is a real change
        await pilot.pause(app_module.QUEUE_SAVE_DELAY + 0.2)
        assert writes[count:] == ["session"]  # the queue rows are not rewritten


# ------------------------------------------------ damaged or stale state


@pytest.mark.parametrize(
    "data",
    [
        "{not json",
        '{"view": 12, "playlist_id": "x", "queue_cursor": 999, "status": 4}',
        '{"queue_source": "oops", "playlist_id": true, "song_id": null}',
        "[]",
    ],
)
async def test_malformed_session_does_not_crash_startup(
    tmp_path: Path, data: str
) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:3], 2, 12.0)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE session SET data = ?", (data,))
    conn.commit()
    conn.close()
    app = make_app(Library.open(path))
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert app.is_running
        assert [e.song for e in app.queue.entries] == SONGS[:3]  # queue kept
        assert app.queue.current_index == 2
        assert app.player.get_state().status is S.PAUSED


async def test_newer_session_format_keeps_the_queue(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:2], 1, 50.0, {"version": 99, "view": "queue-view"})
    app = make_app(Library.open(path))
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert [e.song for e in app.queue.entries] == SONGS[:2]
        assert app._active_view() == "booth-view"  # unknown extras ignored


async def test_stale_position_for_another_track_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:3], 0, 95.0, {"song_id": SONGS[2].id})
    app = make_app(Library.open(path))
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        state = app.player.get_state()
        assert state.song == SONGS[0] and state.position == 0.0


def test_interrupted_save_keeps_the_previous_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:3], 1, 40.0, {"view": "queue-view"})
    library = Library.open(path)

    def boom(*args: object) -> None:
        raise OSError("disk full")

    library._write_session = boom  # type: ignore[method-assign]
    with pytest.raises(OSError):
        library.save_queue(SONGS[5:], 0, 10.0, {})  # fails half-way through
    library.close()
    songs, current, position, session = saved(path)
    assert (songs, current, position) == (SONGS[:3], 1, 40.0)
    assert session["view"] == "queue-view"


def test_session_from_before_v4_keeps_its_position(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    library = Library.open(path)
    library.save_queue(SONGS[:2], 1, 0.0)
    # Simulate a 2.0 (schema 3) database: position in the old preference.
    for table in ("import_items", "imports", "session"):  # newer than schema 3
        library._conn.execute(f"DROP TABLE {table}")
    library.set_pref("queue_position", 33.3)
    library._conn.execute("PRAGMA user_version = 3")
    library.close()
    songs, current, position, _ = saved(path)
    assert (songs, current, position) == (SONGS[:2], 1, 33.3)


def test_no_save_before_the_session_was_restored(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:3], 0, 20.0)
    app = make_app(Library.open(path))  # constructed, but never mounted
    app.save_session()  # e.g. the crash handler after a failed start-up
    app.library.close()
    assert saved(path)[0] == SONGS[:3]  # the empty queue did not overwrite it


# ------------------------------------------------------------- shutdown


async def test_ctrl_c_saves_position_then_stops_ffplay(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    player, spawner = fake_ffplay()
    app = make_app(Library.open(path), player)
    async with app.run_test(size=(140, 42)) as pilot:
        app.queue.extend(SONGS[:2])
        app._play_entry(app.queue.advance(manual=True))  # type: ignore[arg-type]
        await until(lambda: bool(spawner.processes), pilot)
        spawner.last.progress(42.0)
        await until(lambda: player.get_state().position == 42.0, pilot)
        await pilot.press("ctrl+c")
    assert not app.is_running
    assert spawner.live == []  # no orphaned ffplay
    assert spawner.processes[0].signals  # it was asked to stop
    assert saved(path)[2] == 42.0


async def test_sigterm_or_hangup_saves_before_stopping(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    player, spawner = fake_ffplay()
    app = make_app(Library.open(path), player)
    async with app.run_test(size=(140, 42)) as pilot:
        app.queue.extend(SONGS[:2])
        app._play_entry(app.queue.advance(manual=True))  # type: ignore[arg-type]
        await until(lambda: bool(spawner.processes), pilot)
        spawner.last.progress(61.0)
        await until(lambda: player.get_state().position == 61.0, pilot)
        app._on_terminate()  # what SIGTERM / SIGHUP (terminal closed) run
        await pilot.pause(0.2)
    assert saved(path)[2] == 61.0
    assert saved(path)[3]["status"] == "playing"


def test_interrupt_outside_the_ui_exits_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saves: list[bool] = []

    def run(self: PixelFMApp) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(PixelFMApp, "run", run)
    monkeypatch.setattr(
        PixelFMApp, "save_session", lambda self: saves.append(True), raising=True
    )
    assert entry.main(["--mock"]) == 130
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    assert saves == [True]  # the crash path still tried to save


# -------------------------------------------------- unavailable sources


async def test_missing_online_source_keeps_the_session(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    seed_session(path, SONGS[:3], 1, 90.0, {"song_id": SONGS[1].id})
    resolver = FakeResolver(
        error=StreamError("This video is unavailable.", retryable=False)
    )
    player, spawner = fake_ffplay(resolver)
    app = make_app(Library.open(path), player)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("space")
        await until(lambda: player.get_state().status is S.ERROR, pilot)
        await pilot.pause(0.3)
        message = app.query_one(StatusBar).message
        assert "Couldn't resume Track 2" in message
        assert "queue is unchanged" in message
        # No skip, no loss: same queue, same current entry and position.
        assert [e.song for e in app.queue.entries] == SONGS[:3]
        assert app.queue.current_index == 1
        assert player.get_state().song == SONGS[1]
        assert player.get_state().position == 90.0
        assert spawner.processes == []
        await pilot.press("q")
    songs, current, position, _ = saved(path)
    assert (songs, current, position) == (SONGS[:3], 1, 90.0)


async def test_missing_local_file_resumes_from_the_stream(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    library = Library.open(path)
    library.add_download(SONGS[0], tmp_path / "gone.m4a", 100, "m4a")
    library.close()
    seed_session(path, SONGS[:2], 0, 25.0, {"song_id": SONGS[0].id})
    remote = FakeResolver()
    missing: list[str] = []
    holder: dict[str, PixelFMApp] = {}
    resolver = LocalFirstResolver(
        remote,
        lambda song_id: holder["app"]._local_path(song_id),
        lambda song, path: missing.append(song.id),
    )
    player, spawner = fake_ffplay(resolver)
    app = make_app(Library.open(path), player)
    holder["app"] = app
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("space")
        await until(lambda: bool(spawner.processes), pilot)
        assert remote.calls  # fell back to the online source
        assert missing == [SONGS[0].id]
        assert spawner.last.arg("-ss") == "25.00"
        assert not spawner.last.args[-1].startswith("file:")


async def test_local_file_resumes_locally_with_seek(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    audio = tmp_path / "My Music" / "Track 1 [song0000001].m4a"
    audio.parent.mkdir()
    audio.write_bytes(b"\0" * 64)
    library = Library.open(path)
    library.add_download(SONGS[0], audio, 64, "m4a")
    library.close()
    seed_session(path, SONGS[:1], 0, 33.0, {"song_id": SONGS[0].id})
    remote = FakeResolver()
    holder: dict[str, PixelFMApp] = {}
    resolver = LocalFirstResolver(
        remote, lambda song_id: holder["app"]._local_path(song_id)
    )
    player, spawner = fake_ffplay(resolver)
    app = make_app(Library.open(path), player)
    holder["app"] = app
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("space")
        await until(lambda: bool(spawner.processes), pilot)
        assert spawner.last.args[-1] == f"file:{audio}"
        assert spawner.last.arg("-ss") == "33.00"
        assert remote.calls == []  # local playback never resolves a stream URL


def test_cue_clamps_and_live_tracks_start_over() -> None:
    player = SimulatedPlayer(load_delay=0)
    assert player.cue(make_song(1, duration=100), 250.0)
    assert player.get_state().position == 99.0  # within the track
    assert not player.cue(make_song(2), 10.0)  # only from IDLE
    live = SimulatedPlayer(load_delay=0)
    assert live.cue(make_song(3, duration=0), 42.0)
    assert live.get_state().position == 0.0 and not live.get_state().can_seek
