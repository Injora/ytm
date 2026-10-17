"""Fake subprocess, spawner, resolver, and reaper for playback tests."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Sequence

from pixelfm.models import Song
from pixelfm.services.streams import StreamError, StreamInfo

_pids = itertools.count(40_000)


class FakeProcess:
    """Mimics ``asyncio.subprocess.Process`` for an ffplay child."""

    def __init__(self, args: Sequence[str], *, stubborn: bool = False) -> None:
        self.args = list(args)
        self.pid = next(_pids)
        self.returncode: int | None = None
        self.stderr = asyncio.StreamReader()
        self.signals: list[str] = []
        self.stubborn = stubborn  # ignores SIGTERM
        self._exited = asyncio.Event()

    # -- test controls -------------------------------------------------
    def emit(self, text: str) -> None:
        self.stderr.feed_data(text.encode())

    def progress(self, seconds: float) -> None:
        self.emit(
            f"  {seconds:6.2f} M-A:  0.000 fd=   0 aq=   10KB vq=    0KB sq=    0B \r"
        )

    def exit(self, code: int = 0) -> None:
        if self.returncode is None:
            self.returncode = code
            self.stderr.feed_eof()
            self._exited.set()

    @property
    def alive(self) -> bool:
        return self.returncode is None

    def arg(self, flag: str) -> str | None:
        return self.args[self.args.index(flag) + 1] if flag in self.args else None

    # -- Process API ---------------------------------------------------
    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.signals.append("TERM")
        if not self.stubborn:
            self.exit(-15)

    def kill(self) -> None:
        self.signals.append("KILL")
        self.exit(-9)


class FakeSpawner:
    def __init__(self, *, stubborn: bool = False, error: Exception | None = None):
        self.processes: list[FakeProcess] = []
        self.stubborn = stubborn
        self.error = error

    async def __call__(self, args: Sequence[str]) -> FakeProcess:
        if self.error is not None:
            raise self.error
        proc = FakeProcess(args, stubborn=self.stubborn)
        self.processes.append(proc)
        return proc

    @property
    def last(self) -> FakeProcess:
        return self.processes[-1]

    @property
    def live(self) -> list[FakeProcess]:
        return [p for p in self.processes if p.alive]


class FakeResolver:
    def __init__(
        self,
        stream: StreamInfo | None = None,
        error: StreamError | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.stream = stream or StreamInfo(
            url="https://media.example.com/audio.m4a?expire=9999999999",
            headers=(("User-Agent", "UA/1.0"), ("Accept", "*/*")),
            duration=None,
        )
        self.error = error
        self.gate = gate
        self.calls: list[tuple[str, bool]] = []

    async def resolve(self, song: Song, *, refresh: bool = False) -> StreamInfo:
        self.calls.append((song.id, refresh))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.stream


class FakeReaper:
    def __init__(self) -> None:
        self.tracked: set[int] = set()
        self.closed = False

    def track(self, pid: int) -> None:
        self.tracked.add(pid)

    def untrack(self, pid: int) -> None:
        self.tracked.discard(pid)

    def close(self) -> None:
        self.closed = True


async def settle(rounds: int = 10) -> None:
    """Let reader/watcher tasks run."""
    for _ in range(rounds):
        await asyncio.sleep(0)
