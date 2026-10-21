"""Reusable Textual widgets."""

from pixelfm.widgets.album_art import AlbumArt
from pixelfm.widgets.booth import (
    BoothHeader,
    Fader,
    LightBar,
    LyricsView,
    Spectrum,
    StereoMeter,
    Timeline,
    Toggle,
    Transport,
)
from pixelfm.widgets.dialogs import (
    NEW_PLAYLIST,
    ConfirmDialog,
    HelpScreen,
    PlaylistPicker,
    TextPrompt,
    WelcomeScreen,
)
from pixelfm.widgets.now_playing import LevelMeter, NowPlaying, ProgressLine
from pixelfm.widgets.queue import QueuePanel
from pixelfm.widgets.results import (
    BoothQueueTable,
    DownloadsTable,
    LikedTable,
    Marks,
    PlaylistTable,
    QueueTable,
    RecentTable,
    ResultsTable,
    Row,
    SongTable,
)
from pixelfm.widgets.status import StatusBar, StatusLevel
from pixelfm.widgets.views import PlaylistNav, SettingsList, ViewTabs

__all__ = [
    "NEW_PLAYLIST",
    "AlbumArt",
    "BoothHeader",
    "BoothQueueTable",
    "ConfirmDialog",
    "DownloadsTable",
    "Fader",
    "HelpScreen",
    "LevelMeter",
    "LightBar",
    "LikedTable",
    "LyricsView",
    "Marks",
    "NowPlaying",
    "PlaylistNav",
    "PlaylistPicker",
    "PlaylistTable",
    "ProgressLine",
    "QueuePanel",
    "QueueTable",
    "RecentTable",
    "ResultsTable",
    "Row",
    "SettingsList",
    "SongTable",
    "Spectrum",
    "StatusBar",
    "StatusLevel",
    "StereoMeter",
    "TextPrompt",
    "Timeline",
    "Toggle",
    "Transport",
    "ViewTabs",
    "WelcomeScreen",
]
