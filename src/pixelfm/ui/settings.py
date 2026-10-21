"""Settings view: every row changes a real, persisted preference."""

from __future__ import annotations

from textual.theme import BUILTIN_THEMES
from textual.widgets import Static

from pixelfm.config import resolve_effects
from pixelfm.logs import default_log_path
from pixelfm.paths import cache_dir, display_path
from pixelfm.services.artwork import ArtworkMode
from pixelfm.services.library import LIKED_ORDERS
from pixelfm.services.party import PartyMode, RavePattern
from pixelfm.styles import THEMES
from pixelfm.ui.theming import MUSIC
from pixelfm.widgets import SettingsList, StatusBar, TextPrompt

LAYOUTS = ("auto", "full", "compact")
EFFECTS = ("auto", "full", "reduced")


class SettingsMixin:
    """Mixed into :class:`~pixelfm.app.PixelFMApp`."""

    def _theme_names(self) -> list[str]:
        extra = [
            name
            for name in ("nord", "dracula", "tokyo-night")
            if name in BUILTIN_THEMES
        ]
        return [MUSIC] + [t.name for t in THEMES] + extra

    def _settings_rows(self) -> list[tuple[str, str, str, bool]]:
        on_off = lambda flag: "[b $primary]on[/]" if flag else "[$text-muted]off[/]"  # noqa: E731
        effects_note = (
            ""
            if self._effects_mode != "auto"
            else (f" [$text-muted](now {'full' if self.effects else 'reduced'})[/]")
        )
        theme_note = (
            " [$text-muted](colours from the current track's artwork)[/]"
            if self._theme_choice == MUSIC
            else ""
        )
        if self._party_running():
            party = f"[b $secondary]{self._party_style}[/] [$text-muted](P: off)[/]"
        elif reason := self._party_allowed():
            party = f"[$text-muted]off · {reason}[/]"
        else:
            party = (
                f"[$text-muted]off · P starts {self._party_style} "
                "(ambient, pulse, strobe, rave; R starts rave)[/]"
            )
        rave = (
            f"[b]{self._rave_pattern.label.lower()}[/] [$text-muted](R cycles "
            "while RAVE is on: auto, chase, dance floor, laser, bass, rainbow)[/]"
        )
        return [
            ("theme", "Theme", f"[b]{self._theme_choice}[/]{theme_note}", True),
            ("party", "Party mode", party, True),
            ("rave_pattern", "Rave pattern", rave, True),
            (
                "layout",
                "Layout",
                f"[b]{self._layout}[/] [$text-muted](v toggles)[/]",
                True,
            ),
            ("effects", "Effects", f"[b]{self._effects_mode}[/]{effects_note}", True),
            ("visualizer", "Meters & spectrum", on_off(self.visualizer), True),
            (
                "lyrics",
                "Lyrics",
                f"{on_off(self.lyrics_on)} [$text-muted](y; from lrclib.net)[/]",
                True,
            ),
            (
                "artwork",
                "Artwork style",
                f"[b]{self.artwork_mode}[/] [$text-muted](m)[/]",
                True,
            ),
            ("liked_order", "Liked songs order", f"[b]{self._liked_order}[/]", True),
            ("spotify_client_id", "Spotify client ID", self._spotify_id_label(), True),
            ("spotify_account", "Spotify sign-in", self._spotify_account_label(), True),
            (
                "downloads_dir",
                "Downloads folder",
                f"[b]{display_path(self._downloads_dir())}[/] "
                "[$text-muted](enter to edit)[/]",
                True,
            ),
            (
                "clear_history",
                "Clear play history",
                "[$text-muted]enter, then confirm[/]",
                True,
            ),
            (
                "volume",
                "Volume",
                f"[b]{self._volume}[/] [$text-muted](← →, or + -)[/]",
                True,
            ),
            ("engine", "Playback engine", f"{self.player.name}", False),
        ]

    def _spotify_id_label(self) -> str:
        client_id = self._spotify_client_id()
        if not client_id:
            return "[$text-muted]not set · enter to set up playlist import (I)[/]"
        return (
            f"[b]{client_id[:6]}…{client_id[-4:]}[/] [$text-muted](enter to change)[/]"
        )

    def _spotify_account_label(self) -> str:
        client = self._spotify() if self._spotify_client_id() else None
        try:
            signed_in = client is not None and client.signed_in
        except Exception:  # keychain unavailable
            signed_in = False
        if signed_in:
            store = getattr(self.token_store, "name", "keychain")
            return f"[b]signed in[/] [$text-muted](kept in {store}; enter signs out)[/]"
        return "[$text-muted]not signed in (PixelFM asks when you import)[/]"

    def _refresh_settings(self) -> None:
        try:
            settings = self.query_one(SettingsList)
        except Exception:
            return
        settings.set_rows(self._settings_rows())
        library = (
            display_path(self.library.path)
            if self.library.path
            else "in memory (not saved)"
        )
        self.query_one("#settings-paths", Static).update(
            "[b $primary]STORAGE[/]\n"
            f"[$text-muted]library  [/]{library}\n"
            f"[$text-muted]downloads[/]{' '}{display_path(self._downloads_dir())}\n"
            f"[$text-muted]caches   [/]{display_path(cache_dir())}\n"
            f"[$text-muted]log      [/]{display_path(default_log_path())}\n\n"
            "[$text-muted]Settings are saved in the library and override config.toml; "
            "command-line flags override both for one session.[/]"
        )

    def action_setting_change(self, direction: int) -> None:
        key = self.query_one(SettingsList).setting
        status = self.query_one(StatusBar)

        def cycle(values: list[str] | tuple[str, ...], current: str) -> str:
            index = values.index(current) if current in values else 0
            return values[(index + direction) % len(values)]

        if key == "theme":
            choice = cycle(self._theme_names(), self._theme_choice)
            self._apply_theme_choice(choice)
            self._save_pref("theme", choice)
            self.load_artwork(self._newest_song())
        elif key == "party":
            current = self._party_style if self._party_running() else PartyMode.OFF
            style = PartyMode(cycle(list(PartyMode), current))
            if style is PartyMode.OFF:
                self._set_party(False)
            elif reason := self._party_allowed():
                status.show(reason)
                return
            else:
                self._party_style = style
                self._save_pref("party_mode", str(style))
                self._set_party(True)
        elif key == "rave_pattern":
            self._rave_pattern = RavePattern(
                cycle([str(p) for p in RavePattern], str(self._rave_pattern))
            )
            self._save_pref("rave_pattern", str(self._rave_pattern))
            if self._party is not None and hasattr(self._party, "pattern"):
                self._party.pattern = self._rave_pattern
        elif key == "layout":
            self._layout = cycle(LAYOUTS, self._layout)
            self._save_pref("layout", self._layout)
            self._apply_layout()
        elif key == "effects":
            self._effects_mode = cycle(EFFECTS, self._effects_mode)
            self._save_pref("effects", self._effects_mode)
            self.effects = resolve_effects(self._effects_mode)  # type: ignore[arg-type]
            self.query_one(StatusBar).animate = self.effects
            self.visualizer = (
                bool(self._pref("visualizer", self.config.visualizer)) and self.effects
            )
            self.load_artwork(self._newest_song())
            self._update_animation(self.playback)
            if self._party_wanted:  # reduced effects pause the party
                self._set_party(self.effects, save=False)
        elif key == "visualizer":
            self.action_toggle_visualizer()
            return
        elif key == "lyrics":
            self.action_toggle_lyrics()
            return
        elif key == "artwork":
            modes = [m.value for m in ArtworkMode]
            self.artwork_mode = ArtworkMode(cycle(modes, self.artwork_mode.value))
            self.load_artwork(self._newest_song())
        elif key == "liked_order":
            self._liked_order = cycle(list(LIKED_ORDERS), self._liked_order)
            self._save_pref("liked_order", self._liked_order)
        elif key == "downloads_dir":
            if direction < 0:
                return

            def done(text: str | None) -> None:
                if text is None:
                    return
                self._save_pref("downloads_dir", text.strip())
                folder = display_path(self._downloads_dir())
                status.show(f"Downloads go to {folder} from now on.")
                self._refresh_settings()

            self.push_screen(
                TextPrompt("Downloads folder", str(self._downloads_dir()), "folder"),
                done,
            )
            return
        elif key == "spotify_client_id":
            if direction > 0:
                self.action_spotify_setup()
            return
        elif key == "spotify_account":
            if direction > 0:
                self.action_spotify_sign_out()
            return
        elif key == "clear_history":
            if direction > 0:
                self.action_clear_history()
            return
        elif key == "volume":
            self.action_volume(direction)
        else:
            return
        status.show("Setting saved.")
        self._refresh_settings()
