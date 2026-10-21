"""macOS media controls: helper protocol, command routing, metadata.

These run everywhere with a fake helper speaking the same protocol. They do
not prove that macOS routes real media keys to the helper; that needs a real
Mac (``pixelfm --media-test``).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.services import media_session
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.library import Library
from pixelfm.services.media_session import (
    MediaSession,
    NowPlayingInfo,
    helper_path,
    unavailable_reason,
)
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.state import PlaybackStatus
from tests.conftest import image_bytes, make_song

S = PlaybackStatus
FAKE = Path(__file__).parent / "helpers" / "fake_media_helper.py"
SONGS = [make_song(n) for n in range(1, 6)]


def fake_executable(tmp_path: Path) -> Path:
    """A launcher for the fake helper using this interpreter."""
    script = tmp_path / "fake-helper"
    script.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}'\n")
    script.chmod(0o755)
    return script


def messages(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines() if line]


async def until(predicate: Callable[[], bool], pilot=None, limit: float = 4.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        if pilot is not None:
            await pilot.pause(0.02)
        else:
            await asyncio.sleep(0.02)


# ---------------------------------------------------------- the session


async def test_helper_protocol_and_clean_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_MEDIA_LOG", str(log))
    monkeypatch.setenv("FAKE_MEDIA_COMMANDS", "toggle,seek:42")
    received: list[tuple[str, float | None]] = []
    session = MediaSession(
        lambda n, p: received.append((n, p)), fake_executable(tmp_path)
    )
    assert await session.start()
    await asyncio.wait_for(session.ready.wait(), 5)
    info = NowPlayingInfo("Title", "Artist", "Album", 200.0, 12.0, True, None)
    session.publish(info)
    session.publish(info)  # unchanged: not sent again
    session.publish(NowPlayingInfo("Title", "Artist", "Album", 200.0, 50.0, False))
    await until(lambda: len(received) == 2)
    assert received == [("toggle", None), ("seek", 42.0)]
    await session.aclose()
    assert not session.running
    sent = messages(log)
    updates = [m for m in sent if m["type"] == "update"]
    assert len(updates) == 2 and updates[0]["title"] == "Title"
    assert updates[1]["playing"] is False and updates[1]["elapsed"] == 50.0
    assert sent[-2]["type"] == "quit" and sent[-1]["type"] == "exited"


def test_repeated_key_events_are_ignored() -> None:
    clock = [100.0]
    received: list[str] = []
    session = MediaSession(
        lambda n, p: received.append(n), None, clock=lambda: clock[0]
    )
    session._command({"name": "toggle"})
    clock[0] += 0.05
    session._command({"name": "toggle"})  # repeat of the same press
    clock[0] += 0.5
    session._command({"name": "toggle"})  # a new press
    session._command({"name": "next"})
    session._command({"name": "format-disk"})  # unknown: ignored
    assert received == ["toggle", "toggle", "next"]


async def test_missing_or_broken_helper_fails_gracefully(tmp_path: Path) -> None:
    assert not await MediaSession(lambda n, p: None, None).start()
    broken = tmp_path / "broken"
    broken.write_bytes(b"\xcf\xfa\xed\xfe garbage")
    broken.chmod(0o755)
    session = MediaSession(lambda n, p: None, broken)
    assert not await session.start()
    session.publish(NowPlayingInfo("x", "y"))  # no process: a no-op
    await session.aclose()


def test_availability_is_explained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(media_session.sys, "platform", "linux")
    assert "only available on macOS" in (unavailable_reason({}) or "")
    assert helper_path({}) is None
    monkeypatch.setattr(media_session.sys, "platform", "darwin")
    assert "turned off" in (unavailable_reason({"PIXELFM_MEDIA_CONTROLS": "off"}) or "")
    monkeypatch.setattr(media_session.platform, "mac_ver", lambda: ("15.1", "", ""))
    missing = {"PIXELFM_MEDIA_HELPER": str(tmp_path / "nope")}
    assert "helper is missing" in (unavailable_reason(missing) or "")
    monkeypatch.setattr(media_session.platform, "mac_ver", lambda: ("11.7", "", ""))
    assert "macOS 12" in (unavailable_reason({}) or "")


# ------------------------------------------------------------ in the app


class Provider:
    name = "youtube"
    browsable = False

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        return SONGS


def make_app(media: MediaSession | None) -> PixelFMApp:
    return PixelFMApp(
        config=Config(),
        provider=Provider(),
        artwork=ArtworkService(lambda url: image_bytes()),
        player=SimulatedPlayer(load_delay=0),
        library=Library.memory(),
        unicode=True,
        truecolor=True,
        welcome=False,
        media=media,
    )


class Recorder:
    """A session double that records what the app publishes."""

    def __init__(self) -> None:
        self.published: list[NowPlayingInfo | None] = []
        self.closed = False
        self.ready = asyncio.Event()

    async def start(self) -> bool:
        return True

    def publish(self, info: NowPlayingInfo | None) -> None:
        if not self.published or self.published[-1] != info:
            self.published.append(info)

    async def aclose(self) -> None:
        self.closed = True

    def shutdown(self) -> None:
        self.closed = True


async def test_media_commands_use_the_same_actions_as_the_keys() -> None:
    recorder = Recorder()
    app = make_app(recorder)  # type: ignore[arg-type]
    async with app.run_test(size=(140, 42)) as pilot:
        app.queue.extend(SONGS)
        await pilot.press("z")  # global shuffle: next must follow it
        app._play_entry(app.queue.advance(manual=True))  # type: ignore[arg-type]
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        order = [e.song for e in app.queue.entries]
        entries_before = len(app.queue.entries)

        app._on_media_command("pause", None)
        await until(lambda: app.player.get_state().status is S.PAUSED, pilot)
        app._on_media_command("pause", None)  # already paused: nothing happens
        app._on_media_command("play", None)
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        app._on_media_command("toggle", None)
        await until(lambda: app.player.get_state().status is S.PAUSED, pilot)
        app._on_media_command("toggle", None)
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)

        app._on_media_command("next", None)
        await until(lambda: app.player.get_state().song == order[1], pilot)
        app._on_media_command("previous", None)
        await until(lambda: app.player.get_state().song == order[0], pilot)
        app._on_media_command("seek", 90.0)
        await until(lambda: app.player.get_state().position >= 90, pilot)
        assert len(app.queue.entries) == entries_before  # no duplicates

        # The system saw each change: title, play state, position.
        latest = recorder.published[-1]
        assert latest is not None and latest.title == order[0].title
        assert latest.playing and latest.elapsed >= 90
        states = [p.playing for p in recorder.published if p is not None]
        assert False in states and True in states
        await pilot.press("x")  # stop: Now Playing is cleared
        await until(lambda: recorder.published[-1] is None, pilot)
        await pilot.press("q")
    assert recorder.closed


async def test_app_runs_the_helper_process_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_MEDIA_LOG", str(log))
    monkeypatch.setenv("FAKE_MEDIA_COMMANDS", "toggle")
    holder: dict[str, PixelFMApp] = {}
    session = MediaSession(
        lambda n, p: holder["app"]._on_media_command(n, p), fake_executable(tmp_path)
    )
    app = make_app(session)
    holder["app"] = app
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[0]))
        # The fake helper presses play/pause after the first update.
        await until(lambda: app.player.get_state().status is S.PAUSED, pilot, 6)
        await pilot.press("q")
    await until(lambda: '"exited"' in log.read_text())
    sent = messages(log)
    titles = {m.get("title") for m in sent if m["type"] == "update"}
    assert SONGS[0].title in titles
    assert any(m["type"] == "quit" for m in sent)  # cleared on exit


async def test_without_a_helper_the_app_works_normally() -> None:
    app = make_app(None)
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[0]))
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        app._publish_now_playing()  # no session: harmless
        await pilot.press("q")
