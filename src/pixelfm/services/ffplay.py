"""Real audio playback through an ``ffplay`` subprocess.

Verified behaviour of ffplay (8.x) that shapes this design:

* With ``-nodisp`` there is no control channel: ffplay's keyboard controls
  need an SDL window, and there is no stdin protocol. Volume is fixed at
  launch (``-volume``) and this build has no ``azmq`` filter.
* ``SIGSTOP``/``SIGCONT`` do not pause cleanly: on resume ffplay's clock jumps
  forward by the paused time and the audio in between is skipped.
* ``-stats`` prints the real audio clock (``"  12.34 M-A: ..."``) to stderr
  about 17 times a second, and ``-ss`` reports absolute positions.
* ffplay exits with status 0 even on errors, so failures are classified from
  stderr and from whether the stream reached its end.

So each ffplay process plays one uninterrupted segment. Pause stops the process
and remembers the position; resume, seek, and volume changes restart at that
position with ``-ss``; network drops and expired URLs restart the same way.
Restarts cost a brief rebuffer (typically under a second).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from pixelfm._reaper import NullReaper, ProcessReaper, default_reaper
from pixelfm.models import Song
from pixelfm.services.metering import MeterParser, StereoLevel, audio_filter
from pixelfm.services.playback import BasePlayer, clamp_volume
from pixelfm.services.streams import StreamError, StreamInfo
from pixelfm.state import ACTIVE, AudioFx, PlaybackStatus

log = logging.getLogger(__name__)

FFPLAY_MISSING = (
    "ffplay not found, so audio is unavailable. Install FFmpeg: brew install ffmpeg"
)
PROTOCOL_WHITELIST = "https,http,tls,tcp,crypto,httpproxy"
#: Seconds from the end within which an exit counts as a natural end.
END_TOLERANCE = 3.0

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_PROGRESS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+(?:M-A|A-V|M-V):")
_EXPIRED = re.compile(r"\b(403|410)\b|forbidden|access denied", re.IGNORECASE)
_NETWORK = re.compile(
    r"connection (reset|refused|timed out)|timed out|input/output error|i/o error"
    r"|network is unreachable|broken pipe|end of file|stream ends prematurely"
    r"|failed to resolve|server returned 5\d\d|will reconnect|error number -\d+",
    re.IGNORECASE,
)
_FILTER_TROUBLE = re.compile(r"astats|ametadata|asetnsamples|filter", re.IGNORECASE)

_AUDIO_DEVICE = re.compile(r"audio open failed|sdl_openaudio|no audio device", re.I)
_FATAL = re.compile(
    r"invalid data found|not found|no such filter|not on whitelist|could not find codec"
    r"|decoder .* not found|unsupported|failed to open",
    re.IGNORECASE,
)


class ProcessLike(Protocol):
    """The subset of :class:`asyncio.subprocess.Process` the player uses."""

    pid: int
    returncode: int | None
    stderr: asyncio.StreamReader | None

    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


Spawner = Callable[[Sequence[str]], Awaitable[ProcessLike]]


class Resolver(Protocol):
    async def resolve(self, song: Song, *, refresh: bool = False) -> StreamInfo: ...


async def spawn_process(args: Sequence[str]) -> ProcessLike:
    """Start ffplay without a shell, detached from the terminal."""
    return await asyncio.create_subprocess_exec(  # type: ignore[return-value]
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # own process group; terminal ^C never hits it
    )


def build_args(
    binary: str,
    stream: StreamInfo,
    *,
    start: float = 0.0,
    volume: int = 80,
    filters: str | None = None,
) -> list[str]:
    """ffplay argument vector (never passed through a shell).

    ``filters`` is an audio filter graph (effects and meters). Local files
    get a ``file``-only protocol whitelist and no network options.
    """
    args = [
        binary,
        "-nodisp",
        "-autoexit",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats",
        "-vn",
        "-sn",
        "-volume",
        str(clamp_volume(volume)),
    ]
    if start > 0 and not stream.is_live:
        args += ["-ss", f"{start:.2f}"]
    if filters:
        args += ["-af", filters]
    if stream.local:
        return [*args, "-protocol_whitelist", "file", "-i", stream.url]
    args += [
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_on_network_error",
        "1",
        "-reconnect_delay_max",
        "4",
        "-rw_timeout",
        "15000000",  # microseconds; a stalled connection fails instead of hanging
        "-protocol_whitelist",
        PROTOCOL_WHITELIST,
    ]
    headers = dict(stream.headers)
    if user_agent := headers.pop("User-Agent", None):
        args += ["-user_agent", user_agent]
    if headers:
        args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    args += ["-i", stream.url]
    return args


def parse_progress(line: str) -> float | None:
    """Position in seconds from an ffplay ``-stats`` line, else ``None``."""
    match = _PROGRESS.match(_ANSI.sub("", line))
    return max(0.0, float(match.group(1))) if match else None


@dataclass(frozen=True, slots=True)
class Problem:
    """Why ffplay stopped early."""

    kind: str  # "expired", "network", "device", or "fatal"
    message: str


def classify_stderr(lines: Sequence[str]) -> Problem | None:
    """Find the most relevant error in ffplay's stderr, newest first."""
    for line in reversed(lines):
        if _EXPIRED.search(line):
            return Problem("expired", "The stream link expired or was refused.")
        if _AUDIO_DEVICE.search(line):
            return Problem("device", "Could not open the audio output device.")
        if _NETWORK.search(line):
            return Problem("network", "The network connection was interrupted.")
        if _FATAL.search(line):
            return Problem("fatal", f"ffplay could not play this stream: {line[:80]}")
    return None


