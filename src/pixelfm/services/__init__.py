"""Data providers and engines used by the UI."""

from pixelfm.services.artwork import (
    ArtworkMode,
    ArtworkResult,
    ArtworkService,
    ColorDepth,
    TerminalArt,
)
from pixelfm.services.ffplay import FFplayPlayer
from pixelfm.services.playback import Player, SimulatedPlayer
from pixelfm.services.search import (
    CachedSearchProvider,
    MockSearchProvider,
    SearchError,
    SearchProvider,
)
from pixelfm.services.streams import StreamError, StreamInfo, StreamResolver
from pixelfm.services.youtube import YouTubeSearchProvider

__all__ = [
    "ArtworkMode",
    "ArtworkResult",
    "ArtworkService",
    "CachedSearchProvider",
    "ColorDepth",
    "FFplayPlayer",
    "MockSearchProvider",
    "Player",
    "SearchError",
    "SearchProvider",
    "SimulatedPlayer",
    "StreamError",
    "StreamInfo",
    "StreamResolver",
    "TerminalArt",
    "YouTubeSearchProvider",
]
