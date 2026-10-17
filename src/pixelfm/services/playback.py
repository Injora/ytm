"""Playback service interface and shared engine behaviour.

The UI talks only to :class:`Player`. Two implementations exist:
:class:`~pixelfm.services.ffplay.FFplayPlayer` for real audio and
:class:`SimulatedPlayer` for the offline ``--mock`` mode and tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from pixelfm.models import Song
from pixelfm.services.metering import StereoLevel
from pixelfm.state import (
    ACTIVE,
    AudioFx,
    PlaybackState,
    PlaybackStatus,
    check_transition,
)

log = logging.getLogger(__name__)

Listener = Callable[[PlaybackState], None]

VOLUME_STEP = 5


def clamp_volume(value: int) -> int:
    return max(0, min(100, int(value)))


class Player(Protocol):
    """Stable playback interface used by the UI."""

    name: str

    def get_state(self) -> PlaybackState:
        """Current immutable state snapshot."""
        ...

    def subscribe(self, listener: Listener) -> None:
        """Call ``listener`` (on the event loop) whenever the state changes."""
        ...

    def audio_levels(self, count: int) -> list[float]:
        """Up to ``count`` recent RMS levels (dBFS) that are audible now.

        Empty when the engine cannot measure audio (e.g. simulated playback).
        """
        ...

    async def play(self, song: Song, start: float = 0.0) -> None: ...
    async def set_fx(self, fx: AudioFx) -> None:
        """Set bass/treble/normalize (see :class:`AudioFx`)."""
        ...

    def stereo_level(self) -> StereoLevel | None: ...
    def spectrum(self) -> tuple[float, ...] | None: ...
    def cue(self, song: Song, position: float = 0.0) -> bool:
        """Load ``song`` paused at ``position`` (no audio until resumed)."""
        ...

    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def toggle(self) -> None: ...
    async def stop(self) -> None: ...
    async def set_volume(self, value: int) -> None: ...
    async def seek(self, position: float) -> bool:
        """Seek to ``position`` seconds. Returns ``False`` if unsupported."""
        ...

    async def retry(self) -> None: ...
    async def aclose(self) -> None:
        """Stop playback and release every resource."""
        ...

    def shutdown(self) -> None:
        """Synchronous last-resort cleanup (atexit, signal handlers)."""
        ...


class BasePlayer:
    """State bookkeeping shared by players.

    State changes go through :meth:`_update`, which enforces the lifecycle
    transition table and notifies listeners. Position-only updates are
    throttled to ``notify_interval`` to keep UI work small.
    """

    name = "base"

    def __init__(self, volume: int = 80, notify_interval: float = 0.25) -> None:
        self._state = PlaybackState(volume=clamp_volume(volume))
        self._listeners: list[Listener] = []
        self._notify_interval = notify_interval
        self._last_notify = float("-inf")

    def get_state(self) -> PlaybackState:
        return self._state

    @property
    def state(self) -> PlaybackState:
        return self._state

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def audio_levels(self, count: int) -> list[float]:
        """Recent real audio levels in dBFS; empty if the engine has none."""
        return []

    def stereo_level(self) -> StereoLevel | None:
        """Measured L/R levels, or ``None`` when the engine can't measure."""
        return None

    def spectrum(self) -> tuple[float, ...] | None:
        """Measured band levels, or ``None`` when the engine can't measure."""
        return None

    def _update(self, **changes: Any) -> None:
        old = self._state
        status = changes.get("status")
        if status is not None:
            check_transition(old.status, status)
            if status is not old.status:
                log.debug("playback %s -> %s", old.status, status)
        new = replace(old, **changes)
        if new == old:
            return
        self._state = new
        now = time.monotonic()
        if (
            set(changes) <= {"position"}
            and now - self._last_notify < self._notify_interval
        ):
            return
        self._last_notify = now
        for listener in list(self._listeners):
            try:
                listener(new)
            except Exception:
                log.exception("playback listener failed")

    def cue(self, song: Song, position: float = 0.0) -> bool:
        """Load ``song`` paused at ``position`` without starting audio, e.g. a
        track restored from the last session. ``resume`` (or ``toggle``) then
        starts it from there. Only possible while nothing is loaded."""
        if self._state.status is not PlaybackStatus.IDLE:
            return False
        duration = float(song.duration) or None
        # Unknown length (e.g. a live stream) can't be seeked: start over.
        position = min(float(position), max(0.0, duration - 1)) if duration else 0.0
        self._update(
            status=PlaybackStatus.PAUSED,
            song=song,
            position=max(0.0, float(position)),
            duration=duration,
            can_seek=bool(song.duration),
            error=None,
            retryable=False,
            detail=None,
            local=False,
        )
        return True

    async def toggle(self) -> None:
        """Pause, resume, restart, or retry depending on the current status."""
        status = self._state.status
        if status in ACTIVE:
            await self.pause()  # type: ignore[attr-defined]
        elif status is PlaybackStatus.PAUSED:
            await self.resume()  # type: ignore[attr-defined]
        elif status is PlaybackStatus.ERROR:
            await self.retry()  # type: ignore[attr-defined]
        elif (
            status in (PlaybackStatus.STOPPED, PlaybackStatus.ENDED)
            and self._state.song
        ):
            await self.play(self._state.song)  # type: ignore[attr-defined]


