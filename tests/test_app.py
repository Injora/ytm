"""UI integration tests with fake providers and engines; no network or audio."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from textual.widgets import Input

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.services.artwork import ArtworkMode, ArtworkService
from pixelfm.services.ffplay import FFplayPlayer
from pixelfm.services.library import Library
from pixelfm.services.playback import Player, SimulatedPlayer
from pixelfm.services.search import (
    CachedSearchProvider,
    MockSearchProvider,
    SearchError,
)
from pixelfm.services.streams import StreamError
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import (
    AlbumArt,
    NowPlaying,
    ProgressLine,
    QueuePanel,
    ResultsTable,
    StatusBar,
    StatusLevel,
)
from tests.conftest import image_bytes, make_song
from tests.fakes import FakeReaper, FakeResolver, FakeSpawner

S = PlaybackStatus
SONGS = [make_song(n) for n in range(1, 6)]


class FakeYouTube:
    """Non-browsable provider, like YouTube, returning canned songs."""

    name = "youtube"
    browsable = False

    def __init__(self, songs: Sequence[Song] = SONGS, error: str | None = None) -> None:
        self.songs = list(songs)
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        self.queries.append(query)
        if self.error:
            raise SearchError(self.error)
        return self.songs[:limit]


def make_app(
    provider=None,  # type: ignore[no-untyped-def]
    fetch_data: bytes = image_bytes(),
    player: Player | None = None,
    library: Library | None = None,
    **config,
) -> PixelFMApp:
    return PixelFMApp(
        config=Config(**config),
        provider=provider or FakeYouTube(),
        artwork=ArtworkService(lambda url: fetch_data),
        player=player or SimulatedPlayer(load_delay=0),
        library=library or Library.memory(),
        unicode=True,
        truecolor=True,
    )


def ffplay_engine(
    resolver: FakeResolver | None = None,
) -> tuple[FFplayPlayer, FakeSpawner]:
    spawner = FakeSpawner()
    player = FFplayPlayer(
        resolver or FakeResolver(),
        spawn=spawner,
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        volume_debounce=0,
        retry_delays=(),
        kill_timeout=0.05,
        notify_interval=0,
    )
    return player, spawner


async def settle(app: PixelFMApp, pilot) -> None:  # type: ignore[no-untyped-def]
    await app.workers.wait_for_complete()
    await pilot.pause()


async def search(app: PixelFMApp, pilot, query: str) -> None:  # type: ignore[no-untyped-def]
    await pilot.press("slash")
    app.query_one("#search", Input).value = query
    await pilot.press("enter")
    await settle(app, pilot)


async def until(predicate, pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


# ----------------------------------------------------------------- search


async def test_startup_shows_the_booth() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert app.query_one("#views").current == "booth-view"
        assert app.focused is app.query_one("#tx-play")
        await pilot.press("slash")
        assert app.focused is app.query_one("#search")
        assert app.query_one(ResultsTable).row_count == 0
        art = app.query_one("#deck-art", AlbumArt).art
        assert art is not None and art.placeholder


async def test_search_select_and_artwork() -> None:
    provider = FakeYouTube()
    app = make_app(provider)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "daft punk")
        table = app.query_one(ResultsTable)
        assert provider.queries == ["daft punk"]
        assert table.row_count == len(SONGS)
        assert app.focused is table
        assert app.query_one(StatusBar).level is StatusLevel.SUCCESS

        await pilot.press("j", "j", "enter")
        await settle(app, pilot)
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        assert app.player.get_state().song == SONGS[2]
        art = app.query_one("#deck-art", AlbumArt).art
        assert art is not None and not art.placeholder
        assert (art.width, art.height) == (32, 16)  # 40 rows: large deck art


async def test_repeated_search_hits_cache() -> None:
    inner = FakeYouTube()
    app = make_app(CachedSearchProvider(inner))
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "abc")
        await search(app, pilot, "ABC ")
        assert inner.queries == ["abc"]


async def test_search_error_shown_in_status() -> None:
    app = make_app(FakeYouTube(error="Network error: couldn't reach YouTube."))
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "anything")
        status = app.query_one(StatusBar)
        assert status.level is StatusLevel.ERROR
        assert "Network error" in status.message
        assert app.query_one(ResultsTable).row_count == 0


async def test_empty_results_message() -> None:
    app = make_app(FakeYouTube(songs=[]))
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "zzz")
        assert "No results" in app.query_one(StatusBar).message


async def test_blank_query_does_not_search() -> None:
    provider = FakeYouTube()
    app = make_app(provider)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "   ")
        assert provider.queries == []
        assert "Type something" in app.query_one(StatusBar).message


async def test_typing_in_search_does_not_trigger_shortcuts() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("escape")  # search starts focused; leave it first
        await pilot.press("slash", "q", "space", "a", "plus", "minus")
        assert app.query_one("#search", Input).value == "q a+-"
        assert app.is_running
        assert app.player.get_state().volume == 80
        await pilot.press("escape")
        assert app.focused is app.query_one(ResultsTable)


# --------------------------------------------------------------- playback


async def test_space_toggles_pause_and_resume() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("space")  # nothing selected yet
        assert app.player.get_state().status is S.IDLE
        assert "Nothing loaded" in app.query_one(StatusBar).message

        await pilot.press("enter")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("space")
        await settle(app, pilot)
        assert app.player.get_state().status is S.PAUSED
        assert "Paused" in app.query_one(StatusBar).message
        await pilot.press("space")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)


async def test_volume_keys() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter", "plus", "plus", "equals_sign", "minus")
        await settle(app, pilot)
        assert app.player.get_state().volume == 90
        for _ in range(30):
            await pilot.press("minus")
        await settle(app, pilot)
        assert app.player.get_state().volume == 0


async def test_seek_keys_accumulate() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("right_square_bracket", "right_square_bracket")
        await settle(app, pilot)
        await until(lambda: app.player.get_state().position >= 20, pilot)
        await pilot.press("left_square_bracket")
        await settle(app, pilot)
        assert 9 <= app.player.get_state().position < 15


async def test_queue_next_and_auto_advance() -> None:
    songs = [make_song(n, duration=2) for n in range(1, 4)]
    player = SimulatedPlayer(load_delay=0, speed=20, tick=0.02)
    played: list[Song] = []
    player.subscribe(
        lambda st: (
            st.status is S.PLAYING
            and st.song is not None
            and (not played or played[-1] != st.song)
            and played.append(st.song)
        )
    )
    app = make_app(FakeYouTube(songs), player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("j", "a", "j", "a")
        assert [e.song for e in app.queue.upcoming] == songs[1:3]
        assert app.query_one(QueuePanel).border_subtitle == "2"

        await pilot.press("n")  # plays songs[1]; it ends after ~0.1 s
        await until(lambda: app.player.get_state().song == songs[2], pilot)
        await until(lambda: app.player.get_state().status is S.ENDED, pilot)
        await settle(app, pilot)
        assert played == songs[1:3]  # advanced through the queue by itself
        assert app.queue.upcoming == []
        assert "End of queue" in app.query_one(StatusBar).message


async def test_progress_comes_from_engine_state() -> None:
    player, spawner = ffplay_engine()
    app = make_app(player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await until(lambda: bool(spawner.processes), pilot)
        assert "BUFFERING" in str(app.query_one("#np-state").render())
        spawner.last.progress(65.4)
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.pause()
        progress = app.query_one(ProgressLine)
        assert (progress.position, progress.duration) == (65, 180)
        assert "PLAYING" in str(app.query_one("#np-state").render())
        assert "simulated" not in str(app.query_one("#np-state").render())


async def test_playback_error_is_shown_and_retryable() -> None:
    resolver = FakeResolver(
        error=StreamError("Network error while loading the stream.")
    )
    player, spawner = ffplay_engine(resolver)
    app = make_app(player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await until(lambda: app.player.get_state().status is S.ERROR, pilot)
        await settle(app, pilot)
        status = app.query_one(StatusBar)
        assert status.level is StatusLevel.ERROR
        assert "Press space to retry" in status.message
        assert app.is_running

        resolver.error = None
        await pilot.press("space")
        await until(lambda: bool(spawner.processes), pilot)
        assert app.player.get_state().status is S.LOADING


async def test_quit_stops_audio_process() -> None:
    player, spawner = ffplay_engine()
    app = make_app(player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await until(lambda: bool(spawner.processes), pilot)
        spawner.last.progress(1.0)
        await pilot.press("q")
        await pilot.pause()
    assert spawner.live == []
    assert not app.is_running


async def test_stop_key() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("x")
        await settle(app, pilot)
        assert app.player.get_state().status is S.STOPPED


# ----------------------------------------------------------------- artwork


async def test_artwork_mode_cycles() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await settle(app, pilot)
        for expected in (ArtworkMode.MONO, ArtworkMode.GLYPH, ArtworkMode.COLOR):
            await pilot.press("m")
            await settle(app, pilot)
            art = app.query_one("#deck-art", AlbumArt).art
            assert app.artwork_mode is expected
            assert art is not None and art.mode is expected


async def test_configured_artwork_mode_and_size() -> None:
    app = make_app(artwork_mode="glyph", artwork_width=10, artwork_height=5)
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await settle(app, pilot)
        art = app.query_one("#deck-art", AlbumArt).art
        assert art is not None
        # 40+ rows: the deck shows the configured cover at 1.5x.
        assert (art.mode, art.width, art.height) == (ArtworkMode.GLYPH, 20, 10)


async def test_corrupt_thumbnail_shows_placeholder() -> None:
    app = make_app(fetch_data=b"definitely not a jpeg")
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await settle(app, pilot)
        art = app.query_one("#deck-art", AlbumArt).art
        assert art is not None and art.placeholder


async def test_mock_provider_is_browsable() -> None:
    app = make_app(MockSearchProvider())
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert app.query_one(ResultsTable).row_count > 0


# ------------------------------------------------------------------ layout


@pytest.mark.parametrize("size", [(40, 10), (60, 16), (80, 24), (200, 60)])
async def test_layout_survives_any_size(size: tuple[int, int]) -> None:
    app = make_app()
    async with app.run_test(size=size) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.query_one(ResultsTable).row_count == len(SONGS)
        assert app.query_one(QueuePanel).display == (size[0] >= 100)
        assert app.query_one(NowPlaying).display


async def test_resize_keeps_working() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot, "x")
        await pilot.resize_terminal(45, 12)
        await pilot.pause()
        await pilot.press("j", "enter")
        await settle(app, pilot)
        assert app.player.get_state().song == SONGS[1]
        await pilot.resize_terminal(150, 45)
        await pilot.pause()
        assert app.query_one(QueuePanel).display


async def test_ascii_mode_has_no_unicode_art() -> None:
    app = PixelFMApp(
        config=Config(),
        provider=FakeYouTube(),
        artwork=ArtworkService(lambda url: image_bytes()),
        player=SimulatedPlayer(load_delay=0),
        library=Library.memory(),
        unicode=False,
        truecolor=False,
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await search(app, pilot, "x")
        await pilot.press("enter")
        await settle(app, pilot)
        art = app.query_one("#deck-art", AlbumArt).art
        assert art is not None and art.to_text().plain.isascii()
        assert "-ascii" in app.screen.classes
