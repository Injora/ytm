"""The PixelFM Textual application: a terminal DJ booth for one deck."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import random
import signal
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from pathlib import Path
from typing import Any

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Input,
    OptionList,
    Static,
)

from pixelfm import __version__
from pixelfm.config import Config, detect_truecolor, detect_unicode, resolve_effects
from pixelfm.logs import default_log_path
from pixelfm.models import Song
from pixelfm.paths import cache_dir, display_path, library_path
from pixelfm.queue import PlayQueue, QueueEntry, RepeatMode
from pixelfm.services.artwork import ArtworkMode, ArtworkService, ColorDepth
from pixelfm.services.cache import DiskCache
from pixelfm.services.credentials import default_store
from pixelfm.services.deps import check_dependencies
from pixelfm.services.downloads import DownloadManager
from pixelfm.services.ffplay import FFplayPlayer
from pixelfm.services.graphics import cell_pixels, widget_class
from pixelfm.services.library import (
    DownloadRecord,
    Library,
    LibraryError,
    LibraryTrack,
    is_placeholder,
)
from pixelfm.services.lyrics import LyricsService
from pixelfm.services.media_session import MediaSession, NowPlayingInfo, helper_path
from pixelfm.services.palette import DEFAULT_PALETTE, Palette, PaletteService
from pixelfm.services.party import PartyMode, RavePattern
from pixelfm.services.playback import Player, SimulatedPlayer
from pixelfm.services.search import (
    CachedSearchProvider,
    MockSearchProvider,
    SearchError,
    SearchProvider,
)
from pixelfm.services.streams import LocalFirstResolver, StreamResolver
from pixelfm.services.youtube import YouTubeSearchProvider
from pixelfm.state import ACTIVE, PlaybackState, PlaybackStatus
from pixelfm.styles import STYLESHEET, THEMES, glyphs_for, hex_to_rgb
from pixelfm.ui.booth import BoothMixin
from pixelfm.ui.downloads import DownloadsMixin
from pixelfm.ui.importing import ImportMixin
from pixelfm.ui.library import LIST_VIEWS, LibraryMixin
from pixelfm.ui.settings import SettingsMixin
from pixelfm.ui.theming import MUSIC, ThemingMixin
from pixelfm.widgets import (
    AlbumArt,
    BoothHeader,
    BoothQueueTable,
    ConfirmDialog,
    DownloadsTable,
    Fader,
    HelpScreen,
    LevelMeter,
    LightBar,
    LikedTable,
    LyricsView,
    Marks,
    NowPlaying,
    PlaylistNav,
    PlaylistTable,
    QueuePanel,
    QueueTable,
    RecentTable,
    ResultsTable,
    Row,
    SettingsList,
    SongTable,
    Spectrum,
    StatusBar,
    StatusLevel,
    StereoMeter,
    Timeline,
    Toggle,
    Transport,
    ViewTabs,
    WelcomeScreen,
)
from pixelfm.widgets.views import VIEW_LABELS, VIEWS, section_id_for_playlist

log = logging.getLogger(__name__)

SEEK_STEP = 10.0
#: "Previous" restarts the current track when more than this many seconds in.
RESTART_THRESHOLD = 3.0
#: Debounce for session saves after a change (queue edit, seek, view, ...).
QUEUE_SAVE_DELAY = 0.5
#: While playing, the position is saved at least this often, so a crash or
#: power loss loses at most a few seconds.
POSITION_SAVE_INTERVAL = 5.0
SESSION_VERSION = 1
BOOTH = "booth-view"


def build_provider(config: Config) -> SearchProvider:
    """Create the configured search provider, wrapped in a result cache."""
    if config.provider == "mock":
        inner: SearchProvider = MockSearchProvider(latency=config.mock_latency)
    else:
        inner = YouTubeSearchProvider(timeout=config.search_timeout)
    return CachedSearchProvider(inner)


def open_library() -> tuple[Library, str | None]:
    """Open the on-disk library, or an in-memory one plus a warning."""
    path = library_path()
    try:
        return Library.open(path), None
    except LibraryError as exc:
        log.error("library unavailable: %s", exc)
        return Library.memory(), f"{exc} Changes this session will not be saved."


class PixelFMApp(
    BoothMixin,
    LibraryMixin,
    ImportMixin,
    DownloadsMixin,
    SettingsMixin,
    ThemingMixin,
    App[None],
):
    """Search, queue, library, downloads, artwork, meters, and playback."""

    TITLE = "PixelFM"
    CSS_PATH = STYLESHEET
    ENABLE_COMMAND_PALETTE = False
    AUTO_FOCUS = "#tx-play"
    HORIZONTAL_BREAKPOINTS = [(0, "-xnarrow"), (64, "-narrow"), (110, "-wide")]
    # Very small terminals get "-tiny"; full/compact is decided in _apply_layout.
    VERTICAL_BREAKPOINTS = [(0, "-tiny"), (22, "-roomy")]
    COMPACT_BELOW = (100, 30)  # width, height for layout = "auto"

    # Letters and digits never fire while a text box has focus: the Input
    # consumes printable keys before app bindings are checked.
    BINDINGS = [
        *[
            Binding(str(n), f"show_view('{view}')", label.title(), show=False)
            for n, (view, label, _) in enumerate(VIEWS, start=1)
        ],
        Binding("slash", "focus_search", "Search", key_display="/"),
        Binding("s", "focus_search", "Search", show=False),
        Binding("ctrl+f", "focus_filter", "Filter", show=False),
        Binding("space", "toggle_playback", "Play/Pause", key_display="space"),
        Binding("n", "next_track", "Next"),
        Binding("b", "previous_track", "Prev"),
        Binding("f", "toggle_favorite", "Like"),
        Binding("D", "download", "Download"),
        Binding("a", "enqueue", "Queue", show=False),
        Binding("A", "play_next", "Play next", show=False),
        Binding("p", "add_to_playlist", "Playlist", show=False),
        Binding("r", "cycle_repeat", "Repeat", show=False),
        Binding("z", "toggle_shuffle", "Shuffle", show=False),
        Binding("Z", "shuffle_playlist", "Shuffle playlist", show=False),
        Binding("P", "toggle_party", "Party", show=False),
        Binding("R", "rave", "Rave", show=True),
        Binding("I", "import_playlist", "Import playlist", show=False),
        Binding("M", "review_matches", "Review matches", show=False),
        Binding("X", "stop_matching", "Stop matching", show=False),
        Binding("minus", "volume(-1)", "Vol-", show=False),
        Binding("plus,equals_sign", "volume(1)", "Vol+", show=False),
        Binding("left_curly_bracket", "bass(-1)", "Bass-", show=False),
        Binding("right_curly_bracket", "bass(1)", "Bass+", show=False),
        Binding("left_parenthesis", "treble(-1)", "Treble-", show=False),
        Binding("right_parenthesis", "treble(1)", "Treble+", show=False),
        Binding("N", "toggle_normalize", "Normalize", show=False),
        Binding("left_square_bracket", "seek(-1)", "-10s", show=False),
        Binding("right_square_bracket", "seek(1)", "+10s", show=False),
        Binding("x", "stop", "Stop", show=False),
        Binding("c", "download_cancel", "Cancel download", show=False),
        Binding("m", "cycle_artwork", "Art", show=False),
        Binding("v", "toggle_layout", "Layout", show=False),
        Binding("V", "toggle_visualizer", "Meters", show=False),
        Binding("y", "toggle_lyrics", "Lyrics", show=False),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        config: Config | None = None,
        provider: SearchProvider | None = None,
        artwork: ArtworkService | None = None,
        player: Player | None = None,
        library: Library | None = None,
        *,
        unicode: bool | None = None,
        truecolor: bool | None = None,
        startup_warnings: Sequence[str] = (),
        effects: bool | None = None,
        welcome: bool | None = None,
        downloader: Any = None,
        cli_overrides: Collection[str] = (),
        rng: random.Random | None = None,
        graphics: str | None = None,
        image_widget: Any = None,
        media: MediaSession | bool | None = True,
        token_store: Any = None,
        spotify_options: dict[str, Any] | None = None,
        lyrics: LyricsService | None = None,
    ) -> None:
        super().__init__()
        self.config = config or Config()
        self._cli_overrides = set(cli_overrides)
        self._startup_warnings = list(startup_warnings)
        if library is None:
            library, warning = open_library()
            if warning:
                self._startup_warnings.insert(0, warning)
        self.library = library

        # Library state used by the player and markers (loaded before the player).
        self._download_records: dict[str, DownloadRecord] = {}
        self._download_paths: dict[str, Path] = self._safe_load(
            library.download_paths, {}
        )
        self._missing_downloads: set[str] = set()
        self._favorites: set[str] = set()
        self._unavailable: set[str] = set()
        self._list_tracks: dict[str, list[LibraryTrack]] = {}

        self.provider = provider or build_provider(self.config)
        self.artwork = artwork or ArtworkService(
            disk=DiskCache(cache_dir() / "thumbnails")
        )
        self.palettes = PaletteService(self.artwork)
        self.lyrics = lyrics or LyricsService(disk=DiskCache(cache_dir() / "lyrics"))
        self._lyrics_song: str | None = None  # id of the song the lyrics are for
        self._lyrics_asked = False  # fetched only once the song plays
        self.lyrics_on = self._pref("lyrics", True) is not False
        self.player = player or self._build_player()
        kwargs = {"downloader": downloader} if downloader else {}
        self.downloads = DownloadManager(
            self._downloads_dir, self._on_download_update, **kwargs
        )

        self.unicode = (
            detect_unicode(self.config.unicode) if unicode is None else unicode
        )
        if truecolor is None:
            truecolor = detect_truecolor(self.config.color)
        self.color_depth = ColorDepth.TRUECOLOR if truecolor else ColorDepth.REDUCED
        # NO_COLOR (https://no-color.org): Textual renders monochrome; ASCII
        # artwork reads better than grey blocks.
        self.no_color = "NO_COLOR" in os.environ
        # Always start from the configured style (colour by default): `m` and
        # Settings change it for this session only, so a stray key press
        # can't leave the cover as ASCII for good.
        mode = self.config.artwork_mode
        if self.no_color and mode == "color":
            mode = "mono"
        self.artwork_mode = ArtworkMode(mode)
        self._effects_mode = self._pref("effects", self.config.effects)
        if self._effects_mode not in ("auto", "full", "reduced"):
            self._effects_mode = self.config.effects
        self.effects = (
            resolve_effects(self._effects_mode) if effects is None else effects
        )
        self.visualizer = (
            bool(self._pref("visualizer", self.config.visualizer)) and self.effects
        )
        self._layout: str = self._pref("layout", self.config.layout)
        if self._layout not in ("auto", "full", "compact"):
            self._layout = self.config.layout
        self._liked_order = self._pref("liked_order", "added")
        if self._liked_order not in ("added", "title", "artist"):
            self._liked_order = "added"
        self._welcome = welcome
        # Theme and Party Mode (see ThemingMixin).
        self._theme_choice: str = MUSIC
        self._palette_now: Palette = DEFAULT_PALETTE
        self._palette_target: Palette = DEFAULT_PALETTE
        self._palette_timer: Any = None
        self._party: Any = None
        self._party_timer: Any = None
        self._party_t0 = 0.0
        self._party_accents: Any = None
        style = self._pref("party_mode", str(PartyMode.AMBIENT))
        self._party_style = (
            PartyMode(style)
            if style in {str(m) for m in PartyMode} and style != PartyMode.OFF
            else PartyMode.AMBIENT
        )
        self._party_wanted = self._pref("party", False) is True
        pattern = self._pref("rave_pattern", str(RavePattern.AUTO))
        self._rave_pattern = (
            RavePattern(pattern)
            if pattern in {str(p) for p in RavePattern}
            else RavePattern.AUTO
        )
        self._rave_applied: dict[str, Any] = {}
        self._rave_frames = 0
        self._frame = 0
        self._decay_until = 0.0
        self.glyphs = glyphs_for(self.unicode)

        #: Terminal image protocol ("sixel"/"kitty", detected in __main__), and
        #: the widget class that draws real covers with it.
        self.graphics = graphics if "NO_COLOR" not in os.environ else None
        if image_widget is None and self.graphics:
            image_widget = widget_class(self.graphics)
        self._image_widget = image_widget
        # macOS media keys / Control Centre (``True``: use the helper if this
        # Mac has it; tests pass a session or ``None``).
        if media is True:
            executable = helper_path()
            media = (
                MediaSession(self._on_media_command, executable) if executable else None
            )
        self.media: MediaSession | None = media or None
        self._media_art: dict[str, str] = {}
        # Playlist import (see ImportMixin). Tokens live in the system keychain.
        self.token_store = token_store if token_store is not None else default_store()
        self._spotify_options = spotify_options or {}
        self._spotify_client: Any = None
        self._importing: Any = None
        self._matching: tuple[int, int, int] | None = None
        self._matcher_instance: Any = None
        self._media_published = 0.0
        self.queue = PlayQueue()
        self._shuffle_rng = rng or random.Random()
        #: Playlist the queue was last filled from: {"id", "name", "shuffled"}.
        self._queue_source: dict[str, Any] | None = None
        self._last_shuffle: dict[int, list[str]] = {}
        self._play_note: tuple[int, str] | None = None  # (entry uid, message)
        self.playback = self.player.get_state()
        self._volume = self.playback.volume
        self._fx = self._load_fx()
        self._seek_target: float | None = None
        self._result_count = 0
        self._signals: list[signal.Signals] = []
        self._restored_position = 0.0
        self._queue_timer: Any = None
        self._last_position_save = 0.0
        # Session persistence (see _save_queue / _restore_session).
        self._session_ready = False  # never save before the restore finished
        self._restored_uid: int | None = None  # restored entry not resumed yet
        self._restored_status: str | None = None
        self._saved_queue_key: Any = None
        self._saved_session_key: Any = None
        self._last_ui: dict[str, Any] = {}

        # Playback request bookkeeping (see _after_track).
        self._requested: QueueEntry | None = None
        self._play_seq = 0
        self._recorded_seq = 0
        self._consecutive_skips = 0

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _safe_load(fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:
            log.exception("library read failed")
            return default

    def _build_player(self) -> Player:
        if self.config.provider == "mock":
            return SimulatedPlayer(volume=self.config.volume)
        resolver = LocalFirstResolver(
            StreamResolver(), self._local_path, self._on_download_missing
        )
        return FFplayPlayer(
            resolver,
            binary=self.config.ffplay_path or "ffplay",
            volume=self.config.volume,
        )

    def _pref(self, key: str, default: Any) -> Any:
        """Saved UI preference, unless a command-line flag set it this session."""
        if key in self._cli_overrides:
            return default
        return self._safe_load(lambda: self.library.get_pref(key, default), default)

    def _save_pref(self, key: str, value: Any) -> None:
        self._safe_db(lambda: self.library.set_pref(key, value))

    def _safe_db[T](self, operation: Callable[[], T]) -> T | None:
        """Run a library operation; report database errors instead of crashing."""
        try:
            return operation()
        except LibraryError as exc:
            self._status(str(exc), StatusLevel.ERROR)
        except Exception as exc:  # sqlite3.Error and friends
            log.exception("library operation failed")
            self._status(f"Library error: {exc}", StatusLevel.ERROR)
        return None

    def _status(self, message: str, level: StatusLevel = StatusLevel.INFO) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one(StatusBar).show(message, level)

    # -------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        g = self.glyphs
        u = self.unicode
        yield LightBar(u, id="top-lights")
        yield BoothHeader(self.player.name, unicode=u)
        yield ViewTabs(id="tabs")
        with ContentSwitcher(initial=BOOTH, id="views"):
            with Horizontal(id=BOOTH):
                with Vertical(id="booth-main"):
                    yield LightBar(u, id="party-lights")
                    with Vertical(id="deck"):
                        with Horizontal(id="deck-row"):
                            yield AlbumArt(
                                24, 12, id="deck-art", image_widget=self._image_widget
                            )
                            with Vertical(id="deck-info"):
                                yield Static(id="deck-title")
                                yield Static(id="deck-artist")
                                yield Static(id="deck-meta")
                                yield Static(id="deck-state")
                                yield Timeline(g, id="deck-timeline")
                        yield Transport(u)
                    with Horizontal(id="booth-lower"):
                        with Vertical(id="signal"):
                            yield StereoMeter(g, u, id="meter")
                            yield Spectrum(g, u, id="spectrum")
                            yield LevelMeter(g.meter, id="loudness")
                        with Vertical(id="mixer"):
                            yield Fader("VOL", 0, 100, 5, id="fader-vol", unicode=u)
                            yield Fader(
                                "BASS", -12, 12, 2, " dB", id="fader-bass", unicode=u
                            )
                            yield Fader(
                                "TREB", -12, 12, 2, " dB", id="fader-treble", unicode=u
                            )
                            yield Toggle("NORM", id="toggle-norm")
                            yield Static(id="mixer-status")
                            yield LyricsView(u, id="lyrics")
                    yield Static(id="mixer-compact")
                with Vertical(id="booth-queue-panel"):
                    with Horizontal(id="booth-source"):
                        yield Static(id="source-label")
                        yield Button(
                            "⇄ SHUFFLE" if u else "SHUFFLE",
                            id="booth-shuffle",
                            compact=True,
                            classes="action",
                        )
                    yield BoothQueueTable(g, id="booth-queue", title="queue")
            with Vertical(id="search-view"):
                search = Input(
                    placeholder="Search YouTube for tracks or artists, then enter",
                    id="search",
                )
                search.border_title = "search"
                yield search
                with Horizontal(id="body"):
                    yield ResultsTable(g, id="results", title="results")
                    yield QueuePanel()
            with Vertical(id="queue-view"):
                yield Static(id="queue-empty", classes="empty")
                yield QueueTable(g, id="queue-table", title="queue")
            with Horizontal(id="playlists-view"):
                yield PlaylistNav(id="playlist-nav")
                with Vertical(id="playlist-main"):
                    with Horizontal(id="playlist-actions"):
                        yield Static(id="playlist-heading")
                        yield Button(
                            f"{g.playing} PLAY ALL",
                            id="pl-play",
                            compact=True,
                            classes="action",
                        )
                        yield Button(
                            "⇄ SHUFFLE" if u else "SHUFFLE",
                            id="pl-shuffle",
                            compact=True,
                            classes="action",
                        )
                        yield Button(
                            "⤓ IMPORT" if u else "IMPORT",
                            id="pl-import",
                            compact=True,
                            classes="action",
                        )
                    yield Input(
                        placeholder="filter this playlist (ctrl+f)",
                        id="playlist-filter",
                        classes="filter",
                    )
                    yield Static(id="playlist-empty", classes="empty")
                    yield PlaylistTable(g, id="playlist-table", title="playlist")
            with Vertical(id="liked-view", classes="list-view"):
                yield Static(id="liked-info", classes="view-info")
                yield Input(
                    placeholder="filter liked songs (ctrl+f)",
                    id="liked-filter",
                    classes="filter",
                )
                yield Static(id="liked-empty", classes="empty")
                yield LikedTable(g, id="liked-table", title="liked songs")
            with Vertical(id="downloads-view", classes="list-view"):
                yield Static(id="downloads-info", classes="view-info")
                yield Static(id="downloads-active", classes="empty")
                yield Input(
                    placeholder="filter downloads (ctrl+f)",
                    id="downloads-filter",
                    classes="filter",
                )
                yield Static(id="downloads-empty", classes="empty")
                yield DownloadsTable(g, id="downloads-table", title="downloaded songs")
            with Vertical(id="recent-view", classes="list-view"):
                yield Static(id="recent-info", classes="view-info")
                yield Input(
                    placeholder="filter history (ctrl+f)",
                    id="recent-filter",
                    classes="filter",
                )
                yield Static(id="recent-empty", classes="empty")
                yield RecentTable(g, id="recent-table", title="recently played")
            with Vertical(id="settings-view"):
                yield SettingsList(id="settings")
                yield Static(id="settings-paths")
        yield NowPlaying(g, (10, 5), self._image_widget)
        yield StatusBar(g, animate=self.effects)
        yield Footer(compact=True)

    def on_mount(self) -> None:
        for theme in THEMES:
            self.register_theme(theme)
        theme = self._pref("theme", self.config.theme)
        self._setup_themes(MUSIC)
        if theme != MUSIC and not self._apply_theme_choice(theme):
            known = ", ".join([MUSIC, *self._theme_names()[1:]])
            self._startup_warnings.append(
                f"Unknown theme {theme!r}; available: {known}"
            )
        if not self.unicode:
            self.screen.add_class("-ascii")
        self.query_one(PlaylistNav).border_title = "playlists"
        self.query_one("#booth-queue-panel").border_title = "up next"
        self.query_one("#deck").border_title = "deck a"
        self.query_one("#signal").border_title = "signal · measured"
        self.query_one("#mixer").border_title = "mixer"
        self.query_one("#lyrics").border_title = "lyrics"
        self.screen.add_class("-booth")

        self._fx_timer = self.set_interval(1 / 15, self._fx_tick, pause=True)
        self._load_preferences()
        session = self._restore_queue()
        self.player.subscribe(self._on_playback)
        self._cue_restored(session)
        self._install_signal_handlers()
        self._reload_library_sets()
        self._apply_layout(rerender=False)
        self._refresh_queue_views()
        self._sync_mixer()
        self._render_playback(self.playback)
        self.query_one(BoothHeader).set_view(VIEW_LABELS[BOOTH])
        self.query_one(StereoMeter).feed(None, "idle" if self.visualizer else "off")
        self.query_one(Spectrum).feed(None, "idle" if self.visualizer else "off")
        self.load_artwork(self._newest_song())
        if self._fx.bass or self._fx.treble or self._fx.normalize:
            self._control(self.player.set_fx(self._fx), "Effects")

        if self.provider.browsable:
            self.run_search("")
        elif self.queue.current is not None:
            self._status(
                "Restored your last session, paused. Press space to resume, ? for keys."
            )
        else:
            self._status("Ready. Press 2 or / to search YouTube, ? for keys.")
        self.query_one("#tx-play", Button).focus()
        self._restore_ui(session)
        self._render_queue_source()
        self._render_party_indicator()
        if self._party_wanted:
            self._set_party(True, save=False)
        self._session_ready = True
        if self.media is not None:
            self.run_worker(self._start_media(), group="media", exit_on_error=False)
        if self._startup_warnings and not self.provider.browsable:
            self._status(self._startup_warnings.pop(0), StatusLevel.ERROR)
        self._maybe_welcome()

    def _load_preferences(self) -> None:
        volume = self._pref("volume", None)
        if isinstance(volume, int) and not isinstance(volume, bool):
            self._volume = max(0, min(100, volume))
            if self._volume != self.playback.volume:
                self._control(self.player.set_volume(self._volume), "Volume")
        with contextlib.suppress(ValueError):
            self.queue.repeat = RepeatMode(self._pref("repeat", "off"))
        self.queue.shuffle = self._pref("shuffle", False) is True

    def _restore_queue(self) -> dict[str, Any]:
        """Restore the saved queue in its exact order. Playback is *not*
        started (see :meth:`_cue_restored`). Returns the session snapshot."""
        saved = self._safe_load(self.library.load_queue, None)
        if saved is None:
            return {}
        session = saved.session if isinstance(saved.session, dict) else {}
        if session.get("version", SESSION_VERSION) > SESSION_VERSION:
            session = {}  # written by a newer PixelFM: keep only the queue
        if not saved.songs:
            return session
        self.queue.restore(saved.songs, saved.current)
        current = self.queue.current
        position = saved.position if current else 0.0
        if current is not None and session.get("song_id") not in (
            None,
            current.song.id,
        ):
            position = 0.0  # stale snapshot: the position was for another track
        self._restored_position = position
        self._saved_queue_key = (tuple(s.id for s in saved.songs), saved.current)
        log.info(
            "restored queue: %s entries, current %s at %.1fs",
            len(saved.songs),
            saved.current,
            position,
        )
        return session

    def _cue_restored(self, session: dict[str, Any]) -> None:
        """Load the restored track paused at its saved position. Pressing play
        resumes from there; nothing plays until then."""
        entry = self.queue.current
        if entry is None:
            return
        if not self.player.cue(entry.song, self._restored_position):
            return
        self._requested = entry
        self._play_seq += 1
        self._recorded_seq = self._play_seq  # its play was counted last session
        self._restored_uid = entry.uid
        status = session.get("status")
        self._restored_status = status if isinstance(status, str) else None
        source = session.get("queue_source")
        if isinstance(source, dict) and isinstance(source.get("id"), int):
            with contextlib.suppress(LibraryError, Exception):
                playlist = self.library.playlist(source["id"])
                self._queue_source = {
                    "id": playlist.id,
                    "name": playlist.name,
                    "shuffled": source.get("shuffled") is True,
                }

    def _restore_ui(self, session: dict[str, Any]) -> None:
        """Reopen the saved view, playlist, and selected rows where possible."""
        view = session.get("view")
        if isinstance(view, str) and view in VIEW_LABELS and view != BOOTH:
            self.action_show_view(view)
        playlist_id = session.get("playlist_id")
        if isinstance(playlist_id, int) and not isinstance(playlist_id, bool):
            with contextlib.suppress(NoMatches):
                self._refresh_playlists()
                nav = self.query_one(PlaylistNav)
                nav.select_section(section_id_for_playlist(playlist_id))
                self._show_playlist()
                track = session.get("playlist_track")
                table = self.query_one(PlaylistTable)
                for i, song in enumerate(table.songs):
                    if song.id == track:
                        table.move_cursor(row=i)
                        break
        cursor = session.get("queue_cursor")
        if isinstance(cursor, int) and not isinstance(cursor, bool):
            for table in self.query(QueueTable):
                if 0 <= cursor < table.row_count:
                    table.move_cursor(row=cursor)

    def _maybe_welcome(self) -> None:
        """First run on a real library: show what's installed and where data lives."""
        show = self._welcome
        if show is None:
            show = self.library.persistent and not self.library.get_pref("welcomed")
        if not show:
            return
        deps = check_dependencies(
            ffplay=self.config.ffplay_path or "ffplay", versions=False
        )
        checks = [(d.name, d.found, d.detail, d.install) for d in deps]
        paths = [
            (
                "library",
                display_path(self.library.path)
                if self.library.path
                else "in memory (not saved)",
            ),
            ("downloads", display_path(self._downloads_dir())),
            ("caches", display_path(cache_dir())),
            ("log", display_path(default_log_path())),
        ]

        def done(_: None) -> None:
            self._save_pref("welcomed", __version__)

        self.push_screen(WelcomeScreen(checks, paths), done)

    # ----------------------------------------------------------- lifecycle

    def _install_signal_handlers(self) -> None:
        """Exit cleanly (stopping audio, restoring the terminal) on TERM/HUP/INT."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._on_terminate)
                self._signals.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    def _on_terminate(self) -> None:
        log.info("terminating on signal")
        self.save_session()  # before the player stops: keeps the position
        self.player.shutdown()
        if self.media is not None:
            self.media.shutdown()  # the helper clears Now Playing as it exits
        self.exit()

    async def action_quit(self) -> None:
        """Save the session and stop audio before exiting; never hang."""
        self.save_session()
        if self.media is not None:
            with contextlib.suppress(Exception):
                await self.media.aclose()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.downloads.aclose(), 1.0)
        try:
            await asyncio.wait_for(self.player.aclose(), 3.0)
        except Exception:
            log.exception("player did not close cleanly")
            self.player.shutdown()
        self.exit()

    def on_unmount(self) -> None:
        self.save_session()  # no-op if nothing changed since the last save
        self._stop_party()
        self._stop_palette_transition()
        with contextlib.suppress(Exception):
            self._fx_timer.pause()
        loop = asyncio.get_running_loop()
        for sig in self._signals:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(sig)
        self.player.shutdown()  # idempotent fallback for every exit path
        if self.media is not None:
            self.media.shutdown()

    def _fx_tick(self) -> None:
        with contextlib.suppress(NoMatches):  # ticks can race app shutdown
            self._animate_frame()

    # ---------------------------------------------------------------- views

    def _active_view(self) -> str:
        return self.query_one("#views", ContentSwitcher).current or BOOTH

    def action_show_view(self, view: str) -> None:
        self.query_one("#views", ContentSwitcher).current = view
        self.query_one(ViewTabs).active = view
        self.screen.set_class(view == BOOTH, "-booth")
        self.query_one(BoothHeader).set_view(VIEW_LABELS.get(view, view))
        self._refresh_view(view)
        self.action_focus_main()
        self._schedule_queue_save()

    def action_focus_main(self) -> None:
        """Focus the main list of the active view."""
        targets = {
            BOOTH: "#tx-play",
            "search-view": "#results",
            "queue-view": "#queue-table",
            "playlists-view": "#playlist-nav",
            "liked-view": "#liked-table",
            "downloads-view": "#downloads-table",
            "recent-view": "#recent-table",
            "settings-view": "#settings",
        }
        with contextlib.suppress(NoMatches):
            self.query_one(targets.get(self._active_view(), "#tx-play")).focus()

    def action_back(self) -> None:
        if not self._clear_filter_or_back():
            self.action_focus_main()

    def action_focus_search(self) -> None:
        if self._active_view() != "search-view":
            self.action_show_view("search-view")
        self.query_one("#search", Input).focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout(size=(event.size.width, event.size.height))

    def _apply_layout(
        self, rerender: bool = True, size: tuple[int, int] | None = None
    ) -> None:
        """Pick full or compact layout and size the artwork to match."""
        width, height = size or self.size
        min_width, min_height = self.COMPACT_BELOW
        compact = self._layout == "compact" or (
            self._layout == "auto" and (width < min_width or height < min_height)
        )
        self.screen.set_class(compact, "-compact")
        self.query_one(ViewTabs).compact = compact or width < 110
        base_w, base_h = self.config.artwork_width, self.config.artwork_height
        if compact:
            deck = (max(4, base_w * 5 // 8), max(2, base_h * 5 // 8))
        elif height >= 40:
            deck = (base_w * 2, base_h * 2)  # tall terminal: a big, detailed cover
        else:
            deck = (base_w, base_h)
        changed = self.query_one("#deck-art", AlbumArt).set_size(*deck)
        changed |= self.query_one("#art", AlbumArt).set_size(10, 5)
        if changed and rerender:
            self.load_artwork(self._newest_song())

    def action_toggle_layout(self) -> None:
        compact = self.screen.has_class("-compact")
        self._layout = "full" if compact else "compact"
        self._save_pref("layout", self._layout)
        self._apply_layout()
        self._status(f"{self._layout.capitalize()} layout.")

    # --------------------------------------------------------------- search

    @on(Input.Submitted, "#search")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query and not self.provider.browsable:
            self._status("Type something to search for.")
            return
        self.run_search(query)
        self.query_one(ResultsTable).focus()

    @on(Input.Changed, ".filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        for view, (filter_id, _, _) in LIST_VIEWS.items():
            if event.input.id == filter_id.lstrip("#"):
                if view == "downloads-view":
                    self._render_downloads()
                elif view == "playlists-view":
                    self._show_playlist()
                else:
                    self._refresh_view(view)

    @on(Input.Submitted, ".filter")
    def _on_filter_submitted(self, event: Input.Submitted) -> None:
        self.action_focus_main()

    @work(exclusive=True, group="search")
    async def run_search(self, query: str) -> None:
        """Query the provider in a worker; a newer search cancels this one."""
        table = self.query_one(ResultsTable)
        label = f"“{query}”" if self.unicode else f'"{query}"'
        self._status(
            f"Searching {self.provider.name} for {label}"
            if query
            else "Loading sample catalogue",
            StatusLevel.LOADING,
        )
        table.border_subtitle = "searching"
        try:
            songs = await self.provider.search(query, limit=self.config.search_limit)
        except SearchError as exc:
            self._status(str(exc), StatusLevel.ERROR)
            return
        except Exception as exc:  # UI boundary: report, never crash or print.
            log.exception("search failed")
            self._status(f"Unexpected search error: {exc}", StatusLevel.ERROR)
            return
        finally:
            table.border_subtitle = ""

        table.set_songs(songs)
        table.refresh_marks(Marks())  # rows are new: force a recompute below
        self._refresh_marks()
        self._result_count = len(songs)
        self._refresh_meta()
        if self._startup_warnings:
            self._status(self._startup_warnings.pop(0), StatusLevel.ERROR)
        elif not songs:
            self._status(f"No results for {label}.")
        elif query:
            noun = "result" if len(songs) == 1 else "results"
            self._status(f"{len(songs)} {noun} for {label}", StatusLevel.SUCCESS)
        else:
            self._status("Ready. Press / to search, ? for keys.")

    # -------------------------------------------------------------- artwork

    @work(exclusive=True, group="artwork")
    async def load_artwork(self, song: Song | None) -> None:
        """Render artwork for ``song`` at every artwork widget's size."""
        tint = hex_to_rgb(getattr(self.current_theme, "primary", None), (0, 229, 255))
        for art_widget in self.query(AlbumArt):
            if song is not None and self.unicode and art_widget.id == "deck-art":
                art_widget.set_busy(True)
            try:
                result = await self.artwork.artwork_for(
                    song,
                    art_widget.art_width,
                    art_widget.art_height,
                    self.artwork_mode,
                    self.color_depth,
                    self.unicode,
                    tint=tint,
                )
            finally:
                art_widget.set_busy(False)
            if song is not None and self._newest_song() != song:
                return  # a different track was selected meanwhile
            art_widget.show(result.art)
            if result.error and song is not None:
                log.info("artwork unavailable for %s: %s", song.id, result.error)
            elif (
                art_widget.supports_images
                and song is not None
                and self.artwork_mode is ArtworkMode.COLOR
            ):
                # The terminal can draw pictures: show the real cover.
                cell_w, cell_h = cell_pixels()
                cover = await self.artwork.cover_image(
                    song, art_widget.art_width * cell_w, art_widget.art_height * cell_h
                )
                if self._newest_song() != song:
                    return
                if cover is not None:
                    art_widget.show_image(cover)
        self._refresh_palette(song)  # bytes are cached now: the palette is cheap

    # ------------------------------------------------------------- playback

    def _control(self, operation: Awaitable[object], label: str) -> None:
        """Run a player command off the input handler, reporting failures."""

        async def guarded() -> None:
            try:
                await operation
            except Exception as exc:
                log.exception("%s failed", label)
                self._status(f"{label} failed: {exc}", StatusLevel.ERROR)

        self.run_worker(guarded(), group="playback", exit_on_error=False)

    def _play_entry(self, entry: QueueEntry, start: float = 0.0) -> None:
        """Ask the player for ``entry``; it becomes the one real events refer to."""
        if is_placeholder(entry.song.id):
            self._status(
                f"“{entry.song.title}” isn't matched to a YouTube video yet: open "
                "its playlist (4) and press M to find one.",
                StatusLevel.ERROR,
            )
            return
        self._requested = entry
        self._play_seq += 1
        self._restored_position = 0.0
        self._restored_uid = None
        self.load_artwork(entry.song)
        self._control(self.player.play(entry.song, start), "Play")
        self._refresh_queue_views()

    def _on_playback(self, state: PlaybackState) -> None:
        """Engine listener: re-render, record history, drive the queue."""
        previous, self.playback = self.playback, state
        # NoMatches: the app is shutting down and its widgets are already gone.
        with contextlib.suppress(NoMatches):
            self._handle_playback(previous, state)

    def _handle_playback(self, previous: PlaybackState, state: PlaybackState) -> None:
        self._render_playback(state)
        self._update_animation(state)
        if state.status is not PlaybackStatus.LOADING:
            self._seek_target = None
        if state.status is PlaybackStatus.PLAYING:
            self._restored_uid = None
            if time.monotonic() - self._last_position_save >= POSITION_SAVE_INTERVAL:
                self._last_position_save = time.monotonic()
                self._save_queue()
        if (previous.status, previous.song) != (state.status, state.song):
            self._schedule_queue_save()  # track change, pause, resume, end, ...
            self._publish_now_playing()
        elif time.monotonic() - self._media_published >= POSITION_SAVE_INTERVAL:
            self._publish_now_playing()  # keep the system's clock honest
        if (previous.status, previous.detail, previous.song) != (
            state.status,
            state.detail,
            state.song,
        ):
            self._announce(state)

    def _is_requested(self, state: PlaybackState) -> bool:
        return self._requested is not None and state.song == self._requested.song

    def _announce(self, state: PlaybackState) -> None:
        song = state.song
        title = song.title if song else ""
        match state.status:
            case PlaybackStatus.LOADING:
                detail = (state.detail or "buffering").capitalize()
                self._status(f"{detail}: {title}", StatusLevel.LOADING)
            case PlaybackStatus.PLAYING:
                if song is not None:
                    where = " (local file)" if state.local else ""
                    note, self._play_note = self._play_note, None
                    extra = (
                        f" · {note[1]}"
                        if note and self._requested and note[0] == self._requested.uid
                        else ""
                    )
                    self._status(
                        f"Now playing {song.title} by {song.artist}{where}{extra}",
                        StatusLevel.SUCCESS,
                    )
                    self._record_play(state)
            case PlaybackStatus.PAUSED:
                self._status(f"Paused {title}")
                self._schedule_queue_save()
            case PlaybackStatus.STOPPED:
                self._status("Stopped.")
            case PlaybackStatus.ENDED:
                self._after_track(state)
            case PlaybackStatus.ERROR:
                if (
                    self._restored_uid is not None
                    and song is not None
                    and self._is_requested(state)
                ):
                    # Resuming last session's track failed: keep the queue
                    # and the position, and say what to do.
                    reason = state.error or "unavailable"
                    if not state.retryable:
                        self._safe_db(
                            lambda: self.library.mark_unavailable(song, reason)
                        )
                        self._unavailable.add(song.id)
                    self._status(
                        f"Couldn't resume {song.title}: {reason}. Your queue is "
                        "unchanged: space retries, n skips, 3 edits the queue.",
                        StatusLevel.ERROR,
                    )
                    return
                if (
                    not state.retryable
                    and song is not None
                    and self._is_requested(state)
                ):
                    reason = state.error or "unavailable"
                    self._safe_db(lambda: self.library.mark_unavailable(song, reason))
                    self._unavailable.add(song.id)
                    self._after_track(state, skipped=reason)
                    return
                message = state.error or "Playback failed."
                if state.retryable:
                    message += " Press space to retry."
                self._status(message, StatusLevel.ERROR)

    def _record_play(self, state: PlaybackState) -> None:
        """History is written once per request, when audio really starts."""
        self._consecutive_skips = 0
        if state.song is None or not self._is_requested(state):
            return
        if self._recorded_seq == self._play_seq:
            return  # resume, seek, or volume restart of the same request
        self._recorded_seq = self._play_seq
        song = state.song

        def record() -> bool:
            self.library.record_play(song)
            return True

        if self._safe_db(record):
            self._unavailable.discard(song.id)
        if self._active_view() == "recent-view":
            self._refresh_recent()

    def _after_track(self, state: PlaybackState, skipped: str | None = None) -> None:
        """Advance the queue after a real end (or an unplayable track).

        Ignores events that do not belong to the current request: if the user
        already picked another track, the queue has moved on, and a late
        "ended" for the old one must not skip again.
        """
        requested = self._requested
        if requested is None or not self._is_requested(state):
            return
        result = self.queue.on_track_ended(requested.uid)
        if result.stale:
            return
        upcoming = result.entry
        if skipped is not None:
            self._consecutive_skips += 1
            if upcoming is not None and upcoming.uid == requested.uid:
                upcoming = self.queue.advance(manual=True)  # never repeat a dead track
            if self._consecutive_skips >= max(1, len(self.queue.entries)):
                upcoming = None  # every queued track failed; stop looping
            self._status(
                f"Skipped unavailable track: {requested.song.title} ({skipped})",
                StatusLevel.ERROR,
            )
        if upcoming is not None and (skipped is None or upcoming.uid != requested.uid):
            self._play_entry(upcoming)
            return
        self._refresh_queue_views()
        if skipped is None:
            self._status("End of queue.")

    def _render_playback(self, state: PlaybackState) -> None:
        upcoming = len(self.queue.upcoming)
        self.query_one(NowPlaying).update_state(
            state,
            f"{upcoming} up next" if upcoming else "",
            simulated=self.player.name == "simulated",
            flags=self._flags_markup(state),
            loading_glyph=(
                self.glyphs.spinner[self._frame % len(self.glyphs.spinner)]
                if self.effects
                else None
            ),
        )
        self._render_deck(state)
        self._refresh_marks()

    def _flags_markup(self, state: PlaybackState | None = None) -> str:
        g = self.glyphs
        parts = []
        if state and state.song and state.song.id in self._favorites:
            parts.append(f"[$secondary]{g.favorite}[/]")
        if self.queue.repeat is not RepeatMode.OFF:
            parts.append(f"[$secondary]{g.repeat} {self.queue.repeat}[/]")
        if self.queue.shuffle:
            parts.append(f"[$secondary]{g.shuffle} shuffle[/]")
        return " ".join(parts)

    # ------------------------------------------------------ track targeting

    def _target_song(self, deck: bool = False) -> Song | None:
        """Highlighted track in the focused list, else the deck's track."""
        if not deck:
            focused = self.focused
            if isinstance(focused, SongTable) and (song := focused.highlighted_song):
                return song
        return self._deck_song()

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        table = event.data_table
        if isinstance(table, QueueTable):
            row = table.highlighted_row
            if row and (entry := self.queue.select(int(row.key))):
                self._play_entry(entry)
        elif isinstance(table, ResultsTable):
            song = table.highlighted_song
            if song is not None:
                self._play_entry(self.queue.play_now(song))
        else:
            for view, (_, table_id, _) in LIST_VIEWS.items():
                if table.id == table_id.lstrip("#"):
                    self._play_list_from(view, table.cursor_row)

    @on(DataTable.RowHighlighted)
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = event.data_table
        if isinstance(table, QueueTable | PlaylistTable) and self._session_ready:
            self._schedule_queue_save()  # selected rows are part of the session
        for view, (_, table_id, _) in LIST_VIEWS.items():
            if table.id == table_id.lstrip("#"):
                tracks = self._list_tracks.get(view, [])
                index = event.cursor_row
                if not table.has_focus:  # a refresh, not the user moving
                    continue
                if 0 <= index < len(tracks) and not tracks[index].available:
                    if is_placeholder(tracks[index].song.id):
                        self._status(
                            "Not matched on YouTube yet: press M to review the "
                            "playlist's matches."
                        )
                    else:
                        reason = tracks[index].unavailable_reason
                        self._status(f"Unavailable last time: {reason}")

    @on(OptionList.OptionHighlighted, "#playlist-nav")
    def _on_playlist_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_playlist()
        if self._session_ready:
            self._schedule_queue_save()

    @on(Button.Pressed, ".transport")
    def _on_transport_pressed(self, event: Button.Pressed) -> None:
        self._on_transport(event.button.id or "")

    @on(Button.Pressed, "#party-toggle")
    def _on_party_pressed(self) -> None:
        self.action_toggle_party()

    @on(Button.Pressed, "#booth-shuffle")
    def _on_booth_shuffle(self) -> None:
        self.action_shuffle_playlist(from_booth=True)

    @on(Button.Pressed, "#pl-shuffle")
    def _on_playlist_shuffle(self) -> None:
        self.action_shuffle_playlist()

    @on(Button.Pressed, "#pl-import")
    def _on_playlist_import(self) -> None:
        self.action_import_playlist()

    @on(Button.Pressed, "#pl-play")
    def _on_playlist_play(self) -> None:
        self.action_playlist_play()

    @on(Timeline.Seek)
    def _on_seek(self, event: Timeline.Seek) -> None:
        self._on_timeline_seek(event.position)

    @on(Fader.Changed)
    def _on_fader_changed(self, event: Fader.Changed) -> None:
        self._on_fader(event.fader.id or "", event.value)

    @on(Toggle.Changed, "#toggle-norm")
    def _on_norm_changed(self, event: Toggle.Changed) -> None:
        if event.on != self._fx.normalize:
            self.action_toggle_normalize()

    # -------------------------------------------------------------- actions

    def action_toggle_playback(self) -> None:
        if self.playback.song is None:
            current = self.queue.current
            if current is not None:
                self._play_entry(current, self._restored_position)
                return
            entry = self.queue.advance(manual=True) if self.queue.upcoming else None
            if entry is not None:
                self._play_entry(entry)
            else:
                self._status(
                    "Nothing loaded. Press 2 or / to search, then enter on a track."
                )
            return
        self._control(self.player.toggle(), "Play/pause")

    def action_stop(self) -> None:
        self._control(self.player.stop(), "Stop")

    def action_next_track(self) -> None:
        entry = self.queue.advance(manual=True)
        if entry is None:
            self._status("End of queue.")
            return
        self._play_entry(entry)

    def action_previous_track(self) -> None:
        current = self.queue.current
        if current is not None and self.playback.position > RESTART_THRESHOLD:
            self._play_entry(current)  # restart this track
            return
        entry = self.queue.previous()
        if entry is not None:
            self._play_entry(entry)
        elif current is not None:
            self._play_entry(current)
        else:
            self._status("Start of queue.")

    def action_seek(self, direction: int) -> None:
        state = self.playback
        if state.song is None:
            return
        if not state.can_seek:
            self._status(
                "Seeking is not available for this track (live or unknown length)."
            )
            return
        base = self._seek_target if self._seek_target is not None else state.position
        self._seek_target = max(0.0, base + direction * SEEK_STEP)
        self._control(self.player.seek(self._seek_target), "Seek")
        self._schedule_queue_save()

    def action_cycle_repeat(self) -> None:
        mode = self.queue.cycle_repeat()
        self._save_pref("repeat", str(mode))
        labels = {
            RepeatMode.OFF: "Repeat off.",
            RepeatMode.ALL: "Repeat queue.",
            RepeatMode.ONE: "Repeat this track.",
        }
        self._status(labels[mode])
        self._refresh_queue_views()

    def action_toggle_shuffle(self) -> None:
        self.queue.set_shuffle(not self.queue.shuffle)
        enabled = self.queue.shuffle
        self._save_pref("shuffle", enabled)
        self._status(
            "Shuffle on: upcoming tracks shuffled." if enabled else "Shuffle off."
        )
        self._refresh_queue_views()

    def action_enqueue(self) -> None:
        song = self._target_song()
        if song is None:
            return
        entry = self.queue.add(song)
        if entry is None:
            self._status(f"{song.title} is already up next.")
        else:
            self._status(
                f"Queued {song.title} (#{len(self.queue.upcoming)} up next)",
                StatusLevel.SUCCESS,
            )
        self._refresh_queue_views()

    def action_play_next(self) -> None:
        song = self._target_song()
        if song is None:
            return
        self.queue.add_next(song)
        self._status(f"{song.title} plays next.", StatusLevel.SUCCESS)
        self._refresh_queue_views()

    def action_cycle_artwork(self) -> None:
        self.artwork_mode = self.artwork_mode.next()
        self.load_artwork(self._newest_song())
        self._refresh_meta()
        self._status(f"Artwork mode: {self.artwork_mode}")

    # ----------------------------------------------------------- queue view

    def _queue_table(self) -> QueueTable:
        focused = self.focused
        if isinstance(focused, QueueTable):
            return focused
        return self.query_one("#queue-table", QueueTable)

    def _queue_row(self) -> int | None:
        row = self._queue_table().highlighted_row
        return int(row.key) if row else None

    def action_queue_remove(self) -> None:
        uid = self._queue_row()
        if uid is None:
            return
        entry = self.queue.find(uid)
        if not self.queue.remove(uid):
            self._status("That's the playing track: press n to skip or x to stop.")
            return
        if entry is not None:
            self._status(f"Removed {entry.song.title} from the queue.")
        self._refresh_queue_views()

    def action_queue_move(self, delta: int) -> None:
        uid = self._queue_row()
        if uid is None:
            return
        table = self._queue_table()
        if self.queue.move(uid, delta):
            self._refresh_queue_views()
            table.select_key(str(uid))
        else:
            self._status("Only upcoming tracks can be moved, within the upcoming list.")

    def action_queue_clear(self) -> None:
        if len(self.queue.entries) <= (1 if self.queue.current else 0):
            self._status("The queue is already empty.")
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                removed = self.queue.clear()
                self._set_queue_source(None)
                self._status(
                    f"Cleared {removed} track{'s' if removed != 1 else ''}; "
                    "the playing track keeps playing."
                )
                self._refresh_queue_views()

        self.push_screen(
            ConfirmDialog("Clear the queue? (The playing track stays.)"), done
        )

    # --------------------------------------------- system media controls

    async def _start_media(self) -> None:
        if self.media is not None and await self.media.start():
            self._publish_now_playing()

    def _on_media_command(self, name: str, position: float | None) -> None:
        """A media key / Control Centre button: the same actions as the keys."""
        status = self.playback.status
        log.info("media command: %s", name)
        if name == "toggle":
            self.action_toggle_playback()
        elif name == "play":
            if status not in ACTIVE:
                self.action_toggle_playback()
        elif name == "pause":
            if status in ACTIVE:
                self._control(self.player.pause(), "Pause")
        elif name == "next":
            self.action_next_track()
        elif name == "previous":
            self.action_previous_track()
        elif name == "seek" and position is not None and self.playback.can_seek:
            self._seek_target = position
            self._control(self.player.seek(position), "Seek")

    def _now_playing_info(self) -> NowPlayingInfo | None:
        """What the system shows; nothing when no track is loaded or stopped."""
        state = self.playback
        song = state.song or self._deck_song()
        if song is None or state.status is PlaybackStatus.STOPPED:
            return None
        meta = self._display_meta(song)
        return NowPlayingInfo(
            title=meta.get("title", song.title),
            artist=meta.get("artist", song.artist),
            album=meta.get("album", ""),
            duration=float(state.duration or song.duration or 0),
            elapsed=round(float(state.position), 1),
            playing=state.status in ACTIVE,
            artwork=self._media_art.get(song.id),
        )

    def _publish_now_playing(self) -> None:
        if self.media is None:
            return
        self._media_published = time.monotonic()
        info = self._now_playing_info()
        self.media.publish(info)
        song = self.playback.song or self._deck_song()
        if info is not None and song is not None and song.id not in self._media_art:
            self.media_artwork(song)

    @work(group="media-art", exclusive=True, exit_on_error=False)
    async def media_artwork(self, song: Song) -> None:
        """Save the cover to a file for Control Centre (it reads a path)."""
        if not song.thumbnail_url:
            return
        try:
            data = await self.artwork.image_data(song.thumbnail_url)
        except Exception:
            return
        folder = cache_dir() / "nowplaying"
        path = (
            folder
            / f"{hashlib.sha256(song.thumbnail_url.encode()).hexdigest()[:24]}.img"
        )

        def write() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)

        with contextlib.suppress(OSError):
            await asyncio.to_thread(write)
            self._media_art[song.id] = str(path)
            self._publish_now_playing()

    # ----------------------------------------------------- session saving

    def _schedule_queue_save(self) -> None:
        if self._queue_timer is not None:
            self._queue_timer.stop()
        self._queue_timer = self.set_timer(QUEUE_SAVE_DELAY, self._save_queue)

    def _ui_snapshot(self) -> dict[str, Any]:
        """View and selections; the last known values once widgets are gone."""
        ui = dict(self._last_ui)
        try:
            ui["view"] = self._active_view()
            queue_table = self.query_one("#queue-table", QueueTable)
            ui["queue_cursor"] = (
                queue_table.cursor_row if queue_table.row_count else None
            )
            ui["playlist_id"] = self.query_one(PlaylistNav).playlist_id
            track = self.query_one(PlaylistTable).highlighted_song
            ui["playlist_track"] = track.id if track else None
        except Exception:  # shutting down: widgets are being removed
            return ui
        self._last_ui = ui
        return ui

    def _session_snapshot(self) -> tuple[list[Song], int | None, float, dict[str, Any]]:
        songs = [entry.song for entry in self.queue.entries]
        current = self.queue.current
        state = self.playback
        if state.song is not None and state.status in (*ACTIVE, PlaybackStatus.PAUSED):
            position = state.position
        else:
            position = self._restored_position
        source = self._queue_source
        session = {
            "version": SESSION_VERSION,
            "song_id": current.song.id if current else None,
            "status": str(state.status) if state.song is not None else "idle",
            "duration": state.duration
            or (float(current.song.duration) if current else None),
            "local": state.local,
            "queue_source": (
                {"id": source["id"], "shuffled": source["shuffled"]} if source else None
            ),
            **self._ui_snapshot(),
        }
        return songs, self.queue.current_index, position, session

    def _save_queue(self) -> None:
        """Write the session if it changed (queue rows only when they did).

        SQLite commits each save atomically, so an interrupted save leaves the
        previous snapshot intact.
        """
        if not self._session_ready:
            return  # never overwrite the saved session with a half-built one
        songs, current, position, session = self._session_snapshot()
        queue_key = (tuple(song.id for song in songs), current)
        session_key = (round(position, 1), json.dumps(session, sort_keys=True))
        if queue_key != self._saved_queue_key:

            def save_all() -> bool:
                self.library.save_queue(songs, current, position, session)
                return True

            if self._safe_load(save_all, False):
                self._saved_queue_key = queue_key
                self._saved_session_key = session_key
        elif session_key != self._saved_session_key:

            def save_session() -> bool:
                self.library.save_session(position, session)
                return True

            if self._safe_load(save_session, False):
                self._saved_session_key = session_key

    def save_session(self) -> None:
        """Save now (quit, signals, and the crash handler in ``__main__``)."""
        if self._queue_timer is not None:
            with contextlib.suppress(Exception):
                self._queue_timer.stop()
        self._save_queue()

    # -------------------------------------------------------------- refresh

    def _reload_library_sets(self) -> None:
        self._favorites = self._safe_db(self.library.favorite_ids) or set()
        self._unavailable = self._safe_db(self.library.unavailable_ids) or set()

    def _refresh_marks(self) -> None:
        state = self.playback
        idle = state.status in (PlaybackStatus.IDLE, PlaybackStatus.STOPPED)
        playing_id = state.song.id if state.song and not idle else None
        requested = self._requested
        playing_key = (
            str(requested.uid)
            if playing_id and requested and requested.song.id == playing_id
            else None
        )
        marks = Marks(
            playing_id=playing_id,
            playing_key=playing_key,
            favorites=frozenset(self._favorites),
            queued=frozenset(self.queue.upcoming_ids()),
            unavailable=frozenset(self._unavailable),
            downloaded=frozenset(set(self._download_paths) - self._missing_downloads),
        )
        for table in self.query(SongTable):
            table.refresh_marks(marks)

    def _refresh_queue_views(self) -> None:
        upcoming = self.queue.upcoming
        self.query_one(QueuePanel).update_queue([e.song for e in upcoming])
        history = {e.uid for e in self.queue.history}
        rows = [
            Row(str(e.uid), e.song, played=e.uid in history) for e in self.queue.entries
        ]
        flags = self._flags_markup()
        for table in self.query(QueueTable):
            table.set_rows(rows, keep_cursor=True)
            table.refresh_marks(Marks())
            table.border_subtitle = flags
        empty = self.query_one("#queue-empty", Static)
        empty.update(
            "[$text-muted]The queue is empty. On any track press [b $primary]a[/] to "
            "queue it, [b $primary]A[/] to play it next, or [b $primary]enter[/] "
            "to play it now.[/]"
        )
        empty.set_class(not self.queue.entries, "-visible")
        tabs = self.query_one(ViewTabs)
        tabs.counts = {"queue-view": len(upcoming)}
        tabs.flags = flags
        self._refresh_meta()
        self._sync_mixer()
        self._render_playback(self.playback)
        self._schedule_queue_save()

    def _refresh_meta(self) -> None:
        sep = f" {self.glyphs.separator} "
        self.query_one(StatusBar).set_meta(
            sep.join(
                [
                    f"{self._result_count} results",
                    f"{len(self.queue.upcoming)} up next",
                    f"art {self.artwork_mode}",
                ]
            )
        )
