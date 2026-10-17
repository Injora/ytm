"""FFplayPlayer lifecycle with a fake subprocess; no audio, no network."""

from __future__ import annotations

import asyncio
import logging

import pytest

from pixelfm.services.ffplay import (
    FFPLAY_MISSING,
    FFplayPlayer,
    build_args,
    classify_stderr,
    parse_progress,
)
from pixelfm.services.streams import StreamError, StreamInfo
from pixelfm.state import PlaybackState, PlaybackStatus
from tests.conftest import make_song
from tests.fakes import FakeReaper, FakeResolver, FakeSpawner, settle

S = PlaybackStatus
SONG = make_song(1, duration=200)
OTHER = make_song(2, duration=100)


def make_player(
    spawner: FakeSpawner | None = None,
    resolver: FakeResolver | None = None,
    *,
    which: str | None = "/opt/homebrew/bin/ffplay",
    **kwargs: object,
) -> tuple[FFplayPlayer, FakeSpawner, FakeResolver, FakeReaper, list[PlaybackState]]:
    spawner = spawner or FakeSpawner()
    resolver = resolver or FakeResolver()
    reaper = FakeReaper()
    options: dict[str, object] = dict(
        volume_debounce=0, retry_delays=(0, 0), kill_timeout=0.05, notify_interval=0
    )
    options.update(kwargs)
    player = FFplayPlayer(
        resolver,
        spawn=spawner,
        reaper=reaper,
        which=lambda name: which,
        **options,  # type: ignore[arg-type]
    )
    states: list[PlaybackState] = []
    player.subscribe(states.append)
    return player, spawner, resolver, reaper, states


async def playing(player: FFplayPlayer, spawner: FakeSpawner, at: float = 1.0) -> None:
    await player.play(SONG)
    spawner.last.progress(at)
    await settle()


# ------------------------------------------------------------- pure helpers


def test_parse_progress() -> None:
    assert parse_progress("   12.34 M-A:  0.000 fd=   0 aq=   10KB") == 12.34
    assert parse_progress("\x1b[2K   3.50 M-A: 0.0") == 3.5
    assert parse_progress("  98.00 A-V: -0.012 fd=0") == 98.0
    assert parse_progress("  -0.02 M-A:  0.000 fd=   0") == 0.0  # clamped
    assert parse_progress("    nan    :  0.000 fd=   0") is None
    assert parse_progress("[https @ 0x1] Opening 'x' for reading") is None


def test_build_args_is_an_argv_not_a_shell_string() -> None:
    stream = StreamInfo(
        url="https://media.example.com/a.m4a",
        headers=(("User-Agent", "UA; x"), ("Accept", "*/*")),
    )
    args = build_args("/bin/ffplay", stream, start=42.5, volume=150)
    assert args[0] == "/bin/ffplay"
    assert args[-2:] == ["-i", "https://media.example.com/a.m4a"]
    assert args[args.index("-ss") + 1] == "42.50"
    assert args[args.index("-volume") + 1] == "100"  # clamped
    assert args[args.index("-user_agent") + 1] == "UA; x"
    assert args[args.index("-headers") + 1] == "Accept: */*\r\n"
    assert "file" not in args[args.index("-protocol_whitelist") + 1].split(",")
    assert {"-nodisp", "-autoexit", "-stats"} <= set(args)


def test_build_args_live_ignores_start() -> None:
    stream = StreamInfo(url="https://x/live.m3u8", is_live=True)
    assert "-ss" not in build_args("ffplay", stream, start=30)
    assert "-ss" not in build_args("ffplay", StreamInfo(url="https://x/a"), start=0)


@pytest.mark.parametrize(
    "lines,kind",
    [
        (["HTTP error 403 Forbidden"], "expired"),
        (["Server returned 403 Forbidden (access denied)"], "expired"),
        (
            ["[tls @ 0x1] Error in the pull function.", "Connection reset by peer"],
            "network",
        ),
        (["https://x: Input/output error"], "network"),
        (["Invalid data found when processing input"], "fatal"),
        (["SDL_OpenAudio (2 channels, 44100 Hz): no device"], "device"),
        (["harmless warning"], None),
        ([], None),
    ],
)
def test_classify_stderr(lines: list[str], kind: str | None) -> None:
    problem = classify_stderr(lines)
    assert (problem.kind if problem else None) == kind


# ---------------------------------------------------------------- lifecycle


async def test_play_reports_real_position() -> None:
    player, spawner, resolver, reaper, states = make_player()
    await player.play(SONG)
    assert player.get_state().status is S.LOADING
    assert resolver.calls == [(SONG.id, False)]
    proc = spawner.last
    assert proc.pid in reaper.tracked

    proc.progress(0.5)
    await settle()
    assert player.get_state().status is S.PLAYING
    proc.progress(7.25)
    await settle()
    state = player.get_state()
    assert state.position == 7.25 and state.duration == 200
    assert [s.status for s in states][:2] == [S.LOADING, S.LOADING]


