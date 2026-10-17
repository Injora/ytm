from __future__ import annotations

import pytest

from pixelfm.state import (
    TRANSITIONS,
    InvalidTransition,
    PlaybackState,
    PlaybackStatus,
    check_transition,
)

S = PlaybackStatus


def test_every_status_has_transitions() -> None:
    assert set(TRANSITIONS) == set(PlaybackStatus)


@pytest.mark.parametrize(
    "old,new",
    [
        (S.IDLE, S.LOADING),
        (S.LOADING, S.PLAYING),
        (S.PLAYING, S.PAUSED),
        (S.PAUSED, S.LOADING),
        (S.PLAYING, S.ENDED),
        (S.ENDED, S.LOADING),
        (S.LOADING, S.ERROR),
        (S.ERROR, S.LOADING),
        (S.PLAYING, S.STOPPED),
        (S.STOPPED, S.LOADING),
        (S.PLAYING, S.PLAYING),  # same status is always allowed
        (S.IDLE, S.PAUSED),  # a restored track cued at its saved position
    ],
)
def test_allowed_transitions(old: PlaybackStatus, new: PlaybackStatus) -> None:
    check_transition(old, new)


@pytest.mark.parametrize(
    "old,new",
    [
        (S.IDLE, S.PLAYING),
        (S.STOPPED, S.PLAYING),
        (S.PAUSED, S.PLAYING),  # resume must rebuffer through LOADING
        (S.ENDED, S.PAUSED),
        (S.ERROR, S.PLAYING),
    ],
)
def test_rejected_transitions(old: PlaybackStatus, new: PlaybackStatus) -> None:
    with pytest.raises(InvalidTransition):
        check_transition(old, new)


def test_progress() -> None:
    assert PlaybackState().progress == 0.0
    assert PlaybackState(position=30, duration=120).progress == 0.25
    assert PlaybackState(position=500, duration=120).progress == 1.0
    assert PlaybackState(position=30, duration=None).progress == 0.0
