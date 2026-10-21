"""Party Mode lighting: coordinated neon colour patterns for the booth.

The engine is pure and deterministic: :meth:`PartyLights.frame` maps a time
and an optional *measured* loudness to colours. It never invents audio data;
PULSE and STROBE only react to loudness when the player actually measures it
and otherwise fall back to calm, time-based lighting (and say so).

Safety limits, enforced here rather than by convention:

* frames are produced at most :data:`MAX_FPS` times a second;
* backgrounds are never animated, and no colour is brighter than the neon
  set itself (no white flashes);
* STROBE changes colour at most :data:`MAX_FLASHES_PER_SECOND` times a
  second, well under the 3 Hz photosensitivity threshold (WCAG 2.3.1);
* intensity changes are smoothed (attack/decay) instead of jumping.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pixelfm.services.palette import RGB, mix

MAX_FPS = 8  # AMBIENT, PULSE, STROBE
MAX_FLASHES_PER_SECOND = 2
#: Theme accents (whole-UI colours) change at most this often.
THEME_STEP_SECONDS = 2.0

#: RAVE runs faster: cheap per-widget lighting at RAVE_FPS, and whole-theme
#: accent steps (a full restyle, ~25-50 ms) only every RAVE_THEME_STEP_SECONDS.
RAVE_FPS = 16
RAVE_MAX_FPS = 20
#: Panel borders repaint their whole panel, so they change every
#: RAVE_BORDER_EVERY frames (8 a second); small widgets (strips, logo, tabs,
#: progress, meters) follow every frame.
RAVE_BORDER_EVERY = 2
RAVE_THEME_STEP_SECONDS = 1.0
#: Hard ceiling on abrupt colour swaps (DANCE FLOOR), below the 3 Hz
#: photosensitivity guideline (WCAG 2.3.1).
RAVE_MAX_SWAPS_PER_SECOND = 3


class PartyMode(StrEnum):
    OFF = "off"
    AMBIENT = "ambient"  # slow colour drift
    PULSE = "pulse"  # brightness follows measured loudness
    STROBE = "strobe"  # brief colour swaps on loudness onsets, rate-limited
    RAVE = "rave"  # fast, coordinated DJ light show (see RaveLights)

    def next(self) -> PartyMode:
        order = list(PartyMode)
        return order[(order.index(self) + 1) % len(order)]


#: Coordinated neon set: cyan, magenta, violet, electric blue, red, amber.
NEON: tuple[RGB, ...] = (
    (0, 229, 255),
    (255, 43, 214),
    (155, 92, 255),
    (47, 107, 255),
    (255, 59, 92),
    (255, 176, 32),
)
#: Lighting patterns, rotated per scene so the show does not just cycle hues.
PATTERNS = ("sweep", "chase", "split", "breathe")
#: Frames per second by mode (all within MAX_FPS).
FPS = {PartyMode.AMBIENT: 5, PartyMode.PULSE: 8, PartyMode.STROBE: 8}
#: Seconds per scene (colour pair + pattern).
SCENE_SECONDS = {PartyMode.AMBIENT: 12.0, PartyMode.PULSE: 8.0, PartyMode.STROBE: 8.0}
#: Darkest a light gets (fraction of full colour, blended toward the base).
DIM = 0.35
QUIET_DB = -45.0
LOUD_DB = -8.0
ONSET_DB = 3.0  # rise over the running average that counts as an onset


@dataclass(frozen=True, slots=True)
class PartyFrame:
    """Colours for one lighting frame."""

    panels: tuple[RGB, ...]  # border light per booth panel
    bar: tuple[RGB, ...]  # light-bar segments, left to right
    accent: RGB  # timeline and meter accent
    accent2: RGB
    scene: int
    theme_colours: tuple[RGB, RGB]  # whole-UI accents (change slowly)
    intensity: float  # 0..1 after smoothing
    reactive: bool  # driven by measured audio this frame
    flash: bool  # a STROBE colour swap is showing


def _scene_pair(scene: int) -> tuple[RGB, RGB]:
    """Each scene pairs a colour with one ~2 steps around the neon wheel."""
    a = NEON[scene % len(NEON)]
    b = NEON[(scene + 2) % len(NEON)]
    return a, b


def loudness_fraction(db: float) -> float:
    return max(0.0, min(1.0, (db - QUIET_DB) / (LOUD_DB - QUIET_DB)))


class PartyLights:
    """Stateful frame generator (smoothing and strobe limits need history)."""

    def __init__(
        self,
        mode: PartyMode,
        *,
        panels: int = 4,
        segments: int = 32,
        base: RGB = (7, 8, 15),
    ) -> None:
        self.mode = mode
        self.panels = panels
        self.segments = segments
        self.base = base
        self._intensity = 0.6
        self._average_db: float | None = None
        self._flash_times: list[float] = []
        self._flash_until = -1.0
        self._last_theme_step = -math.inf
        self._theme_colours: tuple[RGB, RGB] | None = None

    @property
    def fps(self) -> int:
        return FPS.get(self.mode, MAX_FPS)

    def _dim(self, colour: RGB, intensity: float) -> RGB:
        return mix(self.base, colour, DIM + (1 - DIM) * intensity)

    def _scene(self, t: float) -> tuple[int, float]:
        length = SCENE_SECONDS.get(self.mode, 10.0)
        return int(t // length), (t % length) / length

    def _update_intensity(self, loudness: float | None, t: float) -> bool:
        """Smoothed 0..1 intensity; ``True`` if driven by measured audio."""
        if self.mode is PartyMode.AMBIENT:
            target = 0.75 + 0.25 * math.sin(t * 2 * math.pi / 6)  # slow breath
            self._intensity += (target - self._intensity) * 0.3
            return False
        if loudness is None:
            self._intensity += (0.6 - self._intensity) * 0.2  # steady, no motion
            return False
        target = loudness_fraction(loudness)
        rate = 0.6 if target > self._intensity else 0.25  # fast attack, slow decay
        self._intensity += (target - self._intensity) * rate
        return True

    def _strobe(self, loudness: float | None, t: float) -> bool:
        """Whether a (rate-limited) colour swap shows at ``t``."""
        if t < self._flash_until:
            return True
        self._flash_times = [x for x in self._flash_times if t - x < 1.0]
        onset = False
        if loudness is not None:
            average = self._average_db if self._average_db is not None else loudness
            onset = loudness - average >= ONSET_DB
            self._average_db = average + (loudness - average) * 0.15
        elif not self._flash_times or t - self._flash_times[-1] >= 1.0:
            onset = True  # no measurements: a calm 1 Hz swap, not "beats"
        spaced = not self._flash_times or t - self._flash_times[-1] >= 0.35
        if onset and spaced and len(self._flash_times) < MAX_FLASHES_PER_SECOND:
            self._flash_times.append(t)
            self._flash_until = t + 0.15
            return True
        return False

    def _theme(self, t: float, a: RGB, b: RGB, phase: float) -> tuple[RGB, RGB]:
        """Whole-UI accents drift along the scene, updated every few seconds."""
        if (
            self._theme_colours is None
            or t - self._last_theme_step >= THEME_STEP_SECONDS
        ):
            self._last_theme_step = t
            nxt_a, nxt_b = _scene_pair(self._scene(t)[0] + 1)
            self._theme_colours = (mix(a, nxt_a, phase), mix(b, nxt_b, phase))
        return self._theme_colours

    def frame(self, t: float, loudness: float | None = None) -> PartyFrame:
        """Lighting at time ``t`` (seconds), given measured loudness in dBFS."""
        scene, phase = self._scene(t)
        a, b = _scene_pair(scene)
        pattern = PATTERNS[scene % len(PATTERNS)]
        reactive = self._update_intensity(loudness, t)
        intensity = self._intensity
        flash = self.mode is PartyMode.STROBE and self._strobe(loudness, t)
        if flash:
            a, b = b, a

        def at(position: float) -> RGB:
            """Colour at a 0..1 position across the booth for this pattern."""
            if pattern == "sweep":
                wave = 0.5 + 0.5 * math.sin(2 * math.pi * (position - t / 6))
                return self._dim(mix(a, b, wave), intensity)
            if pattern == "chase":
                lit = int(position * 8 + t * 2) % 2 == 0
                return self._dim(a if lit else b, intensity * (1.0 if lit else 0.7))
            if pattern == "split":
                return self._dim(mix(a, b, position), intensity)
            # breathe: one colour, brightness only
            return self._dim(mix(a, b, phase), intensity)

        segments = max(1, self.segments)
        bar = tuple(at(i / max(1, segments - 1)) for i in range(segments))
        panels = tuple(at(i / max(1, self.panels - 1)) for i in range(self.panels))
        return PartyFrame(
            panels=panels,
            bar=bar,
            accent=self._dim(a, max(0.75, intensity)),
            accent2=self._dim(b, max(0.75, intensity)),
            scene=scene,
            theme_colours=self._theme(t, *_scene_pair(scene), phase),
            intensity=intensity,
            reactive=reactive,
            flash=flash,
        )


# ---------------------------------------------------------------- RAVE


class RavePattern(StrEnum):
    AUTO = "auto"  # rotates through the patterns below
    CHASE = "chase"  # colour chase around the screen
    DANCE = "dance"  # dance floor: alternating coordinated palettes
    LASER = "laser"  # a narrow beam sweeping across
    BASS = "bass"  # brightness follows the measured loudness
    RAINBOW = "rainbow"  # every region cycles the full palette, offset

    def next(self) -> RavePattern:
        order = list(RavePattern)
        return order[(order.index(self) + 1) % len(order)]

    @property
    def label(self) -> str:
        return RAVE_LABELS[self]


RAVE_LABELS = {
    RavePattern.AUTO: "AUTO",
    RavePattern.CHASE: "COLOR CHASE",
    RavePattern.DANCE: "DANCE FLOOR",
    RavePattern.LASER: "LASER SWEEP",
    RavePattern.BASS: "BASS PULSE",
    RavePattern.RAINBOW: "RAINBOW CIRCUIT",
}
#: AUTO plays each pattern for this long, opening with a sweep across the UI.
AUTO_SECONDS = 8.0
SWEEP_SECONDS = 0.6
AUTO_ORDER = (
    RavePattern.CHASE,
    RavePattern.RAINBOW,
    RavePattern.DANCE,
    RavePattern.LASER,
    RavePattern.BASS,
)
#: Dance-floor palettes: no red (saturated red flashes are the riskiest).
DANCE_PALETTES: tuple[tuple[RGB, RGB], ...] = (
    (NEON[0], NEON[1]),  # cyan / magenta
    (NEON[3], NEON[2]),  # electric blue / violet
)
DANCE_SWAP_SECONDS = 0.4  # 2.5 swaps a second, under the 3 per second limit
CHASE_COLOURS_PER_SECOND = 2.5
RAINBOW_CYCLES_PER_SECOND = 0.45
LASER_SWEEPS_PER_SECOND = 1.4
LASER_WIDTH = 0.09


def neon_at(x: float, *, hold: float = 0.0) -> RGB:
    """Colour at position ``x`` on the neon wheel (one unit per colour).

    ``hold`` keeps each colour pure for that fraction of its slot and blends
    quickly in the rest, which reads as a chase rather than a slow fade."""
    n = len(NEON)
    i = math.floor(x)
    f = x - i
    if hold:
        f = 0.0 if f < hold else (f - hold) / (1 - hold)
        f = f * f * (3 - 2 * f)
    return mix(NEON[i % n], NEON[(i + 1) % n], f)


@dataclass(frozen=True, slots=True)
class RaveFrame:
    """Colours for one RAVE frame; every region shares the same clock."""

    zones: tuple[RGB, ...]  # one colour per requested position
    strip: tuple[RGB, ...]  # light strip segments, left to right
    progress: tuple[RGB, ...]  # progress-bar gradient, left to right
    meter: tuple[RGB, RGB]  # meter colours (normal, hot)
    theme_colours: tuple[RGB, RGB]  # whole-UI accents (change every second)
    pattern: RavePattern  # what is showing now (AUTO resolved)
    intensity: float
    reactive: bool  # brightness driven by measured audio this frame
    swaps: int  # abrupt colour swaps so far (DANCE FLOOR)


class RaveLights:
    """The RAVE light rig. Pure apart from smoothing and rate-limit history."""

    def __init__(
        self,
        pattern: RavePattern = RavePattern.AUTO,
        *,
        segments: int = 48,
        base: RGB = (7, 8, 15),
    ) -> None:
        self.pattern = pattern
        self.mode = PartyMode.RAVE
        self.segments = segments
        self.base = base
        self._intensity = 0.85
        self._swap_times: list[float] = []
        self._swaps = 0
        self._parity: int | None = None
        self._last_theme = -math.inf
        self._theme_colours: tuple[RGB, RGB] | None = None

    @property
    def fps(self) -> int:
        return RAVE_FPS

    def current(self, t: float) -> RavePattern:
        if self.pattern is not RavePattern.AUTO:
            return self.pattern
        return AUTO_ORDER[int(t // AUTO_SECONDS) % len(AUTO_ORDER)]

    def _lit(self, colour: RGB, intensity: float) -> RGB:
        return mix(self.base, colour, DIM + (1 - DIM) * intensity)

    def _update_intensity(self, loudness: float | None, pattern: RavePattern) -> bool:
        if loudness is None:
            self._intensity += (0.85 - self._intensity) * 0.3  # steady, no motion
            return False
        target = loudness_fraction(loudness)
        if pattern is not RavePattern.BASS:
            target = 0.7 + 0.3 * target  # the other patterns react a little
        rate = 0.75 if target > self._intensity else 0.35  # fast attack
        self._intensity += (target - self._intensity) * rate
        return True

    def _dance_parity(self, t: float) -> int:
        """Alternation for DANCE FLOOR, never faster than the swap limit."""
        wanted = int(t // DANCE_SWAP_SECONDS) % 2
        if self._parity is None:
            self._parity = wanted
        elif wanted != self._parity:
            self._swap_times = [x for x in self._swap_times if t - x < 1.0]
            if len(self._swap_times) < RAVE_MAX_SWAPS_PER_SECOND:
                self._parity = wanted
                self._swap_times.append(t)
                self._swaps += 1
        return self._parity

    def _colour(
        self, pattern: RavePattern, t: float, p: float, intensity: float
    ) -> RGB:
        """Colour of the region at position ``p`` (0..1 around the screen)."""
        n = len(NEON)
        if pattern is RavePattern.CHASE:
            colour = neon_at(t * CHASE_COLOURS_PER_SECOND - p * n, hold=0.55)
        elif pattern is RavePattern.RAINBOW:
            colour = neon_at((t * RAINBOW_CYCLES_PER_SECOND - p) * n)
        elif pattern is RavePattern.DANCE:
            a, b = DANCE_PALETTES[int(t // (DANCE_SWAP_SECONDS * 2)) % 2]
            parity = self._dance_parity(t)
            colour = a if (int(p * 8) + parity) % 2 == 0 else b
        elif pattern is RavePattern.LASER:
            beam = (t * LASER_SWEEPS_PER_SECOND) % 1.0
            ray = NEON[0] if int(t * LASER_SWEEPS_PER_SECOND) % 2 else NEON[1]
            distance = abs(p - beam)
            glow = max(0.0, 1.0 - distance / LASER_WIDTH)
            room = neon_at(t * 1.2 + 2, hold=0.4)  # the room keeps moving too
            colour = mix(room, ray, glow)  # a bright beam sweeping across
            intensity = intensity * (0.45 + 0.55 * glow)
        else:  # BASS: slow chase, brightness from the measured level
            colour = neon_at(t * 0.8 - p * 2, hold=0.3)
        return self._lit(colour, intensity)

    def frame(
        self,
        t: float,
        loudness: float | None = None,
        positions: Sequence[float] = (),
        progress: float = 0.0,
    ) -> RaveFrame:
        """Lighting at time ``t`` for regions at ``positions`` (0..1), given
        the measured loudness (dBFS) and playback progress (0..1)."""
        pattern = self.current(t)
        reactive = self._update_intensity(loudness, pattern)
        intensity = self._intensity

        def at(p: float) -> RGB:
            colour = self._colour(pattern, t, p, intensity)
            if self.pattern is RavePattern.AUTO and t % AUTO_SECONDS < SWEEP_SECONDS:
                # Pattern change: a coordinated sweep runs across every region.
                edge = (t % AUTO_SECONDS) / SWEEP_SECONDS
                if abs(p - edge) < 0.12:
                    colour = self._lit(NEON[int(t // AUTO_SECONDS) % len(NEON)], 1.0)
            return colour

        segments = max(1, self.segments)
        strip = tuple(at(i / (segments - 1 or 1)) for i in range(segments))
        progress_bar = tuple(
            self._lit(neon_at(progress * len(NEON) + i / 4, hold=0.0), 1.0)
            for i in range(16)
        )
        hot = self._lit(NEON[1] if pattern is not RavePattern.DANCE else NEON[2], 1.0)
        meter = (at(0.45), hot)
        if (
            self._theme_colours is None
            or t - self._last_theme >= RAVE_THEME_STEP_SECONDS
        ):
            self._last_theme = t
            self._theme_colours = (
                neon_at(t * CHASE_COLOURS_PER_SECOND / 2),
                neon_at(t * CHASE_COLOURS_PER_SECOND / 2 + 2),
            )
        return RaveFrame(
            zones=tuple(at(p) for p in positions),
            strip=strip,
            progress=progress_bar,
            meter=meter,
            theme_colours=self._theme_colours,
            pattern=pattern,
            intensity=intensity,
            reactive=reactive,
            swaps=self._swaps,
        )
