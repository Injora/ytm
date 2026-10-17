"""UI tests for views, queue management, likes, playlists, history, and prefs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from textual.widgets import Input, OptionList

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.queue import RepeatMode
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.ffplay import FFplayPlayer
from pixelfm.services.library import Library
from pixelfm.services.playback import Player, SimulatedPlayer
from pixelfm.services.streams import StreamError, StreamInfo
from pixelfm.state import PlaybackState, PlaybackStatus
from pixelfm.widgets import (
    BoothQueueTable,
    ConfirmDialog,
    HelpScreen,
    LikedTable,
    PlaylistPicker,
    PlaylistTable,
    QueueTable,
    RecentTable,
    StatusBar,
    TextPrompt,
    ViewTabs,
)
from tests.conftest import image_bytes, make_song
from tests.fakes import FakeReaper, FakeSpawner

S = PlaybackStatus
SONGS = [make_song(n) for n in range(1, 6)]
VIEW_KEYS = {
    "1": "booth-view",
    "2": "search-view",
    "3": "queue-view",
    "4": "playlists-view",
    "5": "liked-view",
    "6": "downloads-view",
    "7": "recent-view",
    "8": "settings-view",
}


class Provider:
    name = "youtube"
    browsable = False

    def __init__(self, songs: Sequence[Song] = SONGS) -> None:
        self.songs = list(songs)

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        return self.songs[:limit]


def make_app(
    library: Library | None = None,
    player: Player | None = None,
    songs: Sequence[Song] = SONGS,
) -> PixelFMApp:
    return PixelFMApp(
        config=Config(),
        provider=Provider(songs),
        artwork=ArtworkService(lambda url: image_bytes()),
        player=player or SimulatedPlayer(load_delay=0),
        library=library or Library.memory(),
        unicode=True,
        truecolor=True,
        welcome=False,
    )


async def settle(app: PixelFMApp, pilot) -> None:  # type: ignore[no-untyped-def]
    await app.workers.wait_for_complete()
    await pilot.pause()


async def until(predicate, pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


async def search(app: PixelFMApp, pilot) -> None:  # type: ignore[no-untyped-def]
    await pilot.press("slash")
    app.query_one("#search", Input).value = "x"
    await pilot.press("enter")
    await settle(app, pilot)


def status(app: PixelFMApp) -> str:
    return app.query_one(StatusBar).message


# ---------------------------------------------------------------- views


async def test_number_keys_reach_every_view_and_keep_playback() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await search(app, pilot)
        await pilot.press("enter")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        for key, view in VIEW_KEYS.items():
            await pilot.press(key)
            await pilot.pause()
            assert app.query_one("#views").current == view
            assert app.query_one(ViewTabs).active == view
            assert app.player.get_state().song == SONGS[0]  # never interrupted
        await pilot.press("1")
        assert app.screen.has_class("-booth")


async def test_digits_typed_in_search_do_not_switch_views() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("slash", "2", "3", "f", "p", "D")
        assert app.query_one("#search", Input).value == "23fpD"
        assert app.query_one("#views").current == "search-view"
        assert app.library.favorite_ids() == set()


async def test_help_screen() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert not isinstance(app.screen, HelpScreen)


# ---------------------------------------------------------------- likes


async def test_like_from_search_booth_and_liked_view() -> None:
    library = Library.memory()
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await search(app, pilot)
        await pilot.press("j", "f")  # like from search results
        assert library.favorite_ids() == {SONGS[1].id}
        await pilot.press("enter")  # play SONGS[1]
        await pilot.press("1")
        await pilot.pause()  # let the booth lay out before clicking
        await pilot.click("#tx-like")  # unlike via the booth button
        assert library.favorite_ids() == set()
        await pilot.pause(0.6)  # beyond the double-click window
        await pilot.click("#tx-like")
        assert library.favorite_ids() == {SONGS[1].id}

        await pilot.press("5")
        table = app.query_one(LikedTable)
        assert table.songs == [SONGS[1]]
        await pilot.press("d")  # unlike from the Liked view
        assert library.favorite_ids() == set()
        assert table.songs == []
        assert app.query_one("#liked-empty").has_class("-visible")


async def test_liked_sort_and_filter() -> None:
    library = Library.memory()
    songs = [make_song(1), make_song(2), make_song(3)]
    for song in songs:
        library.add_favorite(song)
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("5")
        table = app.query_one(LikedTable)
        assert table.songs == list(reversed(songs))  # newest like first
        await pilot.press("o")  # sort by title
        assert app._liked_order == "title"
        assert [s.title for s in table.songs] == sorted(s.title for s in songs)
        assert library.get_pref("liked_order") == "title"

        await pilot.press("ctrl+f")
        app.query_one("#liked-filter", Input).value = "Track 2"
        await pilot.pause()
        assert table.songs == [songs[1]]
        await pilot.press("escape")  # clears the filter
        await pilot.pause()
        assert len(table.songs) == 3


async def test_liked_play_and_append() -> None:
    library = Library.memory()
    for song in SONGS[:3]:
        library.add_favorite(song)
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("5", "A")
        assert len(app.queue.upcoming) == 3
        await pilot.press("j", "enter")
        await until(lambda: app.player.get_state().song is not None, pilot)
        assert app.player.get_state().song == app.query_one(LikedTable).songs[1]


# ------------------------------------------------------------ playlists


async def test_create_playlist_from_picker_and_add_tracks() -> None:
    library = Library.memory()
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await search(app, pilot)
        await pilot.press("p")
        assert isinstance(app.screen, PlaylistPicker)
        await pilot.press("enter")  # only option: "+ New playlist..."
        assert isinstance(app.screen, TextPrompt)
        await pilot.press(*"Road Trip", "enter")
        await pilot.pause()
        [playlist] = library.playlists()
        assert playlist.name == "Road Trip"
        assert [t.song for t in library.playlist_tracks(playlist.id)] == [SONGS[0]]

        await pilot.press("j", "p")
        app.screen.query_one(OptionList).highlighted = 0
        await pilot.press("enter")
        await pilot.press("p")
        app.screen.query_one(OptionList).highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        assert "already in Road Trip" in status(app)
        assert library.playlist(playlist.id).count == 2


async def test_playlist_view_edit_reorder_filter_play_delete() -> None:
    library = Library.memory()
    pid = library.create_playlist("Mix").id
    for song in SONGS[:3]:
        library.add_to_playlist(pid, song)
    library.add_favorite(SONGS[0])
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4")
        table = app.query_one(PlaylistTable)
        assert table.songs == SONGS[:3]
        await pilot.press("A")  # append whole playlist to the queue
        assert [e.song for e in app.queue.upcoming] == SONGS[:3]

        await pilot.press("l", "j", "J")  # move track 2 down
        assert [t.song for t in library.playlist_tracks(pid)] == [
            SONGS[0],
            SONGS[2],
            SONGS[1],
        ]
        await pilot.press("ctrl+f")
        app.query_one("#playlist-filter", Input).value = "Track 3"
        await pilot.pause()
        assert table.songs == [SONGS[2]]
        await pilot.press("escape", "escape")  # clear the filter, then leave it
        await pilot.pause()

        await pilot.press("4", "e")
        app.screen.query_one(Input).value = "Night Mix"
        await pilot.press("enter")
        await pilot.pause()
        assert library.playlist(pid).name == "Night Mix"

        await pilot.press("enter")  # nav enter: play all
        await until(lambda: app.player.get_state().song == SONGS[0], pilot)

        await pilot.press("d")
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("n")
        assert library.playlists() != []
        await pilot.press("d", "y")
        await pilot.pause()
        assert library.playlists() == []
        assert library.favorite_ids() == {SONGS[0].id}  # likes survive deletion


async def test_cancelling_dialogs_changes_nothing() -> None:
    library = Library.memory()
    app = make_app(library)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("p", "escape")
        assert library.playlists() == []
        await pilot.press("p", "enter", "escape")
        assert library.playlists() == []


# --------------------------------------------------------------- recent


async def test_recent_view_shows_play_counts() -> None:
    library = Library.memory()
    library.record_play(SONGS[1])
    library.record_play(SONGS[1])
    library.record_play(SONGS[0])
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("7")
        table = app.query_one(RecentTable)
        assert table.songs == [SONGS[0], SONGS[1]]
        assert [row.extra for row in table.rows_data] == ["1×", "2×"]
        await pilot.press("enter")
        await until(lambda: app.player.get_state().song == SONGS[0], pilot)


async def test_history_recorded_once_per_play() -> None:
    library = Library.memory()
    app = make_app(library)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("enter")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("space")
        await settle(app, pilot)
        await pilot.press("space")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        assert library.play_count(SONGS[0].id) == 1
        assert [t.song for t in library.recent()] == [SONGS[0]]


# ---------------------------------------------------------------- queue


async def test_queue_view_remove_move_clear() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("enter")  # play SONGS[0]
        for _ in range(3):
            await pilot.press("j", "a")
        await pilot.press("3")
        table = app.query_one("#queue-table", QueueTable)
        assert table.row_count == 4

        await pilot.press("d")  # cursor on the playing entry: refused
        assert "playing track" in status(app)
        assert table.row_count == 4

        await pilot.press("j", "J")  # move SONGS[1] below SONGS[2]
        assert [e.song for e in app.queue.upcoming] == [SONGS[2], SONGS[1], SONGS[3]]
        await pilot.press("d")
        assert [e.song for e in app.queue.upcoming] == [SONGS[2], SONGS[3]]

        await pilot.press("C")
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("n")
        assert len(app.queue.upcoming) == 2
        await pilot.press("C", "y")
        await pilot.pause()
        assert app.queue.upcoming == []
        assert app.player.get_state().song == SONGS[0]  # kept playing


async def test_booth_queue_panel_is_interactive() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await search(app, pilot)
        await pilot.press("enter", "j", "a", "j", "a", "1")
        panel = app.query_one(BoothQueueTable)
        assert panel.row_count == 3
        panel.focus()
        await pilot.press("j", "j", "d")  # remove the last upcoming track
        assert [e.song for e in app.queue.upcoming] == [SONGS[1]]
        await pilot.press("enter")  # cursor moved onto SONGS[1]
        await until(lambda: app.player.get_state().song == SONGS[1], pilot)


async def test_play_next_and_previous() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("enter", "j", "a", "j", "A")
        assert [e.song for e in app.queue.upcoming] == [SONGS[2], SONGS[1]]
        await pilot.press("n")
        await until(lambda: app.player.get_state().song == SONGS[2], pilot)
        await pilot.press("b")
        await until(lambda: app.player.get_state().song == SONGS[0], pilot)


async def test_stale_end_event_does_not_skip_twice() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("enter")
        for _ in range(2):
            await pilot.press("j", "a")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("n")  # user skips to SONGS[1]
        await until(lambda: app.player.get_state().song == SONGS[1], pilot)
        # The old track's "ended" event arrives late:
        app._on_playback(PlaybackState(status=S.ENDED, song=SONGS[0], position=180))
        await settle(app, pilot)
        assert app.queue.current is not None
        assert app.queue.current.song == SONGS[1]
        assert [e.song for e in app.queue.upcoming] == [SONGS[2]]


async def test_repeat_one_replays_on_real_end() -> None:
    songs = [make_song(n, duration=2) for n in range(1, 3)]
    player = SimulatedPlayer(load_delay=0, speed=20, tick=0.02)
    starts: list[Song] = []
    player.subscribe(
        lambda st: st.status is S.LOADING and st.song and starts.append(st.song)
    )
    app = make_app(player=player, songs=songs)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("r", "r")  # off -> all -> one
        assert app.queue.repeat is RepeatMode.ONE
        await pilot.press("enter", "j", "a")
        await until(lambda: starts.count(songs[0]) >= 2, pilot)
        assert songs[1] not in starts
        await pilot.press("r")  # off: finishes, then plays songs[1]
        await until(lambda: app.player.get_state().song == songs[1], pilot)


async def test_unavailable_track_is_marked_and_skipped() -> None:
    bad, good = SONGS[0], SONGS[1]

    class Resolver:
        async def resolve(self, song: Song, *, refresh: bool = False) -> StreamInfo:
            if song == bad:
                raise StreamError("This video is unavailable.", retryable=False)
            return StreamInfo(url="https://media.example.com/a.m4a")

    spawner = FakeSpawner()
    player = FFplayPlayer(
        Resolver(),
        spawn=spawner,
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        retry_delays=(),
        notify_interval=0,
    )
    library = Library.memory()
    app = make_app(library, player)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("j", "a", "k", "enter")  # queue good, play bad
        await until(lambda: bool(spawner.processes), pilot)
        assert app.player.get_state().song == good
        assert library.unavailable_ids() == {bad.id}


async def test_all_unavailable_does_not_loop_forever() -> None:
    class Resolver:
        calls = 0

        async def resolve(self, song: Song, *, refresh: bool = False) -> StreamInfo:
            Resolver.calls += 1
            raise StreamError("This video is unavailable.", retryable=False)

    player = FFplayPlayer(
        Resolver(),
        spawn=FakeSpawner(),
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        notify_interval=0,
    )
    app = make_app(player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("r")  # repeat all
        await pilot.press("j", "a", "k", "enter")
        await pilot.pause(0.5)
        await settle(app, pilot)
        assert Resolver.calls <= 3
        assert "Skipped unavailable" in status(app)


# --------------------------------------------------- persistence on restart


async def test_preferences_persist_between_sessions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "library.sqlite3"
    app = make_app(Library.open(path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r", "z", "minus", "minus", "right_curly_bracket", "N")
        await settle(app, pilot)
    second = make_app(Library.open(path))
    async with second.run_test(size=(120, 40)) as pilot:
        await settle(second, pilot)
        assert second.queue.repeat is RepeatMode.ALL
        assert second.queue.shuffle is True
        assert second.player.get_state().volume == 70
        assert (second._fx.bass, second._fx.normalize) == (2, True)


async def test_queue_is_restored_but_not_auto_played(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "library.sqlite3"
    app = make_app(Library.open(path))
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("j", "enter", "j", "a", "j", "a")
        await until(lambda: app.player.get_state().position >= 0.25, pilot)
        await pilot.press("space")  # pause somewhere
        await settle(app, pilot)
        paused_at = app.player.get_state().position
        await pilot.press("q")
    assert not app.is_running

    second = make_app(Library.open(path))
    async with second.run_test(size=(120, 40)) as pilot:
        await settle(second, pilot)
        assert [e.song for e in second.queue.entries] == SONGS[1:4]
        assert (
            second.queue.current is not None and second.queue.current.song == SONGS[1]
        )
        # Restored paused at the saved position; nothing plays until asked.
        state = second.player.get_state()
        assert state.status is S.PAUSED and state.song == SONGS[1]
        assert abs(state.position - paused_at) < 0.2
        assert "PAUSED" in str(second.query_one("#deck-state").render())
        assert "resume" in str(second.query_one("#deck-state").render())
        await pilot.press("space")
        await until(lambda: second.player.get_state().status is S.PLAYING, pilot)
        assert second.player.get_state().song == SONGS[1]
        assert second.player.get_state().position >= paused_at - 0.2


@pytest.mark.parametrize("size", [(40, 12), (60, 18), (80, 24), (100, 30), (200, 60)])
async def test_views_survive_small_and_large_terminals(size: tuple[int, int]) -> None:
    library = Library.memory()
    library.add_favorite(SONGS[0])
    app = make_app(library)
    async with app.run_test(size=size) as pilot:
        await search(app, pilot)
        await pilot.press("enter")
        for key in VIEW_KEYS:
            await pilot.press(key)
            await pilot.pause()
        assert app.is_running
        assert app.screen.has_class("-compact") is (size[0] < 100 or size[1] < 30)