class SimulatedPlayer(BasePlayer):
    """Plays nothing; advances a clock. Used for ``--mock`` and UI tests.

    The UI labels this engine "simulated" so it is never mistaken for audio.
    """

    name = "simulated"

    def __init__(
        self,
        volume: int = 80,
        *,
        load_delay: float = 0.3,
        speed: float = 1.0,
        tick: float = 0.25,
    ) -> None:
        super().__init__(volume, notify_interval=0.0)
        self._load_delay = load_delay
        self._speed = speed
        self._tick = tick
        self._task: asyncio.Task[None] | None = None

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self, start: float) -> None:
        if self._load_delay:
            await asyncio.sleep(self._load_delay)
        loop = asyncio.get_running_loop()
        began = loop.time()
        self._update(status=PlaybackStatus.PLAYING, position=start, detail=None)
        duration = self._state.duration
        while True:
            await asyncio.sleep(self._tick)
            position = start + (loop.time() - began) * self._speed
            if duration and position >= duration:
                self._update(status=PlaybackStatus.ENDED, position=float(duration))
                return
            self._update(position=position)

    def _start(self, start: float, **changes: Any) -> None:
        self._cancel()
        self._update(status=PlaybackStatus.LOADING, position=start, **changes)
        self._task = asyncio.create_task(self._run(start))

    async def play(self, song: Song, start: float = 0.0) -> None:
        self._start(
            start,
            song=song,
            duration=float(song.duration) or None,
            error=None,
            can_seek=bool(song.duration),
            detail="buffering",
        )

    async def pause(self) -> None:
        if self._state.status in ACTIVE:
            self._cancel()
            self._update(status=PlaybackStatus.PAUSED)

    async def resume(self) -> None:
        if self._state.status is PlaybackStatus.PAUSED:
            self._start(self._state.position)

    async def stop(self) -> None:
        if self._state.status not in (PlaybackStatus.IDLE, PlaybackStatus.STOPPED):
            self._cancel()
            self._update(status=PlaybackStatus.STOPPED, position=0.0, detail=None)

    async def set_volume(self, value: int) -> None:
        self._update(volume=clamp_volume(value))

    async def set_fx(self, fx: AudioFx) -> None:
        self._update(fx=fx.clamped())

    async def seek(self, position: float) -> bool:
        state = self._state
        if not state.can_seek or not state.duration:
            return False
        position = max(0.0, min(position, state.duration - 1))
        if state.status is PlaybackStatus.PAUSED:
            self._update(position=position)
        elif state.status in ACTIVE:
            self._start(position)
        else:
            return False
        return True

    async def retry(self) -> None:
        if self._state.song is not None:
            await self.play(self._state.song, self._state.position)

    async def aclose(self) -> None:
        self._cancel()

    def shutdown(self) -> None:
        with contextlib.suppress(RuntimeError):
            self._cancel()
