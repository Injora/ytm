"""Last-resort cleanup of audio processes if PixelFM dies abruptly.

Normal exits stop playback explicitly. This covers the rest (crash, SIGKILL,
closed terminal): a tiny watchdog process holds the read end of a pipe from
PixelFM. When PixelFM dies for any reason the kernel closes the pipe, the
watchdog reads EOF, and it kills every process group it was told about.

Protocol on the pipe: ``+<pid>\\n`` to track, ``-<pid>\\n`` to untrack. Each
tracked child must be started with ``start_new_session=True`` so its pid is
also its process-group id.

This module is deliberately dependency-free so the watchdog starts quickly.
"""

from __future__ import annotations

import contextlib
import logging
import os
import select
import signal
import subprocess
import sys
import threading

log = logging.getLogger(__name__)


class ProcessReaper:
    """Client side: starts the watchdog lazily and reports child pids."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None

    def _send(self, line: str) -> None:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                try:
                    self._proc = subprocess.Popen(
                        [sys.executable, "-m", "pixelfm._reaper", str(os.getpid())],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,  # immune to the terminal's ^C/hangup
                    )
                except OSError as exc:
                    log.warning("could not start process watchdog: %s", exc)
                    self._proc = None
                    return
            assert self._proc.stdin is not None
            try:
                self._proc.stdin.write(line.encode())
                self._proc.stdin.flush()
            except OSError as exc:
                log.warning("process watchdog unavailable: %s", exc)

    def track(self, pid: int) -> None:
        """Kill ``pid``'s process group if PixelFM exits without cleaning up."""
        self._send(f"+{pid}\n")

    def untrack(self, pid: int) -> None:
        """Forget ``pid`` (it exited or was stopped normally)."""
        self._send(f"-{pid}\n")

    def close(self) -> None:
        """Stop the watchdog. It kills anything still tracked, then exits."""
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None and proc.stdin is not None:
            with contextlib.suppress(OSError):
                proc.stdin.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=2)


class NullReaper:
    """Does nothing; for tests and the simulated player."""

    def track(self, pid: int) -> None:
        pass

    def untrack(self, pid: int) -> None:
        pass

    def close(self) -> None:
        pass


_default: ProcessReaper | None = None


def default_reaper() -> ProcessReaper:
    """The process-wide reaper (one watchdog per PixelFM process)."""
    global _default
    if _default is None:
        _default = ProcessReaper()
    return _default


def _kill_groups(pids: set[int]) -> None:
    for pid in pids:
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGKILL)


def watchdog(parent: int, stdin_fd: int = 0) -> None:
    """Watchdog loop: track pids from ``stdin_fd`` until EOF or parent death."""
    tracked: set[int] = set()
    buffer = b""
    while True:
        ready, _, _ = select.select([stdin_fd], [], [], 1.0)
        if not ready:
            if os.getppid() != parent:  # reparented: parent is gone
                break
            continue
        chunk = os.read(stdin_fd, 4096)
        if not chunk:
            break
        buffer += chunk
        *lines, buffer = buffer.split(b"\n")
        for raw in lines:
            line = raw.strip().decode(errors="ignore")
            if len(line) > 1 and line[1:].isdigit():
                (tracked.add if line[0] == "+" else tracked.discard)(int(line[1:]))
    _kill_groups(tracked)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    watchdog(int(sys.argv[1]) if len(sys.argv) > 1 else os.getppid())