async def test_only_one_process_at_a_time() -> None:
    player, spawner, *_ = make_player()
    await playing(player, spawner)
    first = spawner.last
    await player.play(OTHER)
    assert not first.alive and first.signals == ["TERM"]
    assert spawner.live == [spawner.last]
    assert player.get_state().song == OTHER


async def test_stale_resolve_is_dropped() -> None:
    gate = asyncio.Event()
    resolver = FakeResolver(gate=gate)
    player, spawner, *_ = make_player(resolver=resolver)
    first = asyncio.create_task(player.play(SONG))
    await settle()
    second = asyncio.create_task(player.play(OTHER))
    await settle()
    gate.set()
    await asyncio.gather(first, second)
    assert len(spawner.processes) == 1
    assert player.get_state().song == OTHER


async def test_pause_stops_process_and_resume_restarts_at_position() -> None:
    player, spawner, _, reaper, _ = make_player()
    await playing(player, spawner, at=42.0)
    proc = spawner.last

    await player.pause()
    assert player.get_state().status is S.PAUSED
    assert player.get_state().position == 42.0
    assert not proc.alive and proc.pid not in reaper.tracked
    assert player.process is None

    await player.resume()
    resumed = spawner.last
    assert resumed is not proc
    assert resumed.arg("-ss") == "42.00"
    resumed.progress(42.1)
    await settle()
    assert player.get_state().status is S.PLAYING


async def test_toggle_cycles_play_pause() -> None:
    player, spawner, *_ = make_player()
    await playing(player, spawner)
    await player.toggle()
    assert player.get_state().status is S.PAUSED
    await player.toggle()
    assert player.get_state().status is S.LOADING
    assert len(spawner.live) == 1


async def test_pause_while_resolving_cancels_launch() -> None:
    gate = asyncio.Event()
    player, spawner, *_ = make_player(resolver=FakeResolver(gate=gate))
    task = asyncio.create_task(player.play(SONG))
    await settle()
    await player.pause()
    gate.set()
    await task
    assert spawner.processes == []
    assert player.get_state().status is S.PAUSED


async def test_stop_kills_and_cleans_up() -> None:
    player, spawner, _, reaper, _ = make_player()
    await playing(player, spawner)
    await player.stop()
    assert player.get_state().status is S.STOPPED
    assert spawner.live == [] and reaper.tracked == set()
    await player.stop()  # idempotent


async def test_stubborn_process_is_killed() -> None:
    player, spawner, *_ = make_player(FakeSpawner(stubborn=True))
    await playing(player, spawner)
    proc = spawner.last
    await player.stop()
    assert proc.signals == ["TERM", "KILL"] and not proc.alive


async def test_natural_end() -> None:
    player, spawner, _, reaper, states = make_player()
    await playing(player, spawner, at=150)
    proc = spawner.last
    proc.progress(198.6)
    proc.exit(0)
    await settle()
    state = player.get_state()
    assert state.status is S.ENDED and state.position == 200
    assert proc.pid not in reaper.tracked
    assert states[-1].status is S.ENDED


async def test_unknown_duration_ends_cleanly() -> None:
    player, spawner, *_ = make_player()
    await player.play(make_song(3, duration=0))
    spawner.last.progress(10)
    spawner.last.exit(0)
    await settle()
    assert player.get_state().status is S.ENDED


async def test_network_drop_recovers_at_position() -> None:
    player, spawner, resolver, *_ = make_player()
    await playing(player, spawner, at=60)
    dropped = spawner.last
    dropped.emit("[tls @ 0x1] Connection reset by peer\n")
    dropped.exit(0)  # ffplay exits 0 even on errors
    await settle(30)
    assert len(spawner.processes) == 2
    assert spawner.last.arg("-ss") == "60.00"
    assert resolver.calls[-1] == (SONG.id, False)
    assert player.get_state().status is S.LOADING


async def test_expired_url_refreshes_stream() -> None:
    player, spawner, resolver, *_ = make_player()
    await playing(player, spawner, at=30)
    spawner.last.emit("Server returned 403 Forbidden (access denied)\n")
    spawner.last.exit(0)
    await settle(30)
    assert resolver.calls[-1] == (SONG.id, True)
    assert spawner.last.arg("-ss") == "30.00"


async def test_gives_up_after_retries() -> None:
    player, spawner, *_ = make_player()
    await playing(player, spawner, at=5)
    for _ in range(3):  # initial + 2 retries
        spawner.last.emit("Connection timed out\n")
        spawner.last.exit(0)
        await settle(30)
    state = player.get_state()
    assert state.status is S.ERROR and state.retryable
    assert "Gave up" in (state.error or "")
    assert spawner.live == []


async def test_retry_after_error() -> None:
    player, spawner, *_ = make_player(retry_delays=())
    await playing(player, spawner, at=12)
    spawner.last.emit("Connection timed out\n")
    spawner.last.exit(0)
    await settle()
    assert player.get_state().status is S.ERROR
    await player.retry()
    assert spawner.last.arg("-ss") == "12.00"
    assert player.get_state().status is S.LOADING


