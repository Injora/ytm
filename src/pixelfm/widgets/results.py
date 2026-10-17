"""Track tables: search results, the queue, and library lists."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.widgets import DataTable

from pixelfm.models import Song
from pixelfm.styles import Glyphs, glyphs_for

_INDEX_WIDTH = 3
_MARKS_WIDTH = 3
_TIME_WIDTH = 7


@dataclass(frozen=True, slots=True)
class Row:
    """One table row. ``key`` must be unique within the table."""

    key: str
    song: Song
    played: bool = False  # dimmed (queue history)
    extra: str = ""  # optional extra column (plays, size, status)


@dataclass(frozen=True, slots=True)
class Marks:
    """Everything that decides per-row markers, so tables refresh only on change."""

    playing_id: str | None = None
    playing_key: str | None = None
    favorites: frozenset[str] = field(default_factory=frozenset)
    queued: frozenset[str] = field(default_factory=frozenset)
    unavailable: frozenset[str] = field(default_factory=frozenset)
    downloaded: frozenset[str] = field(default_factory=frozenset)


class SongTable(DataTable[Text]):
    """Row-cursor table of songs with vim-style navigation.

    Markers: the number column shows the play glyph on the playing track; the
    narrow marks column shows favorite, queued, and unavailable flags. Column
    widths follow the table width so long titles are truncated, not scrolled.
    """

    BINDINGS = [
        Binding("enter", "select_cursor", "Play"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    #: Queue tables identify the playing row by entry key, others by song id.
    match_playing_by_key = False

    #: Optional extra column: (heading, width).
    EXTRA: tuple[str, int] | None = None
    #: Narrow tables (the booth queue) can drop these columns.
    SHOW_ARTIST = True
    SHOW_MARKS = True

    def __init__(
        self, glyphs: Glyphs | None = None, *, id: str | None = None, title: str = ""
    ) -> None:
        super().__init__(
            id=id,
            cursor_type="row",
            zebra_stripes=False,
            show_row_labels=False,
            cell_padding=1,
        )
        self._glyphs = glyphs or glyphs_for(True)
        self._rows: list[Row] = []
        self._marks = Marks()
        self._layout_width = 0
        self.border_title = title

    # ------------------------------------------------------------ access

    @property
    def rows_data(self) -> list[Row]:
        return list(self._rows)

    @property
    def songs(self) -> list[Song]:
        return [row.song for row in self._rows]

    @property
    def highlighted_row(self) -> Row | None:
        if not self._rows or not 0 <= self.cursor_row < len(self._rows):
            return None
        return self._rows[self.cursor_row]

    @property
    def highlighted_song(self) -> Song | None:
        row = self.highlighted_row
        return row.song if row else None

    # ----------------------------------------------------------- updates

    def on_resize(self, event: events.Resize) -> None:
        if event.size.width != self._layout_width:
            self._rebuild(keep_cursor=True)

    def set_songs(self, songs: Sequence[Song]) -> None:
        """Rows keyed by song id (search results, favorites, recent)."""
        self.set_rows([Row(song.id, song) for song in songs])

    def set_rows(self, rows: Sequence[Row], *, keep_cursor: bool = False) -> None:
        self._rows = list(rows)
        self._rebuild(keep_cursor=keep_cursor)

    def select_key(self, key: str) -> None:
        for index, row in enumerate(self._rows):
            if row.key == key:
                self.move_cursor(row=index)
                return

    def refresh_marks(self, marks: Marks) -> None:
        """Update marker cells if anything relevant changed."""
        if marks == self._marks:
            return
        self._marks = marks
        for number, row in enumerate(self._rows, start=1):
            self.update_cell(row.key, "idx", self._index_cell(row, number))
            if self.SHOW_MARKS:
                self.update_cell(row.key, "marks", self._marks_cell(row))

    # ---------------------------------------------------------- internals

    def _is_playing(self, row: Row) -> bool:
        if self.match_playing_by_key:
            return row.key == self._marks.playing_key
        return row.song.id == self._marks.playing_id

    def _index_cell(self, row: Row, number: int) -> Text:
        if self._is_playing(row):
            return Text(self._glyphs.playing, style="bold", justify="right")
        return Text(str(number), style="dim", justify="right")

    def _marks_cell(self, row: Row) -> Text:
        theme = self.app.current_theme
        text = Text(no_wrap=True, overflow="crop")
        song_id = row.song.id
        if song_id in self._marks.favorites:
            text.append(self._glyphs.favorite, style=theme.secondary or "magenta")
        if song_id in self._marks.downloaded:
            text.append(self._glyphs.download, style=theme.primary or "cyan")
        if song_id in self._marks.unavailable:
            text.append(self._glyphs.error, style=theme.error or "red")
        elif song_id in self._marks.queued and not self.match_playing_by_key:
            text.append(self._glyphs.queued, style=theme.accent or "cyan")
        return text

    def _column_widths(self) -> tuple[int, int]:
        columns = 3 + bool(self.EXTRA) + self.SHOW_ARTIST + self.SHOW_MARKS
        padding = 2 * self.cell_padding * columns
        extra = self.EXTRA[1] if self.EXTRA else 0
        marks = _MARKS_WIDTH if self.SHOW_MARKS else 0
        available = self.scrollable_content_region.width - padding - 1 - extra
        flexible = max(8, available - _INDEX_WIDTH - marks - _TIME_WIDTH)
        if not self.SHOW_ARTIST:
            return flexible, 0
        artist = min(28, max(8, flexible * 2 // 5))
        return max(8, flexible - artist), artist

    def _rebuild(self, keep_cursor: bool) -> None:
        cursor = self.cursor_row if keep_cursor else 0
        self._layout_width = self.size.width
        title_width, artist_width = self._column_widths()
        self.clear(columns=True)
        self.add_column("#", key="idx", width=_INDEX_WIDTH)
        if self.SHOW_MARKS:
            self.add_column("", key="marks", width=_MARKS_WIDTH)
        self.add_column("TITLE", key="title", width=title_width)
        if self.SHOW_ARTIST:
            self.add_column("ARTIST", key="artist", width=artist_width)
        self.add_column(Text("TIME", justify="right"), key="time", width=_TIME_WIDTH)
        if self.EXTRA:
            heading, width = self.EXTRA
            self.add_column(Text(heading, justify="right"), key="extra", width=width)
        for number, row in enumerate(self._rows, start=1):
            dim = "dim" if row.played else ""
            cells = [self._index_cell(row, number)]
            if self.SHOW_MARKS:
                cells.append(self._marks_cell(row))
            cells.append(
                Text(row.song.title, style=dim, overflow="ellipsis", no_wrap=True)
            )
            if self.SHOW_ARTIST:
                cells.append(
                    Text(
                        row.song.artist, style="dim", overflow="ellipsis", no_wrap=True
                    )
                )
            cells.append(Text(row.song.duration_label, justify="right", style="dim"))
            if self.EXTRA:
                cells.append(
                    Text(row.extra, justify="right", style="dim", no_wrap=True)
                )
            self.add_row(*cells, key=row.key)
        if self._rows:
            self.move_cursor(row=min(cursor, len(self._rows) - 1))


class ResultsTable(SongTable):
    """Search results."""


class QueueTable(SongTable):
    """The full queue, including played history (dimmed)."""

    match_playing_by_key = True

    BINDINGS = [
        Binding("enter", "select_cursor", "Play"),
        Binding("d,delete", "app.queue_remove", "Remove"),
        Binding("K,shift+up", "app.queue_move(-1)", "Up"),
        Binding("J,shift+down", "app.queue_move(1)", "Down"),
        Binding("C", "app.queue_clear", "Clear"),
    ]


class BoothQueueTable(QueueTable):
    """The narrow queue panel in the DJ booth (same actions as the Queue view)."""

    SHOW_ARTIST = False
    SHOW_MARKS = False


class PlaylistTable(SongTable):
    """Tracks of the highlighted playlist."""

    BINDINGS = [
        Binding("enter", "select_cursor", "Play"),
        Binding("Z", "app.shuffle_playlist", "Shuffle"),
        Binding("d,delete", "app.playlist_remove_track", "Remove"),
        Binding("K,shift+up", "app.playlist_move(-1)", "Up"),
        Binding("J,shift+down", "app.playlist_move(1)", "Down"),
        Binding("h,left", "app.focus_playlist_nav", "Playlists", show=False),
    ]


class LikedTable(SongTable):
    """Liked songs."""

    EXTRA = ("PLAYS", 6)
    BINDINGS = [
        Binding("enter", "select_cursor", "Play"),
        Binding("d,delete", "app.liked_remove", "Unlike"),
        Binding("o", "app.liked_sort", "Sort"),
        Binding("A", "app.list_append", "Append all"),
    ]


class DownloadsTable(SongTable):
    """Completed downloads, with file status."""

    EXTRA = ("FILE", 10)
    BINDINGS = [
        Binding("enter", "select_cursor", "Play"),
        Binding("d,delete", "app.download_delete", "Delete"),
        Binding("L", "app.download_relink", "Relink"),
        Binding("X", "app.download_clean_missing", "Clean missing"),
        Binding("A", "app.list_append", "Append all", show=False),
    ]


class RecentTable(SongTable):
    """Recently played, newest first, with play counts."""

    EXTRA = ("PLAYS", 6)
    BINDINGS = [
        Binding("enter", "select_cursor", "Play"),
        Binding("A", "app.list_append", "Append all"),
    ]
