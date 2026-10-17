"""Downloads: manager states, real yt-dlp over loopback, and app integration."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import http.server
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.widgets import Input

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.downloads import (
    DownloadCancelled,
    DownloadError,
    DownloadManager,
    DownloadResult,
    DownloadStatus,
    _cleanup_partial,
    default_downloads_dir,
    eligible_for_download,
    human_size,
    storage_summary,
    ytdlp_download,
)
from pixelfm.services.ffplay import FFplayPlayer
from pixelfm.services.library import Library
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.services.streams import LocalFirstResolver
from pixelfm.widgets import ConfirmDialog, DownloadsTable, StatusBar, TextPrompt
from tests.conftest import image_bytes, make_song
from tests.fakes import FakeReaper, FakeResolver, FakeSpawner

SONG = make_song(1)


def fake_downloader(
    directory_files: dict[str, bytes] | None = None,
    *,
    fail: str | None = None,
    gate: threading.Event | None = None,
) -> Callable[..., DownloadResult]:
    def download(song: Song, directory: Path, progress, cancelled) -> DownloadResult:  # type: ignore[no-untyped-def]
        for step in range(1, 5):
            if gate is not None:
                gate.wait(5)
            if cancelled():
                raise DownloadCancelled
            progress(step * 25, 100, 1000.0)
            time.sleep(0.01)
        if fail:
            raise DownloadError(fail)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{song.artist} - {song.title} [{song.id}].m4a"
        path.write_bytes(b"audio" * 10)
        return DownloadResult(path, path.stat().st_size, "m4a")

    return download


async def wait_for(predicate: Callable[[], bool], limit: float = 5.0) -> None:
    deadline = time.monotonic() + limit
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.02)


# ------------------------------------------------------------- unit level


def test_eligibility_and_paths(tmp_path: Path) -> None:
    assert eligible_for_download(SONG) is None
    assert "Live" in (eligible_for_download(make_song(2, duration=0)) or "")
    assert default_downloads_dir({"PIXELFM_DOWNLOADS_DIR": str(tmp_path)}) == tmp_path
    assert human_size(512) == "512 B" and human_size(3 * 1024 * 1024) == "3.0 MB"
    assert "2 files" in storage_summary(tmp_path, [10, 20])


def test_cleanup_only_removes_partial_files(tmp_path: Path) -> None:
    done = tmp_path / "song.m4a"
    done.write_bytes(b"finished")
    part = tmp_path / "song.m4a.part"
    part.write_bytes(b"half")
    _cleanup_partial(str(part))
    _cleanup_partial(str(done))  # a completed file is never deleted
    assert done.exists() and not part.exists()


async def test_manager_success_with_real_progress(tmp_path: Path) -> None:
    updates = []
    manager = DownloadManager(
        lambda: tmp_path, updates.append, fake_downloader(), progress_interval=0
    )
    assert manager.enqueue(SONG)
    assert not manager.enqueue(SONG)  # already queued
    await wait_for(lambda: manager.jobs[SONG.id].status is DownloadStatus.DONE)
    statuses = [u.status for u in updates]
    assert statuses[0] is DownloadStatus.QUEUED and statuses[-1] is DownloadStatus.DONE
    fractions = [
        u.fraction
        for u in updates
        if u.status is DownloadStatus.DOWNLOADING and u.fraction
    ]
    assert fractions == sorted(fractions) and fractions
    assert (
        manager.jobs[SONG.id].path is not None and manager.jobs[SONG.id].path.exists()
    )


async def test_manager_failure(tmp_path: Path) -> None:
    manager = DownloadManager(
        lambda: tmp_path, lambda u: None, fake_downloader(fail="HTTP 403")
    )
    manager.enqueue(SONG)
    await wait_for(lambda: manager.jobs[SONG.id].status is DownloadStatus.FAILED)
    assert manager.jobs[SONG.id].error == "HTTP 403"
    assert list(tmp_path.iterdir()) == []


async def test_manager_cancel_running_and_queued(tmp_path: Path) -> None:
    gate = threading.Event()
    manager = DownloadManager(
        lambda: tmp_path, lambda u: None, fake_downloader(gate=gate)
    )
    other = make_song(2)
    manager.enqueue(SONG)
    manager.enqueue(other)
    await wait_for(lambda: manager.jobs[SONG.id].status is DownloadStatus.DOWNLOADING)
    assert manager.cancel(other.id)  # still queued: cancelled immediately
    assert manager.jobs[other.id].status is DownloadStatus.CANCELLED
    assert manager.cancel(SONG.id)
    gate.set()
    await wait_for(lambda: manager.jobs[SONG.id].status is DownloadStatus.CANCELLED)
    assert list(tmp_path.iterdir()) == []  # nothing marked finished, nothing left
    assert not manager.cancel(SONG.id)


# --------------------------------------- real yt-dlp against a local server


class _Slow(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def handle_one_request(self) -> None:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            super().handle_one_request()

    def copyfile(self, source, destination) -> None:  # type: ignore[no-untyped-def]
        while chunk := source.read(8192):
            destination.write(chunk)
            time.sleep(0.01)


@pytest.fixture
def media_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    root = tmp_path / "srv"
    root.mkdir()
    (root / "track.m4a").write_bytes(image_bytes() * 60)  # opaque bytes are fine
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Slow, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_ytdlp_download_real_pipeline(media_server: str, tmp_path: Path) -> None:
    """yt-dlp's generic extractor, real HTTP, loopback only (no internet)."""
    song = Song(
        id="local000001",
        title="Tone",
        artist="Tester",
        duration=60,
        source_url=f"{media_server}/track.m4a",
    )
    seen: list[tuple[int, int | None]] = []
    result = ytdlp_download(
        song, tmp_path / "out", lambda d, t, s: seen.append((d, t)), lambda: False
    )
    assert result.path.is_file() and result.size > 0
    assert "[local000001]" in result.path.name
    assert not list((tmp_path / "out").glob("*.part"))
    assert seen and seen[-1][0] > 0
    # A second download never overwrites the existing file.
    mtime = result.path.stat().st_mtime
    again = ytdlp_download(song, tmp_path / "out", lambda *a: None, lambda: False)
    assert again.path == result.path and again.path.stat().st_mtime == mtime