async def test_fatal_stream_error_does_not_retry() -> None:
    player, spawner, *_ = make_player()
    await player.play(SONG)
    spawner.last.emit("https://x: Invalid data found when processing input\n")
    spawner.last.exit(0)
    await settle()
    assert player.get_state().status is S.ERROR
    assert len(spawner.processes) == 1


async def test_missing_ffplay() -> None:
    player, spawner, *_ = make_player(which=None)
    await player.play(SONG)
    state = player.get_state()
    assert state.status is S.ERROR and state.error == FFPLAY_MISSING
    assert "brew install ffmpeg" in FFPLAY_MISSING
    assert spawner.processes == []


async def test_spawn_failure() -> None:
    player, *_ = make_player(FakeSpawner(error=FileNotFoundError("ffplay")))
    await player.play(SONG)
    assert player.get_state().error == FFPLAY_MISSING


async def test_unavailable_video() -> None:
    resolver = FakeResolver(error=StreamError("This video is unavailable.", False))
    player, spawner, *_ = make_player(resolver=resolver)
    await player.play(SONG)
    state = player.get_state()
    assert state.status is S.ERROR and not state.retryable
    assert state.error == "This video is unavailable."
    assert spawner.processes == []


async def test_volume_restarts_at_position_while_playing() -> None:
    player, spawner, *_ = make_player()
    await playing(player, spawner, at=80)
    await player.set_volume(35)
    await settle(30)
    assert player.get_state().volume == 35
    assert len(spawner.processes) == 2
    assert spawner.last.arg("-volume") == "35"
    assert spawner.last.arg("-ss") == "80.00"
    assert len(spawner.live) == 1


async def test_volume_debounces_rapid_changes() -> None:
    player, spawner, *_ = make_player(volume_debounce=0.05)
    await playing(player, spawner, at=10)
    for value in (75, 70, 65, 60):
        await player.set_volume(value)
    await asyncio.sleep(0.15)
    await settle(30)
    assert len(spawner.processes) == 2
    assert spawner.last.arg("-volume") == "60"


async def test_volume_while_paused_applies_on_resume() -> None:
    player, spawner, *_ = make_player()
    await playing(player, spawner)
    await player.pause()
    await player.set_volume(-20)
    assert player.get_state().volume == 0
    assert len(spawner.processes) == 1
    await player.resume()
    assert spawner.last.arg("-volume") == "0"


async def test_seek() -> None:
    player, spawner, *_ = make_player()
    await playing(player, spawner, at=50)
    assert await player.seek(120) is True
    assert spawner.last.arg("-ss") == "120.00"
    assert await player.seek(10_000) is True
    assert spawner.last.arg("-ss") == "199.00"  # clamped before the end
    assert len(spawner.live) == 1


async def test_seek_while_paused_only_moves_position() -> None:
    player, spawner, *_ = make_player()
    await playing(player, spawner, at=50)
    await player.pause()
    assert await player.seek(20) is True
    assert player.get_state().position == 20 and len(spawner.processes) == 1


async def test_seek_unsupported_for_live() -> None:
    live = StreamInfo(url="https://x/live.m3u8", is_live=True)
    player, spawner, *_ = make_player(resolver=FakeResolver(stream=live))
    await player.play(make_song(4, duration=0))
    spawner.last.progress(5)
    await settle()
    assert player.get_state().can_seek is False
    assert await player.seek(100) is False
    assert len(spawner.processes) == 1


async def test_aclose_and_shutdown_leave_nothing_running() -> None:
    player, spawner, _, reaper, _ = make_player()
    await playing(player, spawner)
    await player.aclose()
    assert spawner.live == [] and reaper.closed
    player.shutdown()  # idempotent


async def test_stderr_goes_to_log_not_terminal(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.INFO, logger="pixelfm")
    player, spawner, *_ = make_player()
    await playing(player, spawner)
    spawner.last.emit("[https @ 0x1] Opening 'x' for reading\n")
    await settle()
    assert "Opening 'x' for reading" in caplog.text
    assert "Opening" not in capsys.readouterr().err
    assert player.stderr_tail[-1].startswith("[https")


async def test_listener_errors_do_not_break_playback() -> None:
    player, spawner, *_ = make_player()

    def broken(state: PlaybackState) -> None:
        raise RuntimeError("ui bug")

    player.subscribe(broken)
    await playing(player, spawner)
    assert player.get_state().status is S.PLAYING


async def test_local_flag_is_reset_for_the_next_track() -> None:
    """A stream failing after a local file must not be labelled LOCAL FILE."""
    resolver = FakeResolver(StreamInfo(url="file:/music/a.m4a", local=True))
    player, spawner, *_ = make_player(resolver=resolver)
    await player.play(SONG)
    spawner.last.progress(1.0)
    await settle()
    assert player.get_state().local is True
    resolver.stream = None  # type: ignore[assignment]
    resolver.error = StreamError("offline", retryable=True)
    await player.play(make_song(2))
    await settle()
    state = player.get_state()
    assert state.status is S.ERROR and state.local is False