async def terminate_process(proc: ProcessLike, grace: float = 1.5) -> None:
    """SIGTERM, then SIGKILL if it does not exit within ``grace`` seconds."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), grace)
            return
        except TimeoutError:
            log.warning("ffplay %s ignored SIGTERM; killing", proc.pid)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    await proc.wait()


class FFplayPlayer(BasePlayer):
    """Plays resolved streams with one ffplay subprocess at a time.

    All public methods are coroutines meant to run on the app's event loop;
    none of them block. A generation counter makes every superseded process,
    resolve, and retry a no-op, so at most one ffplay process exists.
    """

    name = "ffplay"

    def __init__(
        self,
        resolver: Resolver,
        *,
        binary: str = "ffplay",
        volume: int = 80,
        spawn: Spawner = spawn_process,
        reaper: ProcessReaper | NullReaper | None = None,
        which: Callable[[str], str | None] = shutil.which,
        volume_debounce: float = 0.4,
        retry_delays: Sequence[float] = (1.0, 2.0, 4.0),
        kill_timeout: float = 1.5,
        notify_interval: float = 0.25,
        levels: bool = True,
        spectrum: bool = True,
    ) -> None:
        super().__init__(volume, notify_interval)
        self._levels_enabled = levels
        self._spectrum_enabled = spectrum
        self.meters = MeterParser()
        self._resolver = resolver
        self._binary = binary
        self._spawn = spawn
        self._reaper = reaper if reaper is not None else default_reaper()
        self._which = which
        self._volume_debounce = volume_debounce
        self._retry_delays = tuple(retry_delays)
        self._kill_timeout = kill_timeout

        self._lock = asyncio.Lock()
        self._generation = 0
        self._proc: ProcessLike | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending: asyncio.Task[None] | None = None  # debounce or retry
        self._got_progress = False
        self._recoveries = 0
        self._recovery_anchor = 0.0
        self.stderr_tail: deque[str] = deque(maxlen=30)

    # ----------------------------------------------------------- public API

    @property
    def process(self) -> ProcessLike | None:
        """The live ffplay process, if any (for diagnostics and tests)."""
        return self._proc

    async def play(self, song: Song, start: float = 0.0) -> None:
        async with self._lock:
            generation = await self._supersede()
            self._recoveries = 0
            self._update(
                status=PlaybackStatus.LOADING,
                song=song,
                position=float(start),
                duration=float(song.duration) or None,
                can_seek=bool(song.duration),
                error=None,
                retryable=False,
                detail="resolving stream",
                local=False,  # set again once the source is known
            )
        log.info("play %s (%s) from %.1fs", song.id, song.title, start)
        await self._launch(generation, start)

    async def pause(self) -> None:
        async with self._lock:
            if self._state.status not in ACTIVE:
                return
            await self._supersede()
            self._update(status=PlaybackStatus.PAUSED, detail=None)

    async def resume(self) -> None:
        if self._state.status is PlaybackStatus.PAUSED:
            await self._restart(self._state.position, "buffering")

    async def stop(self) -> None:
        async with self._lock:
            if self._state.status in (PlaybackStatus.IDLE, PlaybackStatus.STOPPED):
                return
            await self._supersede()
            self._update(status=PlaybackStatus.STOPPED, position=0.0, detail=None)

    async def set_volume(self, value: int) -> None:
        """Set volume 0-100. While playing, applied by a debounced restart."""
        volume = clamp_volume(value)
        if volume == self._state.volume:
            return
        self._update(volume=volume)
        if self._state.status not in ACTIVE:
            return  # applied on the next launch
        self._cancel_pending()
        generation = self._generation
        self._pending = asyncio.create_task(self._apply_volume(generation))

    async def set_fx(self, fx: AudioFx) -> None:
        """Change bass/treble/normalize. While playing, applied by a debounced
        restart at the current position (ffplay filters are fixed at launch)."""
        fx = fx.clamped()
        if fx == self._state.fx:
            return
        self._update(fx=fx)
        if self._state.status not in ACTIVE:
            return
        self._cancel_pending()
        generation = self._generation
        self._pending = asyncio.create_task(
            self._apply_volume(generation, "applying effects")
        )

    async def seek(self, position: float) -> bool:
        state = self._state
        if not state.can_seek or not state.duration or state.song is None:
            return False
        position = max(0.0, min(float(position), state.duration - 1))
        if state.status is PlaybackStatus.PAUSED:
            self._update(position=position)
            return True
        if state.status not in ACTIVE:
            return False
        await self._restart(position, "seeking")
        return True

    async def retry(self) -> None:
        state = self._state
        if state.song is not None and state.status is PlaybackStatus.ERROR:
            await self.play(state.song, state.position)

    async def aclose(self) -> None:
        async with self._lock:
            await self._supersede()
        self._reaper.close()

    def shutdown(self) -> None:
        """Kill ffplay synchronously; safe to call from atexit at any time."""
        self._generation += 1
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(OSError):
                os.kill(proc.pid, signal.SIGKILL)
        self._reaper.close()

    # ------------------------------------------------------------ internals

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    async def _supersede(self) -> int:
        """Invalidate in-flight work and stop the current process. Hold the lock."""
        self._generation += 1
        self._cancel_pending()
        proc, self._proc = self._proc, None
        if proc is not None:
            await terminate_process(proc, self._kill_timeout)
            self._reaper.untrack(proc.pid)
        return self._generation

    async def _restart(self, position: float, detail: str) -> None:
        async with self._lock:
            generation = await self._supersede()
            self._update(
                status=PlaybackStatus.LOADING,
                position=position,
                detail=detail,
                error=None,
            )
        await self._launch(generation, position)

    async def _apply_volume(
        self, generation: int, detail: str = "applying volume"
    ) -> None:
        if self._volume_debounce:
            await asyncio.sleep(self._volume_debounce)
        if generation == self._generation and self._state.status in ACTIVE:
            self._pending = None  # don't cancel ourselves in _supersede
            await self._restart(self._state.position, detail)

    def _fail(self, generation: int, message: str, retryable: bool = True) -> None:
        if generation != self._generation:
            return
        log.warning("playback error: %s", message)
        self._update(
            status=PlaybackStatus.ERROR, error=message, retryable=retryable, detail=None
        )

    async def _launch(
        self, generation: int, start: float, *, refresh: bool = False
    ) -> None:
        song = self._state.song
        if song is None or generation != self._generation:
            return
        binary = self._which(self._binary)
        if binary is None:
            self._fail(generation, FFPLAY_MISSING)
            return
        try:
            stream = await self._resolver.resolve(song, refresh=refresh)
        except StreamError as exc:
            self._fail(generation, exc.message, exc.retryable)
            return
        except Exception as exc:
            log.exception("unexpected resolver failure")
            self._fail(generation, f"Could not load the stream: {exc}")
            return

        async with self._lock:
            if generation != self._generation:
                return  # superseded while resolving
            if stream.is_live:
                start = 0.0
            args = build_args(
                binary,
                stream,
                start=start,
                volume=self._state.volume,
                filters=audio_filter(
                    self._state.fx, self._levels_enabled, self._spectrum_enabled
                ),
            )
            try:
                proc = await self._spawn(args)
            except FileNotFoundError:
                self._fail(generation, FFPLAY_MISSING)
                return
            except OSError as exc:
                self._fail(generation, f"Could not start ffplay: {exc}")
                return
            self._proc = proc
            self._reaper.track(proc.pid)
            self._got_progress = False
            self._recovery_anchor = start
            self.stderr_tail.clear()
            self.meters.reset()
            log.info(
                "ffplay pid %s started at %.1fs (%s)",
                proc.pid,
                start,
                stream.description,
            )
            duration = stream.duration or self._state.duration
            self._update(
                local=stream.local,
                duration=None if stream.is_live else duration,
                can_seek=not stream.is_live and bool(duration),
                detail="buffering"
                if self._state.detail == "resolving stream"
                else self._state.detail,
            )
            reader = asyncio.create_task(self._read_stderr(proc, generation))
            watcher = asyncio.create_task(self._watch(proc, generation, reader))
            for task in (reader, watcher):
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _read_stderr(self, proc: ProcessLike, generation: int) -> None:
        if proc.stderr is None:
            return
        buffer = ""
        while chunk := await proc.stderr.read(4096):
            buffer += chunk.decode(errors="replace")
            *lines, buffer = re.split(r"[\r\n]", buffer)
            for line in lines:
                self._handle_line(line, generation)
        if buffer:
            self._handle_line(buffer, generation)

    def _audible(self) -> bool:
        return self._state.status is PlaybackStatus.PLAYING

    def audio_levels(self, count: int) -> list[float]:
        """Loudness history (dBFS) up to the audible position. Never invented."""
        if count <= 0 or not self._audible():
            return []
        return self.meters.loudness_history(self._state.position, count)

    def stereo_level(self) -> StereoLevel | None:
        """Measured L/R RMS and peak levels for what is audible now."""
        return self.meters.stereo_at(self._state.position) if self._audible() else None

    def spectrum(self) -> tuple[float, ...] | None:
        """Measured band levels (see :data:`BANDS`) for what is audible now."""
        return (
            self.meters.spectrum_at(self._state.position) if self._audible() else None
        )

    def _handle_line(self, line: str, generation: int) -> None:
        if self.meters.feed(line):
            return
        position = parse_progress(line)
        if position is None:
            clean = _ANSI.sub("", line).strip()
            if clean and not clean.startswith("nan"):
                self.stderr_tail.append(clean)
                log.info("ffplay: %s", clean)
            return
        if generation != self._generation:
            return
        self._got_progress = True
        if position > self._recovery_anchor + 10:
            self._recoveries = 0  # playing smoothly again
        if self._state.status is PlaybackStatus.LOADING:
            self._update(status=PlaybackStatus.PLAYING, position=position, detail=None)
        elif self._state.status is PlaybackStatus.PLAYING:
            self._update(position=position)

    async def _watch(
        self, proc: ProcessLike, generation: int, reader: asyncio.Task[None]
    ) -> None:
        returncode = await proc.wait()
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(reader), 1.0)
        self._reaper.untrack(proc.pid)
        if generation != self._generation:
            return  # we stopped it on purpose
        self._proc = None
        log.info("ffplay pid %s exited with %s", proc.pid, returncode)
        self._on_exit(generation)

    def _on_exit(self, generation: int) -> None:
        state = self._state
        problem = classify_stderr(list(self.stderr_tail))
        near_end = bool(state.duration) and state.position >= (
            (state.duration or 0) - END_TOLERANCE
        )
        if self._got_progress and (
            near_end or (problem is None and not state.duration)
        ):
            self._update(
                status=PlaybackStatus.ENDED,
                position=state.duration or state.position,
                detail=None,
            )
            return

        if (
            self._levels_enabled
            and not self._got_progress
            and any(_FILTER_TROUBLE.search(line) for line in self.stderr_tail)
        ):
            # This FFmpeg build lacks a level filter: play without the meter.
            log.warning("disabling the level meter: ffplay rejected the filter")
            self._levels_enabled = self._spectrum_enabled = False
            self._pending = asyncio.create_task(
                self._recover(generation, 0, refresh=False)
            )
            return
        # Exited early: a drop without a message is treated as a network issue.
        problem = problem or Problem("network", "Playback was interrupted.")
        if problem.kind in ("fatal", "device"):
            self._fail(generation, problem.message, retryable=problem.kind == "device")
            return
        if self._recoveries >= len(self._retry_delays):
            self._fail(generation, f"{problem.message} Gave up after retrying.")
            return
        delay = self._retry_delays[self._recoveries]
        self._recoveries += 1
        log.info("recovering from %s (attempt %s)", problem.kind, self._recoveries)
        self._update(
            status=PlaybackStatus.LOADING,
            detail=f"reconnecting {self._recoveries}/{len(self._retry_delays)}",
        )
        self._pending = asyncio.create_task(
            self._recover(generation, delay, refresh=problem.kind == "expired")
        )

    async def _recover(self, generation: int, delay: float, *, refresh: bool) -> None:
        if delay:
            await asyncio.sleep(delay)
        if generation == self._generation:
            self._pending = None
            await self._launch(generation, self._state.position, refresh=refresh)
