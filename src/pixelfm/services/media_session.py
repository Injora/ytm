"""macOS system media controls: Now Playing info and remote commands.

PixelFM runs inside a terminal, which macOS doesn't treat as a media app, so a
small native helper (``native/macos/NowPlaying.swift``, shipped as
``pixelfm/native/PixelFMMedia.app``) does the system side: it publishes
``MPNowPlayingInfoCenter`` metadata (Control Centre, the menu-bar Now Playing
item) and receives ``MPRemoteCommandCenter`` commands (media keys, headphone
buttons, Control Centre). This class starts the helper, sends it the current
state as JSON lines, and turns its command events into calls on the app's
existing playback actions; there is no second playback state machine.

The helper lives exactly as long as PixelFM: it exits when its stdin closes,
clearing Now Playing, so a crash can't leave stale metadata or a background
process behind.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

HELPER = Path(__file__).resolve().parent.parent / "native" / "PixelFMMedia.app"
EXECUTABLE = "Contents/MacOS/pixelfm-nowplaying"
COMMANDS = {"toggle", "play", "pause", "next", "previous", "seek"}
#: The same command arriving again this soon is a key repeat, not a new press.
REPEAT_WINDOW = 0.25
MIN_MACOS = (12, 0)
CLOSE_GRACE = 1.0  # seconds the helper gets to clear Now Playing and exit


@dataclass(frozen=True, slots=True)
class NowPlayingInfo:
    """What macOS should show. ``elapsed`` is extrapolated by the system
    while ``playing``, so updates are only needed when something changes."""

    title: str
    artist: str
    album: str = ""
    duration: float = 0.0
    elapsed: float = 0.0
    playing: bool = False
    artwork: str | None = None  # path to an image file


def helper_path(env: dict[str, str] | None = None) -> Path | None:
    """The helper executable, or ``None`` if unavailable on this system."""
    env = dict(os.environ) if env is None else env
    if sys.platform != "darwin" or env.get("PIXELFM_MEDIA_CONTROLS") == "off":
        return None
    override = env.get("PIXELFM_MEDIA_HELPER")
    executable = Path(override) if override else HELPER / EXECUTABLE
    return (
        executable if executable.is_file() and os.access(executable, os.X_OK) else None
    )


def unavailable_reason(env: dict[str, str] | None = None) -> str | None:
    """Why media controls can't run, in words for the user (or ``None``)."""
    env = dict(os.environ) if env is None else env
    if sys.platform != "darwin":
        return "System media controls are only available on macOS."
    if env.get("PIXELFM_MEDIA_CONTROLS") == "off":
        return "System media controls are turned off (PIXELFM_MEDIA_CONTROLS=off)."
    version = tuple(int(p) for p in (platform.mac_ver()[0] or "0").split(".")[:2])
    if version < MIN_MACOS:
        return "System media controls need macOS 12 or newer."
    if helper_path(env) is None:
        return (
            "The media-controls helper is missing (a source checkout needs "
            "`npm run build:helper`). Everything else works without it."
        )
    return None


class MediaSession:
    """One helper process; commands go to ``on_command(name, position)``."""

    def __init__(
        self,
        on_command: Callable[[str, float | None], None],
        executable: Path | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_command = on_command
        self._executable = executable
        self._clock = clock
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._last: NowPlayingInfo | None = None
        self._last_command: tuple[str, float] = ("", float("-inf"))
        self.ready = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> bool:
        if self._executable is None:
            return False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(self._executable),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,  # Ctrl+C in the terminal isn't for it
            )
        except OSError as exc:  # wrong architecture, damaged bundle, ...
            log.warning("media helper failed to start: %s", exc)
            self._proc = None
            return False
        self._reader = asyncio.create_task(self._read())
        log.info("media helper started (pid %s)", self._proc.pid)
        return True

    async def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while line := await self._proc.stdout.readline():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("event") == "ready":
                self.ready.set()
            elif message.get("event") == "command":
                self._command(message)
        log.info("media helper exited (%s)", self._proc.returncode)

    def _command(self, message: dict[str, object]) -> None:
        name = message.get("name")
        if name not in COMMANDS:
            return
        now = self._clock()
        previous, at = self._last_command
        if name == previous and name != "seek" and now - at < REPEAT_WINDOW:
            return  # key repeat / double delivery
        self._last_command = (str(name), now)
        position = message.get("position")
        seek = float(position) if isinstance(position, int | float) else None
        try:
            self._on_command(str(name), seek)
        except Exception:
            log.exception("media command %s failed", name)

    def _send(self, message: dict[str, object]) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            return
        with contextlib.suppress(OSError, RuntimeError):
            proc.stdin.write((json.dumps(message) + "\n").encode())

    def publish(self, info: NowPlayingInfo | None) -> None:
        """Show ``info`` in the system's Now Playing (``None`` clears it)."""
        if info == self._last:
            return
        self._last = info
        if info is None:
            self._send({"type": "clear"})
        else:
            self._send({"type": "update", **asdict(info)})

    async def aclose(self) -> None:
        """Clear Now Playing and stop the helper; never hangs."""
        proc = self._proc
        if proc is None:
            return
        self._send({"type": "quit"})
        if proc.stdin is not None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), CLOSE_GRACE)
        except (TimeoutError, ProcessLookupError):
            self.shutdown()
        if self._reader is not None:
            self._reader.cancel()

    def shutdown(self) -> None:
        """Synchronous last resort (atexit, signals)."""
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(OSError):
                os.kill(proc.pid, signal.SIGTERM)
