"""YouTube matching, imported playlists in the library, and the import UI."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.library import Library, is_placeholder
from pixelfm.services.matching import LIKELY, Matcher, core_title, rank, score
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.services.spotify import SpotifyPlaylist, SpotifyTrack
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import PlaylistNav, PlaylistTable, StatusBar
from pixelfm.widgets.importer import CandidatePicker, ImportPreview, MatchReview
from tests.conftest import image_bytes
from tests.test_spotify import PID, FakeSpotify, signed_in

S = PlaybackStatus


def video(n: int, title: str, channel: str, duration: int) -> Song:
    vid = f"vid{n:08d}"
    return Song(
        id=vid,
        title=title,
        artist=channel,
        duration=duration,
        source_url=f"https://www.youtube.com/watch?v={vid}",
        thumbnail_url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
    )


WANTED = SpotifyTrack(0, "Blinding Lights", ("The Weeknd",), "After Hours", 200.0)


# ------------------------------------------------------------- ranking


def test_prefers_the_studio_recording_over_other_versions() -> None:
    results = [
        video(1, "The Weeknd - Blinding Lights (Lyrics)", "7clouds", 201),
        video(2, "The Weeknd - Blinding Lights (Live at the Super Bowl)", "NFL", 260),
        video(3, "Blinding Lights (sped up)", "speed songs", 150),
        video(4, "Blinding Lights (slowed + reverb)", "slowed", 260),
        video(5, "Blinding Lights - Piano Cover", "Pianist", 199),
        video(6, "Blinding Lights", "The Weeknd - Topic", 200),
        video(7, "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 263),
    ]
    ranked = rank(WANTED, results)
    assert ranked[0].song.id == "vid00000006"  # the YouTube Music studio track
    assert ranked[0].label == "strong"
    unwanted = {
        "vid00000001",
        "vid00000002",
        "vid00000003",
        "vid00000004",
        "vid00000005",
    }
    assert all(c.score < LIKELY for c in ranked if c.song.id in unwanted)


def test_a_requested_version_is_not_penalised() -> None:
    live = SpotifyTrack(0, "Blinding Lights - Live", ("The Weeknd",), "", 260.0)
    candidate = score(
        live, video(2, "The Weeknd - Blinding Lights (Live)", "The Weeknd", 261)
    )
    assert candidate.score >= LIKELY
    assert not any("version" in r for r in candidate.reasons)


def test_duration_is_a_secondary_signal() -> None:
    near = score(WANTED, video(1, "The Weeknd - Blinding Lights", "Uploader", 201))
    far = score(WANTED, video(2, "The Weeknd - Blinding Lights", "Uploader", 420))
    assert near.score > far.score
    assert any("length differs" in r for r in far.reasons)


def test_wrong_song_by_the_right_artist_is_weak() -> None:
    other = score(
        WANTED, video(1, "The Weeknd - Save Your Tears", "The Weeknd - Topic", 215)
    )
    assert other.label == "weak"


@pytest.mark.parametrize(
    ("raw", "core"),
    [
        ("Hey Jude - Remastered 2015", "hey jude"),
        ("Señorita (feat. Someone)", "senorita"),
        ("Don\u2019t Start Now [Official Video]", "don't start now"),
        ("Rock & Roll", "rock and roll"),
    ],
)
def test_titles_are_normalised(raw: str, core: str) -> None:
    assert core_title(raw) == core


async def test_matcher_limits_concurrent_searches() -> None:
    running = peak = 0

    async def search(query: str, limit: int) -> list[Song]:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return [video(1, query, "x", 200)]

    matcher = Matcher(search, concurrency=2, pause=0)
    tracks = [SpotifyTrack(i, f"Song {i}", ("A",), "", 200.0) for i in range(12)]
    await asyncio.gather(*(matcher.candidates(t) for t in tracks))
    assert peak == 2


# ---------------------------------------------------------- the library


def spotify_playlist(n: int = 6) -> SpotifyPlaylist:
    tracks = [
        SpotifyTrack(
            i, f"Song {i}", (f"Artist {i}",), f"Album {i}", 180.0 + i, f"{i:022d}"
        )
        for i in range(n)
    ]
    tracks[2] = SpotifyTrack(
        2, "Gone", (), available=False, note="removed from Spotify"
    )
    tracks[4] = SpotifyTrack(4, "Song 1", ("Artist 1",), "Album 1", 181.0, f"{1:022d}")
    return SpotifyPlaylist(
        "Road Trip", "Anna", n, PID, f"https://x/{PID}", tracks=tuple(tracks)
    )


def test_import_keeps_order_and_never_drops_tracks(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    library = Library.open(path)
    library.create_playlist("Road Trip")  # the name is taken: no overwrite
    playlist, info = library.create_import(spotify_playlist())
    assert playlist.name == "Road Trip (2)"
    assert info.counts == {"pending": 4, "unavailable": 1, "duplicate": 1}
    library.close()
    reopened = Library.open(path)  # persists across restarts
    items = reopened.import_items(playlist.id)
    assert [i.position for i in items] == list(range(6))  # every entry kept
    assert [i.status for i in items] == [
        "pending",
        "pending",
        "unavailable",
        "pending",
        "duplicate",
        "pending",
    ]
    entries = reopened.playlist_tracks(playlist.id)
    assert [t.song.title for t in entries] == ["Song 0", "Song 1", "Song 3", "Song 5"]
    assert all(is_placeholder(t.song.id) and not t.available for t in entries)
    assert reopened.playlists()[0].name == "Road Trip"  # the original is untouched


def test_matching_replaces_placeholders_in_place() -> None:
    library = Library.memory()
    playlist, _ = library.create_import(spotify_playlist())
    match = Song(
        id="vid00000001",
        title="Song 3",
        artist="Artist 3",
        duration=183,
        source_url="https://www.youtube.com/watch?v=vid00000001",
        thumbnail_url="https://i.scdn.co/cover",
    )
    assert (
        library.set_match(playlist.id, 3, match, status="review", confidence=0.7)
        == "review"
    )
    titles = [t.song.id for t in library.playlist_tracks(playlist.id)]
    assert titles[2] == "vid00000001"  # same place as before
    assert library.import_meta("vid00000001") == {
        "title": "Song 3",
        "artist": "Artist 3",
        "album": "Album 3",
    }
    library.add_favorite(match)
    library.delete_playlist(playlist.id)  # likes survive; import rows go
    assert library.favorite_ids() == {"vid00000001"}
    assert library.import_info(playlist.id) is None


# ------------------------------------------------------------ in the app


class YouTube:
    """Fake search: one official result per known song, nothing for others."""

    name = "youtube"
    browsable = False

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.fail = False

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("network down")
        await asyncio.sleep(0)
        artist, _, title = query.partition(" - ")
        if title.startswith("Song") and int(title.split()[1]) % 3 != 2:
            n = int(title.split()[1])
            return [
                video(100 + n, f"{title} (Lyrics)", "lyrics channel", 180 + n),
                video(n, title, f"{artist} - Topic", 180 + n),
            ]
        return [video(999, "Something else entirely", "Random", 61)]


def make_app(library: Library, youtube: YouTube, api: FakeSpotify) -> PixelFMApp:
    library.set_pref("spotify_client_id", "cid")
    return PixelFMApp(
        config=Config(),
        provider=youtube,
        artwork=ArtworkService(lambda url: image_bytes()),
        player=SimulatedPlayer(load_delay=0),
        library=library,
        unicode=True,
        truecolor=True,
        welcome=False,
        media=None,
        token_store=signed_in(),
        spotify_options={"http": api, "sleep": lambda s: asyncio.sleep(0)},
    )


async def until(predicate: Callable[[], bool], pilot, limit: float = 6.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


def status(app: PixelFMApp) -> str:
    return app.query_one(StatusBar).message


async def test_import_and_match_a_spotify_link_end_to_end(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    youtube, api = YouTube(), FakeSpotify(total=12)
    app = make_app(Library.open(path), youtube, api)
    app._matcher_instance = Matcher(youtube.search, pause=0)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "I")
        await pilot.pause()
        app.screen.query_one(
            Input
        ).value = f"https://open.spotify.com/playlist/{PID}?si=x"
        await pilot.press("enter")
        await until(lambda: isinstance(app.screen, ImportPreview), pilot)
        await pilot.press("m")  # import and match
        await until(
            lambda: app._matching is None and "Matching done" in status(app), pilot
        )
        nav = app.query_one(PlaylistNav)
        pid = nav.playlist_id
        assert pid is not None
        info = app.library.import_info(pid)
        assert info is not None and info.total == 12
        # Order kept; matches use the Topic recording, never the lyric video.
        tracks = app.library.playlist_tracks(pid)
        for entry in tracks:
            if not is_placeholder(entry.song.id):
                assert not entry.song.id.startswith("vid000001")  # lyric videos
        assert info.matched + info.unmatched + info.skipped == 12
        heading = str(app.query_one("#playlist-heading").render())
        assert "matched" in heading and "unmatched" in heading
        # Played tracks show the Spotify metadata and artwork.
        matched = next(t for t in tracks if not is_placeholder(t.song.id))
        assert matched.song.thumbnail_url.startswith("https://i.scdn.co/")
        assert app._display_meta(matched.song)["album"].startswith("Album")

        # Play all and shuffle skip unmatched placeholders.
        await pilot.press("Z")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        assert not any(is_placeholder(e.song.id) for e in app.queue.entries)

        # Review: pick an unmatched track and choose a candidate by hand.
        await pilot.press("4")
        app.query_one(PlaylistNav).focus()
        await pilot.press("M")
        await until(lambda: isinstance(app.screen, MatchReview), pilot)
        review = app.screen.query_one(OptionList)
        review.highlighted = 0  # unmatched/unsure first
        await pilot.press("enter")
        await until(lambda: isinstance(app.screen, CandidatePicker), pilot)
        await pilot.press("enter")  # the first candidate
        await until(lambda: isinstance(app.screen, MatchReview), pilot)
        await pilot.press("escape")
        await pilot.press("q")

    reopened = Library.open(path)  # survives a restart
    items = reopened.import_items(pid)
    assert len(items) == 12
    assert any(i.note == "chosen by you" for i in items)


async def test_unmatched_tracks_cannot_play_and_say_why() -> None:
    library = Library.memory()
    playlist, _ = library.create_import(spotify_playlist())
    app = make_app(library, YouTube(), FakeSpotify())
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4")
        app.query_one(PlaylistTable).focus()
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert app.player.get_state().status is S.IDLE
        assert "isn't matched to a YouTube video" in status(app)
        assert library.playlist(playlist.id).count == 4


async def test_matching_can_be_stopped_and_resumed() -> None:
    library = Library.memory()
    playlist, _ = library.create_import(spotify_playlist(40))
    gate = asyncio.Event()

    class Slow(YouTube):
        async def search(self, query: str, limit: int = 20) -> list[Song]:
            if len(self.queries) >= 6:
                await gate.wait()
            return await super().search(query, limit)

    youtube = Slow()
    app = make_app(library, youtube, FakeSpotify())
    app._matcher_instance = Matcher(youtube.search, pause=0)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4")
        app.match_playlist(playlist.id)
        await until(lambda: len(youtube.queries) >= 6, pilot)
        await pilot.press("X")  # stop
        await pilot.pause(0.2)
        assert app._matching is None and "stopped" in status(app)
        info = library.import_info(playlist.id)
        assert info is not None and info.counts.get("pending", 0) > 20  # kept for later
        gate.set()
        app._matcher_instance = Matcher(youtube.search, pause=0)
        app.match_playlist(playlist.id)  # resume: only what isn't matched
        await until(lambda: "Matching done" in status(app), pilot)
        info = library.import_info(playlist.id)
        assert info is not None and info.counts.get("pending", 0) == 0


async def test_network_errors_never_corrupt_the_library() -> None:
    library = Library.memory()
    playlist, _ = library.create_import(spotify_playlist())
    youtube = YouTube()
    youtube.fail = True
    api = FakeSpotify()
    api.outage = 50
    app = make_app(library, youtube, api)
    app._matcher_instance = Matcher(youtube.search, pause=0)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4")
        app.match_playlist(playlist.id)
        await until(lambda: "Matching done" in status(app), pilot)
        info = library.import_info(playlist.id)
        assert info is not None and info.matched == 0 and info.unmatched == 4
        assert library.playlist(playlist.id).count == 4  # nothing lost
        app.import_source(f"https://open.spotify.com/playlist/{PID}")
        await until(lambda: "Can't reach Spotify" in status(app), pilot)
        assert len(library.playlists()) == 1  # no half-made playlist


async def test_spotify_setup_is_asked_for_first(tmp_path: Path) -> None:
    library = Library.memory()
    app = make_app(library, YouTube(), FakeSpotify(total=3))
    library.set_pref("spotify_client_id", "")
    async with app.run_test(size=(140, 42)) as pilot:
        app.import_source(f"https://open.spotify.com/playlist/{PID}")
        await until(lambda: app.screen.__class__.__name__ == "SpotifySetup", pilot)
        app.screen.query_one(Input).value = "my-client-id"
        await pilot.press("enter")
        await until(
            lambda: library.get_pref("spotify_client_id") == "my-client-id", pilot
        )


async def test_bad_links_and_export_files(tmp_path: Path) -> None:
    library = Library.memory()
    app = make_app(library, YouTube(), FakeSpotify())
    csv_file = tmp_path / "Mix.csv"
    csv_file.write_text("title,artist\nSong 1,Artist 1\nSong 2,Artist 2\n")
    async with app.run_test(size=(140, 42)) as pilot:
        app.import_source(f"https://open.spotify.com/album/{PID}")
        await until(lambda: "album link" in status(app), pilot)
        app.import_source(str(csv_file))
        await until(lambda: isinstance(app.screen, ImportPreview), pilot)
        await pilot.press("i")
        await until(lambda: any(p.name == "Mix" for p in library.playlists()), pilot)
        imported = next(p for p in library.playlists() if p.name == "Mix")
        info = library.import_info(imported.id)
        assert info is not None and info.source == "file" and info.total == 2
