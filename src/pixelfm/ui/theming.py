"""Music-derived theme and Party Mode lighting.

Theme changes go through Textual's theme system, which restyles every
widget (~25 ms plus a repaint here), so they are rare: a short crossfade when
the track changes, and in Party Mode at most one step every
:data:`~pixelfm.services.party.THEME_STEP_SECONDS`. Party lighting itself
animates only a handful of booth widgets (panel borders, the light bar, the
timeline, the meters), at most :data:`~pixelfm.services.party.MAX_FPS` times
a second.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

from textual import work
from textual.color import Color
from textual.css.query import NoMatches

from pixelfm.services.artwork import ArtworkMode
from pixelfm.services.palette import (
    DEFAULT_PALETTE,
    Palette,
    blend,
    mix,
    palette_from_theme,
    theme_from_palette,
    to_hex,
    with_accents,
)
from pixelfm.services.party import (
    RAVE_BORDER_EVERY,
    PartyFrame,
    PartyLights,
    PartyMode,
    RaveFrame,
    RaveLights,
    RavePattern,
)
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import (
    BoothHeader,
    LightBar,
    Spectrum,
    StereoMeter,
    Timeline,
    ViewTabs,
)

if TYPE_CHECKING:
    from pixelfm.models import Song

#: The "Theme" setting value that follows the current track's artwork.
MUSIC = "music"
#: Name of the Textual theme that the music palette is registered under.
DYNAMIC_THEME = "pixelfm-music"
TRANSITION_STEPS = 6
TRANSITION_INTERVAL = 0.15
#: Booth panels whose borders carry Party Mode lighting, left to right.
PARTY_PANELS = ("#deck", "#signal", "#mixer", "#booth-queue-panel")
PARTY_STYLES = tuple(m for m in PartyMode if m is not PartyMode.OFF)
#: RAVE lights the panels of the visible view: (selector, position around the
#: screen, 0..1). One shared clock and offset positions keep regions moving
#: together, like one light rig, instead of each blinking on its own.
RAVE_REGIONS: dict[str, tuple[tuple[str, float], ...]] = {
    "booth-view": (
        ("#deck", 0.2),
        ("#signal", 0.42),
        ("#mixer", 0.6),
        ("#booth-queue-panel", 0.8),
    ),
    "search-view": (("#search", 0.15), ("#results", 0.4), ("#queue", 0.75)),
    "queue-view": (("#queue-table", 0.45),),
    "playlists-view": (("#playlist-nav", 0.25), ("#playlist-table", 0.6)),
    "liked-view": (("#liked-table", 0.45),),
    "downloads-view": (("#downloads-table", 0.45),),
    "recent-view": (("#recent-table", 0.45),),
    "settings-view": (("#settings", 0.45),),
}
#: Fixed positions of the regions every view shares.
TABS_AT, NOW_PLAYING_AT, FOOTER_AT = 0.08, 0.9, 0.97
#: How much light the footer and status line get (backgrounds stay dark).
FLOOR_GLOW = 0.16
BORDER_RULES = ("border_top", "border_right", "border_bottom", "border_left")
#: Regions that repaint a lot when their border changes (the deck redraws its
#: whole cover) are relit less often, in frames between changes.
SLOW_REGIONS = {"#deck": 8}


class ThemingMixin:
    """Mixed into :class:`~pixelfm.app.PixelFMApp`."""

    # -------------------------------------------------------- theme choice

    def _setup_themes(self, choice: str) -> None:
        self._paint(DEFAULT_PALETTE)
        self._apply_theme_choice(choice)

    def _apply_theme_choice(self, choice: str) -> bool:
        """Switch the Theme setting. Returns ``False`` for an unknown theme."""
        if choice == MUSIC:
            self._theme_choice = MUSIC
            self.theme = DYNAMIC_THEME
            self._refresh_palette()
            return True
        if choice in self.available_themes and choice != DYNAMIC_THEME:
            self._theme_choice = choice
            self._stop_palette_transition()
            if not self._party_running():
                self.theme = choice
            return True
        return False

    def _music_theme(self) -> bool:
        return self._theme_choice == MUSIC and not self.no_color

    def _base_palette(self) -> Palette:
        """What the UI shows with Party Mode off."""
        if self._theme_choice == MUSIC:
            return self._palette_target
        theme = self.get_theme(self._theme_choice)
        try:
            return palette_from_theme(theme) if theme is not None else DEFAULT_PALETTE
        except (KeyError, AttributeError):  # third-party theme without our roles
            return DEFAULT_PALETTE

    # ------------------------------------------------------ music palette

    def _refresh_palette(self, song: Song | None = None) -> None:
        """Follow the newest track (called whenever its artwork loads)."""
        if self._music_theme():
            self.load_palette(song if song is not None else self._newest_song())

    @work(exclusive=True, group="palette")
    async def load_palette(self, song: Song | None) -> None:
        """Extract (or fetch from cache) the palette off the UI thread."""
        palette = await self.palettes.palette_for(song)
        if song != self._newest_song() or not self._music_theme():
            return  # superseded by a newer track, or the theme changed
        if palette == self._palette_target:
            return
        self._palette_target = palette
        if not self._party_running():
            self._transition_to(palette)

    def _paint(self, palette: Palette) -> None:
        """Apply ``palette`` to the whole UI immediately."""
        self._palette_now = palette
        self.register_theme(theme_from_palette(palette, DYNAMIC_THEME))
        if self.theme == DYNAMIC_THEME:
            self.refresh_css(animate=False)

    def _stop_palette_transition(self) -> None:
        if self._palette_timer is not None:
            self._palette_timer.stop()
            self._palette_timer = None

    def _transition_to(self, palette: Palette) -> None:
        """Crossfade to ``palette`` (instantly with reduced effects)."""
        self._stop_palette_transition()
        start = self._palette_now
        if start == palette:
            return
        if not self.effects or self.no_color:
            self._paint(palette)
            self._after_palette()
            return
        step = 0

        def tick() -> None:
            nonlocal step
            step += 1
            t = step / TRANSITION_STEPS
            eased = t * t * (3 - 2 * t)  # smoothstep
            self._paint(blend(start, palette, eased))
            if step >= TRANSITION_STEPS:
                self._stop_palette_transition()
                self._after_palette()

        self._palette_timer = self.set_interval(
            TRANSITION_INTERVAL, tick, repeat=TRANSITION_STEPS
        )

    def _after_palette(self) -> None:
        # Mono and glyph artwork are tinted with the accent: re-render them.
        if self.artwork_mode is not ArtworkMode.COLOR:
            self.load_artwork(self._newest_song())

    # ---------------------------------------------------------- party mode

    def _party_allowed(self) -> str | None:
        """Why Party Mode can't run, or ``None``."""
        if self.no_color:
            return "Party Mode is off while NO_COLOR is set."
        if not self.effects:
            return (
                'Party Mode needs effects; set Effects to "full" in Settings (8). '
                "Reduced effects keep the screen still."
            )
        return None

    def _party_running(self) -> bool:
        return self._party is not None

    def action_toggle_party(self) -> None:
        """``P`` / the header button: party on (last style) or off, instantly."""
        if self._party_running():
            self._set_party(False)
            self._status("Party Mode off.")
            return
        if reason := self._party_allowed():
            self._status(reason)
            return
        self._set_party(True)
        self._status(
            f"Party Mode on: {self._party_style}. P turns it off; "
            "change the style in Settings (8)."
        )

    def _set_party(self, on: bool, *, save: bool = True) -> None:
        if save:  # unsaved switches (e.g. reduced effects) keep the preference
            self._save_pref("party", on)
            self._party_wanted = on
        self._stop_party()
        if on and self._party_allowed() is None:
            lights: PartyLights | RaveLights
            if self._party_style is PartyMode.RAVE:
                lights = RaveLights(
                    self._rave_pattern, base=self._palette_now.background
                )
            else:
                lights = PartyLights(
                    self._party_style,
                    panels=len(PARTY_PANELS),
                    base=self._palette_now.background,
                )
            self._party = lights
            self._party_t0 = time.monotonic()
            self._party_accents = None
            # One timer, replaced (never added to) on every switch.
            self._party_timer = self.set_interval(1 / lights.fps, self._party_tick)
            self.screen.add_class("-party")
            self.screen.set_class(isinstance(lights, RaveLights), "-rave")
            if self.theme != DYNAMIC_THEME:
                self._paint(self._base_palette())
                self.theme = DYNAMIC_THEME
            self._party_tick()
        else:
            self._restore_after_party()
        self._render_party_indicator()
        self._refresh_settings()

    def _stop_party(self) -> None:
        if self._party_timer is not None:
            self._party_timer.stop()
            self._party_timer = None
        self._party = None

    def _restore_after_party(self) -> None:
        """Back to the normal theme, with every lighting override removed."""
        self.screen.remove_class("-party")
        self.screen.remove_class("-rave")
        self._clear_rave_styles()
        with contextlib.suppress(NoMatches):
            for selector in (*PARTY_PANELS, "#now-playing"):
                widget = self.query_one(selector)
                for rule in (
                    "border_top",
                    "border_right",
                    "border_bottom",
                    "border_left",
                    "border_title_color",
                ):
                    widget.styles.clear_rule(rule)
                widget.refresh()
            self.query_one(Timeline).accent = None
            self.query_one(StereoMeter).accent = None
            self.query_one(Spectrum).accent = None
            self.query_one(LightBar).colours = ()
        if self._theme_choice == MUSIC:
            self._transition_to(self._palette_target)
        else:
            self._stop_palette_transition()
            self.theme = self._theme_choice
            self._palette_now = self._base_palette()

    def _party_tick(self) -> None:
        lights = self._party
        if lights is None:
            return
        state = self.playback
        level = (
            self.player.stereo_level()
            if state.status is PlaybackStatus.PLAYING
            else None
        )
        loudness = level.loudness if level else None
        t = time.monotonic() - self._party_t0
        with contextlib.suppress(NoMatches):  # ticks can race shutdown
            if isinstance(lights, RaveLights):
                self._apply_rave_frame(lights, t, loudness)
            else:
                self._apply_party_frame(lights.frame(t, loudness))

    def _apply_party_frame(self, frame: PartyFrame) -> None:
        ascii_border = "ascii" if not self.unicode else "solid"
        if self._active_view() == "booth-view":
            for selector, colour in zip(PARTY_PANELS, frame.panels, strict=True):
                widget = self.query_one(selector)
                c = Color(*colour)
                widget.styles.border = (ascii_border, c)
                widget.styles.border_title_color = c
            self.query_one(LightBar).colours = tuple(to_hex(c) for c in frame.bar)
            self.query_one(Timeline).accent = to_hex(frame.accent)
            accents = (to_hex(frame.accent), to_hex(frame.accent2))
            self.query_one(StereoMeter).accent = accents
            self.query_one(Spectrum).accent = accents
        else:
            c = Color(*frame.panels[0])
            now_playing = self.query_one("#now-playing")
            now_playing.styles.border = (ascii_border, c)
            now_playing.styles.border_title_color = c
        if frame.theme_colours != self._party_accents:
            # Whole-UI accents: rate-limited by the engine (THEME_STEP_SECONDS).
            self._party_accents = frame.theme_colours
            self._paint(with_accents(self._base_palette(), *frame.theme_colours))
        self._render_party_indicator(frame)

    # ---------------------------------------------------------------- RAVE

    def action_rave(self) -> None:
        """``R``: start RAVE mode, or switch to its next pattern while on."""
        if isinstance(self._party, RaveLights):
            self._rave_pattern = self._rave_pattern.next()
            self._save_pref("rave_pattern", str(self._rave_pattern))
            self._party.pattern = self._rave_pattern
            self._status(
                f"RAVE pattern: {self._rave_pattern.label}. R for the next one, "
                "P turns the lights off."
            )
            self._render_party_indicator()
            self._refresh_settings()
            return
        if reason := self._party_allowed():
            self._status(reason)
            return
        self._party_style = PartyMode.RAVE
        self._save_pref("party_mode", str(PartyMode.RAVE))
        self._set_party(True)
        self._status(
            f"RAVE MODE on: {self._rave_pattern.label}. R cycles patterns, "
            "P turns it off."
        )

    def _rave_style(self, selector: str, kind: str, colour: Color) -> None:
        """Set a border light, skipping the restyle if nothing changed."""
        if self._rave_applied.get(selector) == (kind, colour):
            return
        widget = self.query_one(selector)
        widget.styles.border = (kind, colour)
        widget.styles.border_title_color = colour
        self._rave_applied[selector] = (kind, colour)

    def _rave_glow(self, selector: str, colour: Color) -> None:
        """A dim glow on a bar's background (text stays readable)."""
        key = f"bg:{selector}"
        if self._rave_applied.get(key) == ("bg", colour):
            return
        self.query_one(selector).styles.background = colour
        self._rave_applied[key] = ("bg", colour)

    def _apply_rave_frame(
        self, lights: RaveLights, t: float, loudness: float | None
    ) -> None:
        view = self._active_view()
        regions = RAVE_REGIONS.get(view, ())
        if view == "booth-view" and self._sixel_cover_showing():
            # A Sixel cover is re-sent whenever the deck repaints; keep the
            # deck border still and light everything around it instead.
            regions = tuple(r for r in regions if r[0] != "#deck")
        positions = [p for _, p in regions]
        positions += [TABS_AT, NOW_PLAYING_AT, FOOTER_AT]
        state = self.playback
        progress = state.position / state.duration if state.duration else 0.0
        frame = lights.frame(t, loudness, positions, progress)
        *panel_colours, tabs, now_playing, floor = frame.zones
        self._rave_frames += 1
        borders = self._rave_frames % RAVE_BORDER_EVERY == 0
        focused = self.focused
        line = "solid" if self.unicode else "ascii"
        focus_line = "heavy" if self.unicode else "ascii"
        for (selector, _), colour in zip(regions, panel_colours, strict=True):
            every = SLOW_REGIONS.get(selector, RAVE_BORDER_EVERY)
            if self._rave_frames % every:
                continue
            widget = self.query_one(selector)
            has_focus = focused is not None and (
                focused is widget or widget in focused.ancestors
            )
            self._rave_style(
                selector, focus_line if has_focus else line, Color(*colour)
            )
        if view != "booth-view" and borders:
            self._rave_style("#now-playing", line, Color(*now_playing))
        strip = tuple(to_hex(c) for c in frame.strip)
        self.query_one("#top-lights", LightBar).colours = strip
        if view == "booth-view":
            self.query_one("#party-lights", LightBar).colours = strip[::-1]
            timeline = self.query_one(Timeline)
            timeline.gradient = tuple(to_hex(c) for c in frame.progress)
            meters = (to_hex(frame.meter[0]), to_hex(frame.meter[1]))
            self.query_one(StereoMeter).accent = meters
            self.query_one(Spectrum).accent = meters
        self.query_one(ViewTabs).accent = to_hex(tabs)
        self.query_one(BoothHeader).set_logo_colours(strip[:: max(1, len(strip) // 24)])
        background = self._palette_now.background
        glow = Color(*mix(background, floor, FLOOR_GLOW))
        if borders:
            self._rave_glow("Footer", glow)
            self._rave_glow("#status", glow)
        sixel_booth = view == "booth-view" and self._sixel_cover_showing()
        if frame.theme_colours != self._party_accents and not sixel_booth:
            # Whole-UI accents (selected rows, focused buttons): every second.
            # Skipped over a Sixel cover, which a full repaint re-sends.
            self._party_accents = frame.theme_colours
            self._paint(with_accents(self._base_palette(), *frame.theme_colours))
        self._render_party_indicator(frame)

    def _sixel_cover_showing(self) -> bool:
        if self.graphics != "sixel":
            return False
        with contextlib.suppress(NoMatches):
            return self.query_one("#deck-art").has_class("-image")
        return False

    def _clear_rave_styles(self) -> None:
        """Remove every inline style RAVE set; nothing is left behind."""
        applied, self._rave_applied = self._rave_applied, {}
        with contextlib.suppress(NoMatches):
            for key in applied:
                if key.startswith("bg:"):
                    widget = self.query_one(key[3:])
                    widget.styles.clear_rule("background")
                else:
                    widget = self.query_one(key)
                    for rule in (*BORDER_RULES, "border_title_color"):
                        widget.styles.clear_rule(rule)
                widget.refresh()
            self.query_one("#top-lights", LightBar).colours = ()
            self.query_one(Timeline).gradient = ()
            self.query_one(ViewTabs).accent = None
            self.query_one(BoothHeader).set_logo_colours(None)

    def _render_party_indicator(
        self, frame: PartyFrame | RaveFrame | None = None
    ) -> None:
        header = self.query_one(BoothHeader)
        if not self._party_running():
            header.set_party("PARTY MODE: OFF", on=False)
            return
        if isinstance(self._party, RaveLights):
            label = self._rave_pattern.label
            if self._rave_pattern is RavePattern.AUTO and isinstance(frame, RaveFrame):
                label = f"AUTO · {frame.pattern.label}"
            if (
                isinstance(frame, RaveFrame)
                and frame.pattern is RavePattern.BASS
                and not frame.reactive
            ):
                label += " · NO SIGNAL"  # nothing measured: no fake pulse
            header.set_party(f"RAVE MODE: ON · {label}", on=True)
            return
        style = str(self._party_style).upper()
        note = ""
        if (
            frame is not None
            and self._party_style is not PartyMode.AMBIENT
            and not frame.reactive
        ):
            note = " · NO SIGNAL"  # nothing measured: lights stay calm
        header.set_party(f"PARTY MODE: ON · {style}{note}", on=True)
