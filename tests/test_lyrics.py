"""Lyrics: LRC parsing, YouTube title cleanup, lookup, and the booth panel."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from textual.widgets import Input

from pixelfm.models import Song
from pixelfm.services.cache import DiskCache
from pixelfm.services.lyrics import (
    LyricsError,
    LyricsService,
    from_record,
    parse_lrc,
    queries,
)
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import LyricsView
from tests.conftest import make_song
from tests.test_booth import load_and_play, make_app, until

LRC = """[ar: Somebody]
[00:01.00]First line
[00:03.50]Second line
[00:05.00][00:09.00]Chorus
[00:07.25]
[00:11.00]Last line
"""


def song(title: str, artist: str, duration: int = 200) -> Song:
    return Song("abcdefghijk", title, artist, duration, "https://example.com/x")


def test_parse_lrc_sorts_repeats_and_skips_tags() -> None:
    lines = parse_lrc(LRC)
    assert [(ln.time, ln.text) for ln in lines] == [
        (1.0, "First line"),
        (3.5, "Second line"),
        (5.0, "Chorus"),
        (7.25, ""),
        (9.0, "Chorus"),
        (11.0, "Last line"),
    ]


def test_index_follows_position() -> None:
    lyrics = from_record({"syncedLyrics": LRC})
    assert lyrics is not None and lyrics.synced
    assert lyrics.index_at(0.5) == -1
    assert lyrics.index_at(3.5) == 1
    assert lyrics.index_at(10.0) == 4
    assert lyrics.index_at(999) == 5


def test_plain_lyrics_are_paced_through_the_track() -> None:
    lyrics = from_record({"plainLyrics": "a\nb\n\nc\nd", "syncedLyrics": None})
    assert lyrics is not None and not lyrics.synced
    assert [lyrics.index_at(t, 100) for t in (0, 30, 60, 99)] == [0, 1, 2, 3]


def test_stubs_and_instrumentals_are_not_lyrics() -> None:
    assert from_record({"syncedLyrics": "[00:00.00]probe"}) is None
    assert from_record({"instrumental": True, "plainLyrics": "a\nb\nc\nd"}) is None


def test_queries_clean_youtube_titles() -> None:
    assert queries(
        song("Rick Astley - Never Gonna Give You Up (Official Music Video)", "Rick")
    ) == [
        ("Rick Astley", "Never Gonna Give You Up"),
        ("Rick", "Rick Astley - Never Gonna Give You Up"),
    ]
    assert queries(song("Blinding Lights [Lyrics] ft. Nobody", "The Weeknd - Topic"))[
        0
    ] == ("The Weeknd", "Blinding Lights")
    assert queries(song("Despacito", "LuisFonsiVEVO")) == [("LuisFonsi", "Despacito")]


class FakeLrclib:
    def __init__(self, get: dict | None = None, search: list | None = None) -> None:
        self.get, self.search, self.urls = get, search or [], []

    def __call__(self, url: str) -> object:
        self.urls.append(url)
        if urlparse(url).path.endswith("/get"):
            return self.get
        return self.search


async def test_service_prefers_synced_search_result_of_right_length() -> None:
    fake = FakeLrclib(
        get={"plainLyrics": "a\nb\nc\nd"},
        search=[
            {"duration": 260, "syncedLyrics": LRC},  # wrong length
            {"duration": 201, "syncedLyrics": LRC},
        ],
    )
    lyrics = await LyricsService(fake).lyrics_for(song("Track", "Artist", 200))
    assert lyrics is not None and lyrics.synced
    query = parse_qs(urlparse(fake.urls[0]).query)
    assert query == {"artist_name": ["Artist"], "track_name": ["Track"],
                     "duration": ["200"]}  # fmt: skip


async def test_service_caches_hits_on_disk_and_misses_in_memory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    disk = DiskCache(tmp_path / "lyrics")
    fake = FakeLrclib(get={"syncedLyrics": LRC})
    track = song("Track", "Artist")
    assert await LyricsService(fake, disk=disk).lyrics_for(track) is not None
    offline = FakeLrclib()
    again = await LyricsService(offline, disk=disk).lyrics_for(track)
    assert again is not None and again.lines[0].text == "First line"
    assert offline.urls == []

    missing = LyricsService(offline)
    assert await missing.lyrics_for(make_song(9)) is None
    asked = len(offline.urls)
    assert await missing.lyrics_for(make_song(9)) is None
    assert len(offline.urls) == asked


async def test_network_errors_are_not_cached() -> None:
    calls = []

    def flaky(url: str) -> object:
        calls.append(url)
        if len(calls) == 1:
            raise LyricsError("offline")
        return {"syncedLyrics": LRC}

    service = LyricsService(flaky)
    assert await service.lyrics_for(song("Track", "Artist")) is None
    assert await service.lyrics_for(song("Track", "Artist")) is not None


async def test_booth_shows_the_line_being_sung() -> None:
    lrc = "\n".join(f"[00:{n:02d}.00]Line {n}" for n in range(0, 60, 2))
    app = make_app(lyrics=LyricsService(FakeLrclib(get={"syncedLyrics": lrc})))
    async with app.run_test(size=(140, 46)) as pilot:
        view = app.query_one("#lyrics", LyricsView)
        await load_and_play(app, pilot)
        await until(lambda: view._lyrics is not None, pilot)
        await until(lambda: view._index >= 0, pilot)
        rendered = str(view.render())
        assert "[b $primary]Line" in rendered
        assert view.border_title == "lyrics"


async def test_booth_lyrics_stay_blank_without_lyrics() -> None:
    app = make_app()  # the default service is offline in tests
    async with app.run_test(size=(140, 46)) as pilot:
        await load_and_play(app, pilot)
        await app.workers.wait_for_complete()
        view = app.query_one("#lyrics", LyricsView)
        assert view.render() == ""
        assert view.border_title == "lyrics"


async def test_lyrics_wait_until_the_track_plays() -> None:
    fake = FakeLrclib(get={"syncedLyrics": LRC})
    app = make_app(lyrics=LyricsService(fake), player=SimulatedPlayer(load_delay=0.5))
    async with app.run_test(size=(140, 46)) as pilot:
        await pilot.press("slash")
        app.query_one("#search", Input).value = "x"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.press("enter")
        await until(lambda: app.playback.status is PlaybackStatus.LOADING, pilot)
        assert fake.urls == []  # nothing asked while the audio loads
        await until(lambda: app.playback.status is PlaybackStatus.PLAYING, pilot)
        await until(lambda: fake.urls != [], pilot)


async def test_y_turns_lyrics_off_and_on() -> None:
    fake = FakeLrclib(get={"syncedLyrics": LRC})
    app = make_app(lyrics=LyricsService(fake))
    async with app.run_test(size=(140, 46)) as pilot:
        view = app.query_one("#lyrics", LyricsView)
        await load_and_play(app, pilot)
        await until(lambda: view._lyrics is not None, pilot)
        await pilot.press("y")
        assert not app.lyrics_on and app.library.get_pref("lyrics") is False
        assert view._lyrics is None and view.border_title == "lyrics · off (y)"
        await pilot.pause(0.6)
        await pilot.press("y")
        await until(lambda: view._lyrics is not None, pilot)
        assert app.library.get_pref("lyrics") is True
