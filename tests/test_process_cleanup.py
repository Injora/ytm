"""Real-process tests for orphan prevention (no audio, no network)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

from pixelfm._reaper import ProcessReaper
from pixelfm.services.ffplay import terminate_process


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); reap it if it is our child.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_watchdog_kills_tracked_children_when_parent_pipe_closes() -> None:
    victim = _sleeper()
    survivor = _sleeper()
    reaper = ProcessReaper()
    try:
        reaper.track(victim.pid)
        reaper.track(survivor.pid)
        reaper.untrack(survivor.pid)
        # Closing the pipe is exactly what the kernel does if PixelFM dies.
        reaper.close()
        assert _wait_dead(victim.pid)
        assert _alive(survivor.pid)
    finally:
        for proc in (victim, survivor):
            proc.kill()
            proc.wait()


async def test_terminate_process_on_real_child() -> None:
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None
    await proc.stdout.readline()  # SIGTERM handler is installed
    started = time.monotonic()
    await terminate_process(proc, grace=0.2)  # type: ignore[arg-type]
    assert proc.returncode is not None
    assert time.monotonic() - started < 3
