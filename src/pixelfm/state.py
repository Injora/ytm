"""Playback state models and lifecycle transitions, independent of the UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pixelfm.models import Song


class PlaybackStatus(StrEnum):
    """Lifecycle of the playback engine."""

    IDLE = "idle"  # nothing loaded yet
    LOADING = "loading"  # resolving, buffering, seeking, or reconnecting
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"  # stopped by the user
    ENDED = "ended"  # track finished naturally
    ERROR = "error"


_S = PlaybackStatus

#: Allowed lifecycle transitions. Anything else is a programming error.
TRANSITIONS: dict[PlaybackStatus, frozenset[PlaybackStatus]] = {
    _S.IDLE: frozenset({_S.LOADING, _S.PAUSED}),  # PAUSED: a restored, cued track
    _S.LOADING: frozenset({_S.PLAYING, _S.PAUSED, _S.STOPPED, _S.ERROR, _S.ENDED}),
    _S.PLAYING: frozenset({_S.LOADING, _S.PAUSED, _S.STOPPED, _S.ENDED, _S.ERROR}),
    _S.PAUSED: frozenset({_S.LOADING, _S.STOPPED, _S.ERROR}),
    _S.STOPPED: frozenset({_S.LOADING}),
    _S.ENDED: frozenset({_S.LOADING, _S.STOPPED}),
    _S.ERROR: frozenset({_S.LOADING, _S.STOPPED}),
}

#: Statuses in which an audio process may be running or starting.
ACTIVE = frozenset({_S.LOADING, _S.PLAYING})


class InvalidTransition(RuntimeError):
    """Raised when the engine attempts an undefined lifecycle transition."""


def check_transition(old: PlaybackStatus, new: PlaybackStatus) -> None:
    """Raise :class:`InvalidTransition` unless ``old -> new`` is allowed."""
    if old is not new and new not in TRANSITIONS[old]:
        raise InvalidTransition(f"{old} -> {new}")


@dataclass(frozen=True, slots=True)
class AudioFx:
    """Audible effects applied by the engine. Gains are in dB."""

    bass: int = 0
    treble: int = 0
    normalize: bool = False

    LIMIT = 12

    def clamped(self) -> AudioFx:
        limit = self.LIMIT
        return AudioFx(
            max(-limit, min(limit, int(self.bass))),
            max(-limit, min(limit, int(self.treble))),
            bool(self.normalize),
        )


@dataclass(frozen=True, slots=True)
class PlaybackState:
    """Immutable snapshot of the engine, published to the UI on change.

    Attributes:
        status: Lifecycle status.
        song: Loaded track, if any.
        position: Playback position in seconds, as reported by the player.
        duration: Track length in seconds, or ``None`` if unknown/live.
        volume: Output volume, 0-100.
        detail: Short sub-status, e.g. ``"resolving stream"``.
        error: User-facing error message when ``status`` is ERROR.
        retryable: Whether retrying the failed operation may help.
        can_seek: Whether :meth:`seek` is supported for this track.
    """

    status: PlaybackStatus = PlaybackStatus.IDLE
    song: Song | None = None
    position: float = 0.0
    duration: float | None = None
    volume: int = 80
    detail: str | None = None
    error: str | None = None
    retryable: bool = False
    can_seek: bool = False
    fx: AudioFx = AudioFx()
    local: bool = False  # playing a downloaded file

    @property
    def progress(self) -> float:
        """Fraction played in ``[0, 1]``; 0 when the duration is unknown."""
        if not self.duration:
            return 0.0
        return max(0.0, min(1.0, self.position / self.duration))
