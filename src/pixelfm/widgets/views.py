"""Navigation tabs, the playlist list, and the settings list."""

from __future__ import annotations

from collections.abc import Sequence

from textual.binding import Binding
from textual.markup import escape
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import OptionList
from textual.widgets.option_list import Option

#: (view id, tab label, header label). Number keys follow this order.
VIEWS: tuple[tuple[str, str, str], ...] = (
    ("booth-view", "BOOTH", "DJ BOOTH"),
    ("search-view", "SEARCH", "SEARCH"),
    ("queue-view", "QUEUE", "QUEUE"),
    ("playlists-view", "PLAYLISTS", "PLAYLISTS"),
    ("liked-view", "LIKED", "LIKED SONGS"),
    ("downloads-view", "DOWNLOADS", "DOWNLOADS"),
    ("recent-view", "RECENT", "RECENTLY PLAYED"),
    ("settings-view", "SETTINGS", "SETTINGS"),
)
VIEW_LABELS = {view: header for view, _, header in VIEWS}
PLAYLIST_PREFIX = "playlist:"


class ViewTabs(Widget):
    """One-line tab strip ``1 BOOTH  2 SEARCH ...`` plus mode flags."""

    active: reactive[str] = reactive("booth-view")
    counts: reactive[dict[str, int]] = reactive(dict, always_update=True)
    flags: reactive[str] = reactive("")
    compact: reactive[bool] = reactive(False)

    def render(self) -> str:
        parts = []
        for number, (view, label, _) in enumerate(VIEWS, start=1):
            if self.compact and view != self.active:
                label = label[:3]
            count = self.counts.get(view)
            suffix = f" {count}" if count else ""
            if view == self.active:
                parts.append(f"[b reverse $primary] {number} {label}{suffix} [/]")
            else:
                parts.append(
                    f"[$text-muted] [b $secondary]{number}[/] {label}{suffix} [/]"
                )
        flags = f"  {self.flags}" if self.flags else ""
        return "".join(parts) + flags


def section_id_for_playlist(playlist_id: int) -> str:
    return f"{PLAYLIST_PREFIX}{playlist_id}"


def playlist_id_for(section: str | None) -> int | None:
    if section and section.startswith(PLAYLIST_PREFIX):
        return int(section.removeprefix(PLAYLIST_PREFIX))
    return None


class PlaylistNav(OptionList):
    """The user's playlists. Playlist-level actions live here."""

    BINDINGS = [
        Binding("enter", "app.playlist_play", "Play all"),
        Binding("Z", "app.shuffle_playlist", "Shuffle"),
        Binding("A", "app.playlist_append", "Append"),
        Binding("l,right", "app.focus_playlist_tracks", "Tracks", show=False),
        Binding("c", "app.playlist_create", "New"),
        Binding("e", "app.playlist_rename", "Rename"),
        Binding("d,delete", "app.playlist_delete", "Delete"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def set_playlists(self, playlists: Sequence[tuple[int, str, int]]) -> None:
        """Rebuild the list, keeping the highlighted playlist if it still exists."""
        current = self.highlighted_section
        options = [
            Option(
                f"{escape(name)} [$text-muted]{count}[/]",
                id=section_id_for_playlist(pid),
            )
            for pid, name, count in playlists
        ]
        if not options:
            options.append(
                Option(
                    "[$text-muted]no playlists · c to create[/]",
                    id="none",
                    disabled=True,
                )
            )
        self.clear_options()
        self.add_options(options)
        if current:
            self.select_section(current)
        elif playlists:
            self.highlighted = 0

    @property
    def highlighted_section(self) -> str | None:
        if self.highlighted is None:
            return None
        option_id = self.get_option_at_index(self.highlighted).id
        return (
            option_id if option_id and option_id.startswith(PLAYLIST_PREFIX) else None
        )

    @property
    def playlist_id(self) -> int | None:
        return playlist_id_for(self.highlighted_section)

    def select_section(self, section: str) -> None:
        try:
            self.highlighted = self.get_option_index(section)
        except Exception:
            self.highlighted = 0 if self.option_count else None


class SettingsList(OptionList):
    """Settings rows. ``enter``/``→`` cycles forward, ``←`` backward."""

    BINDINGS = [
        Binding("enter,right,l", "app.setting_change(1)", "Change"),
        Binding("left,h", "app.setting_change(-1)", "Back", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def set_rows(self, rows: Sequence[tuple[str, str, str, bool]]) -> None:
        """``rows``: (setting id, label, value markup, editable)."""
        current = self.highlighted
        self.clear_options()
        self.add_options(
            [
                Option(
                    f"[b]{escape(label):<22}[/] {value}"
                    + ("" if editable else "  [$text-muted](read only)[/]"),
                    id=key,
                    disabled=not editable,
                )
                for key, label, value, editable in rows
            ]
        )
        if current is not None and current < self.option_count:
            self.highlighted = current

    @property
    def setting(self) -> str | None:
        if self.highlighted is None:
            return None
        return self.get_option_at_index(self.highlighted).id