def test_ytdlp_download_cancel_leaves_no_partial(
    media_server: str, tmp_path: Path
) -> None:
    song = Song(
        id="local000002",
        title="Tone",
        artist="Tester",
        duration=60,
        source_url=f"{media_server}/track.m4a",
    )
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(DownloadCancelled):
        ytdlp_download(song, tmp_path / "out", lambda *a: None, cancelled)
    assert [p for p in (tmp_path / "out").iterdir()] == []


def test_ytdlp_download_network_failure(tmp_path: Path) -> None:
    song = Song(
        id="local000003",
        title="Gone",
        artist="Nobody",
        duration=60,
        source_url="http://127.0.0.1:9/missing.m4a",
    )
    with pytest.raises(DownloadError):
        ytdlp_download(song, tmp_path / "out", lambda *a: None, lambda: False)
    assert not list((tmp_path / "out").glob("*"))


# --------------------------------------------------------- app integration


class Provider:
    name = "youtube"
    browsable = False

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        return [SONG, make_song(2)]


def make_app(library: Library, downloader, tmp_path: Path, player=None) -> PixelFMApp:  # type: ignore[no-untyped-def]
    library.set_pref("downloads_dir", str(tmp_path / "Music"))
    return PixelFMApp(
        config=Config(),
        provider=Provider(),
        artwork=ArtworkService(lambda url: image_bytes()),
        player=player or SimulatedPlayer(load_delay=0),
        library=library,
        unicode=True,
        truecolor=True,
        welcome=False,
        downloader=downloader,
    )


async def search(app: PixelFMApp, pilot) -> None:  # type: ignore[no-untyped-def]
    await pilot.press("slash")
    app.query_one("#search", Input).value = "x"
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_download_flow_requires_notice_and_records(tmp_path: Path) -> None:
    library = Library.memory()
    app = make_app(library, fake_downloader(), tmp_path)
    async with app.run_test(size=(140, 42)) as pilot:
        await search(app, pilot)
        await pilot.press("D")
        assert isinstance(app.screen, ConfirmDialog)  # rights notice first time
        await pilot.press("n")
        assert library.download_paths() == {}
        await pilot.press("D", "y")
        for _ in range(100):
            await pilot.pause(0.05)
            if library.download_paths():
                break
        [path] = library.download_paths().values()
        assert path.is_file() and path.parent == tmp_path / "Music"
        assert "Saved" in app.query_one(StatusBar).message
        await pilot.press("j", "D")  # notice is remembered: no dialog
        assert not isinstance(app.screen, ConfirmDialog)
        await pilot.press("6")
        await pilot.pause(0.3)
        table = app.query_one(DownloadsTable)
        assert SONG in table.songs
        # Liking is independent of downloading.
        assert library.favorite_ids() == set()


async def test_missing_file_is_detected_and_cleaned(tmp_path: Path) -> None:
    library = Library.memory()
    gone = tmp_path / "gone.m4a"
    library.add_download(SONG, gone, 123, "m4a")
    app = make_app(library, fake_downloader(), tmp_path)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("6")
        for _ in range(50):
            await pilot.pause(0.05)
            if app._missing_downloads:
                break
        assert app._missing_downloads == {SONG.id}
        table = app.query_one(DownloadsTable)
        assert table.rows_data[0].extra == "MISSING"
        await pilot.press("X", "y")
        await pilot.pause(0.2)
        assert library.download_paths() == {}


async def test_relink_and_delete(tmp_path: Path) -> None:
    library = Library.memory()
    library.add_download(SONG, tmp_path / "old.m4a", 1, "m4a")
    library.add_favorite(SONG)
    new = tmp_path / "new place.m4a"
    new.write_bytes(b"x" * 99)
    app = make_app(library, fake_downloader(), tmp_path)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("6")
        await pilot.pause(0.3)
        await pilot.press("L")
        assert isinstance(app.screen, TextPrompt)
        app.screen.query_one(Input).value = str(new)
        await pilot.press("enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if library.download_paths().get(SONG.id) == new:
                break
        assert library.download_paths()[SONG.id] == new
        app.query_one(DownloadsTable).focus()
        await pilot.press("d", "y")
        for _ in range(50):
            await pilot.pause(0.05)
            if not new.exists():
                break
        assert not new.exists() and library.download_paths() == {}
        assert library.favorite_ids() == {SONG.id}  # the like is kept


async def test_downloaded_track_plays_from_the_local_file(tmp_path: Path) -> None:
    library = Library.memory()
    local = tmp_path / "Music" / "Artist 1 - Track 1 [song0000001].m4a"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"audio")
    library.add_download(SONG, local, 5, "m4a")
    remote = FakeResolver()
    spawner = FakeSpawner()
    app_holder: dict[str, PixelFMApp] = {}

    def lookup(song_id: str) -> Path | None:
        return app_holder["app"]._local_path(song_id)

    player = FFplayPlayer(
        LocalFirstResolver(remote, lookup),  # type: ignore[arg-type]
        spawn=spawner,
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        notify_interval=0,
    )
    app = make_app(library, fake_downloader(), tmp_path, player=player)
    app_holder["app"] = app
    async with app.run_test(size=(120, 40)) as pilot:
        await search(app, pilot)
        await pilot.press("enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if spawner.processes:
                break
        args = spawner.last.args
        assert args[-1] == f"file:{local}"
        assert remote.calls == []  # no network resolution at all
        spawner.last.progress(1.0)
        await pilot.pause(0.2)
        assert app.player.get_state().local is True
