"""Library views: Liked Songs, Recently Played, and Playlists."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from textual.markup import escape
from textual.widgets import Input, Static

from pixelfm.models import format_duration
from pixelfm.services.library import LIKED_ORDERS, LibraryError, LibraryTrack
from pixelfm.shuffle import shuffle_songs
from pixelfm.state import ACTIVE, PlaybackStatus
from pixelfm.widgets import (
    NEW_PLAYLIST,
    ConfirmDialog,
    LikedTable,
    Marks,
    PlaylistNav,
    PlaylistPicker,
    PlaylistTable,
    RecentTable,
    Row,
    SongTable,
    StatusBar,
    StatusLevel,
    TextPrompt,
)
from pixelfm.widgets.views import section_id_for_playlist

if TYPE_CHECKING:
    from pixelfm.models import Song

#: View id -> (filter input id, table id, empty-state id).
LIST_VIEWS = {
    "liked-view": ("#liked-filter", "#liked-table", "#liked-empty"),
    "recent-view": ("#recent-filter", "#recent-table", "#recent-empty"),
    "playlists-view": ("#playlist-filter", "#playlist-table", "#playlist-empty"),
    "downloads-view": ("#downloads-filter", "#downloads-table", "#downloads-empty"),
}


def ago(timestamp: float | None, now: float | None = None) -> str:
    if not timestamp:
        return ""
    seconds = max(0, int((now or time.time()) - timestamp))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def matches(song: Song, needle: str) -> bool:
    needle = needle.casefold().strip()
    return (
        not needle
        or needle in song.title.casefold()
        or needle in song.artist.casefold()
    )


class LibraryMixin:
    """Mixed into :class:`~pixelfm.app.PixelFMApp`."""

    # ---------------------------------------------------------- generic

    def _filter_text(self, view: str) -> str:
        return self.query_one(LIST_VIEWS[view][0], Input).value

    def _show_list(
        self,
        view: str,
        tracks: Sequence[LibraryTrack],
        extra: Callable[[LibraryTrack], str],
        empty: str,
    ) -> None:
        """Filter ``tracks`` and show them in the view's table."""
        _, table_id, empty_id = LIST_VIEWS[view]
        needle = self._filter_text(view)
        shown = [t for t in tracks if matches(t.song, needle)]
        self._list_tracks[view] = shown
        table = self.query_one(table_id, SongTable)
        table.set_rows(
            [
                Row(f"{i}:{t.song.id}", t.song, extra=extra(t))
                for i, t in enumerate(shown)
            ],
            keep_cursor=True,
        )
        table.refresh_marks(Marks())
        self._refresh_marks()
        message = (
            empty if not tracks else f"No tracks match “{needle}”. Press esc to clear."
        )
        widget = self.query_one(empty_id, Static)
        widget.update(f"[$text-muted]{message}[/]")
        widget.set_class(not shown, "-visible")

    def _refresh_view(self, view: str) -> None:
        refreshers = {
            "liked-view": self._refresh_liked,
            "recent-view": self._refresh_recent,
            "playlists-view": self._refresh_playlists,
            "downloads-view": self._refresh_downloads,
            "settings-view": self._refresh_settings,
        }
        if refresh := refreshers.get(view):
            refresh()

    def action_focus_filter(self) -> None:
        view = self._active_view()
        if view in LIST_VIEWS:
            self.query_one(LIST_VIEWS[view][0], Input).focus()
        elif view == "search-view":
            self.action_focus_search()

    def _clear_filter_or_back(self) -> bool:
        """``esc`` inside a filter box clears it first. Returns True if handled."""
        focused = self.focused
        if isinstance(focused, Input) and focused.id and focused.id.endswith("-filter"):
            if focused.value:
                focused.value = ""
            else:
                self.action_focus_main()
            return True
        return False

    def _play_list_from(self, view: str, index: int) -> None:
        tracks = self._list_tracks.get(view, [])
        if not tracks:
            return
        index = max(0, min(index, len(tracks) - 1))
        # Skip tracks known to be unavailable, but always try the one picked.
        chosen = [t for i, t in enumerate(tracks) if t.available or i == index]
        start = chosen.index(tracks[index])
        entry = self.queue.play_many([t.song for t in chosen], start)
        if entry is not None:
            if view == "playlists-view":
                playlist_id = self.query_one(PlaylistNav).playlist_id
                playlist = self._safe_db(lambda: self.library.playlist(playlist_id))
                self._set_queue_source(
                    (playlist.id, playlist.name) if playlist else None
                )
            else:
                self._set_queue_source(None)
            self._play_entry(entry)

    # ------------------------------------------------------ queue source

    def _set_queue_source(
        self,
        playlist: tuple[int, str] | None,
        *,
        shuffled: bool = False,
        note: str = "",
    ) -> None:
        """Remember which playlist the queue was filled from (for the booth's
        SHUFFLE button and the saved session)."""
        if playlist is None:
            self._queue_source = None
        else:
            self._queue_source = {
                "id": playlist[0],
                "name": playlist[1],
                "shuffled": shuffled,
                "note": note,
            }
        self._render_queue_source()

    def _render_queue_source(self) -> None:
        source = self._queue_source
        self.query_one("#booth-source").set_class(source is not None, "-visible")
        if source is None:
            return
        # Narrow panel: name and state first; the note is cut off first.
        label = f"[b]{escape(source['name'])}[/]"
        if source["shuffled"]:
            label += f" [b $secondary]{self.glyphs.shuffle} SHUFFLED[/]"
        if source.get("note"):
            label += (
                f" [$text-muted]{self.glyphs.separator} {escape(source['note'])}[/]"
            )
        self.query_one("#source-label", Static).update(label)

    # ----------------------------------------------------- shuffle playlist

    def action_shuffle_playlist(self, from_booth: bool = False) -> None:
        """Z / ⇄ SHUFFLE: play a playlist once in a fresh random order.

        In the Playlists view this is the highlighted playlist; elsewhere (and
        from the booth button) the playlist the queue was last filled from.
        The saved playlist and the global shuffle toggle are never changed.
        """
        playlist_id = None
        if not from_booth and self._active_view() == "playlists-view":
            playlist_id = self.query_one(PlaylistNav).playlist_id
            if playlist_id is None:
                self.query_one(StatusBar).show(
                    "No playlist highlighted. Press c to create one."
                )
                return
        elif self._queue_source is not None:
            playlist_id = self._queue_source["id"]
        if playlist_id is None:
            self.query_one(StatusBar).show(
                "Nothing to shuffle yet: open Playlists (4), highlight one, "
                "then press Z or ⇄ SHUFFLE."
            )
            return
        self._shuffle_playlist(playlist_id)

    def _shuffle_playlist(self, playlist_id: int) -> None:
        status = self.query_one(StatusBar)
        try:
            playlist = self.library.playlist(playlist_id)
            tracks = self.library.playlist_tracks(playlist_id)
        except LibraryError as exc:
            status.show(str(exc), StatusLevel.ERROR)
            if self._queue_source and self._queue_source["id"] == playlist_id:
                self._set_queue_source(None)
            return
        name = playlist.name
        if not tracks:
            status.show(
                f"“{name}” is empty, so there is nothing to shuffle. "
                "Highlight a track anywhere and press p to add it."
            )
            return
        playable = [t for t in tracks if t.available]
        skipped = len(tracks) - len(playable)
        if not playable:
            status.show(
                f"Can't shuffle “{name}”: all {plural(len(tracks), 'track')} were "
                "unavailable last time. Open it (4) to see why.",
                StatusLevel.ERROR,
            )
            return
        state = self.playback
        busy = state.status in (*ACTIVE, PlaybackStatus.PAUSED)
        order = shuffle_songs(
            [t.song for t in playable],
            self._shuffle_rng,
            avoid_first=state.song.id if state.song is not None and busy else None,
            previous=self._last_shuffle.get(playlist_id, ()),
        )
        self._last_shuffle[playlist_id] = [s.id for s in order]
        entry = self.queue.play_many(order)
        if entry is None:
            return
        notes = [plural(len(order), "track")]
        if skipped:
            notes.append(f"{skipped} unavailable skipped")
        self._set_queue_source(
            (playlist.id, name), shuffled=len(order) > 1, note=" · ".join(notes)
        )
        skip_note = (
            f" Skipped {plural(skipped, 'unavailable track')}." if skipped else ""
        )
        if len(order) == 1:
            message = f"“{name}” has one playable track: nothing to shuffle.{skip_note}"
        else:
            message = (
                f"Shuffled “{name}”: {len(order)} tracks in a new random order; "
                f"the saved playlist is unchanged.{skip_note}"
            )
        status.show(message, StatusLevel.SUCCESS)
        # Repeated with "Now playing ..." so the result stays readable.
        self._play_note = (entry.uid, message)
        self._play_entry(entry)

    def action_list_append(self) -> None:
        view = self._active_view()
        tracks = self._list_tracks.get(view, [])
        added = self.queue.extend(t.song for t in tracks if t.available)
        self.query_one(StatusBar).show(
            f"Added {added} track{'s' if added != 1 else ''} to the queue."
            if added
            else "Nothing new to add to the queue.",
            StatusLevel.SUCCESS if added else StatusLevel.INFO,
        )
        self._refresh_queue_views()

    # ------------------------------------------------------------ liked

    def _refresh_liked(self) -> None:
        tracks = self._safe_db(lambda: self.library.favorites(self._liked_order)) or []
        self.query_one("#liked-info", Static).update(
            f"[b $secondary]{self.glyphs.favorite} LIKED SONGS[/]  "
            f"[$text-muted]{plural(len(tracks), 'track')}"
            f" · sorted by {self._liked_order} (o) · ctrl+f filter · enter play · "
            "d unlike · A append[/]"
        )
        self._show_list(
            "liked-view",
            tracks,
            lambda t: str(t.play_count or ""),
            "No liked songs yet. Press f on any track (or ♡ LIKE in the booth).",
        )

    def action_liked_sort(self) -> None:
        orders = list(LIKED_ORDERS)
        self._liked_order = orders[(orders.index(self._liked_order) + 1) % len(orders)]
        self._save_pref("liked_order", self._liked_order)
        self._refresh_liked()
        self.query_one(StatusBar).show(f"Liked songs sorted by {self._liked_order}.")

    def action_liked_remove(self) -> None:
        song = self.query_one(LikedTable).highlighted_song
        if song is None:
            return
        if self._safe_db(lambda: self.library.remove_favorite(song.id)):
            self._favorites.discard(song.id)
            downloaded = song.id in self._download_paths
            note = " (the downloaded file is kept)" if downloaded else ""
            self.query_one(StatusBar).show(f"Unliked {song.title}{note}.")
        self._refresh_liked()
        self._render_playback(self.playback)

    # ----------------------------------------------------------- recent

    def _refresh_recent(self) -> None:
        tracks = self._safe_db(lambda: self.library.recent(200)) or []
        self.query_one("#recent-info", Static).update(
            f"[b $primary]RECENTLY PLAYED[/]  "
            f"[$text-muted]{plural(len(tracks), 'track')} · newest first "
            "· PLAYS = times started · enter play · A append[/]"
        )
        now = time.time()
        self._show_list(
            "recent-view",
            tracks,
            lambda t: f"{t.play_count}×" if t.play_count else ago(t.timestamp, now),
            "Nothing played yet. Tracks appear here once audio actually starts.",
        )

    # -------------------------------------------------------- playlists

    def _refresh_playlists(self) -> None:
        playlists = self._safe_db(
            lambda: [(p.id, p.name, p.count) for p in self.library.playlists()]
        )
        if playlists is None:
            return
        nav = self.query_one(PlaylistNav)
        nav.set_playlists(playlists)
        self._show_playlist()

    def _show_playlist(self) -> None:
        nav = self.query_one(PlaylistNav)
        playlist_id = nav.playlist_id
        table = self.query_one(PlaylistTable)
        heading = self.query_one("#playlist-heading", Static)
        if playlist_id is None:
            table.border_title = "playlist"
            heading.update("[$text-muted]no playlist selected[/]")
            self._show_list(
                "playlists-view",
                [],
                lambda t: "",
                "No playlists yet. Press c here (or p on any track) to create one.",
            )
            return
        try:
            playlist = self.library.playlist(playlist_id)
            tracks = self.library.playlist_tracks(playlist_id)
        except LibraryError:
            tracks, playlist = [], None
        table.border_title = f"playlist: {playlist.name}" if playlist else "playlist"
        total = sum(t.song.duration for t in tracks)
        imported = self._import_heading(playlist.id) if playlist else ""
        heading.update(
            f"[b $primary]{escape(playlist.name)}[/]  [$text-muted]"
            f"{plural(len(tracks), 'track')}"
            + (f" · {format_duration(total)}" if total and not imported else "")
            + (f" · {imported}" if imported else " · Z shuffle · enter play")
            + "[/]"
            if playlist
            else ""
        )
        self._show_list(
            "playlists-view",
            tracks,
            lambda t: "",
            "This playlist is empty. Highlight a track anywhere and press p to add it.",
        )

    def action_focus_playlist_nav(self) -> None:
        self.query_one(PlaylistNav).focus()

    def action_focus_playlist_tracks(self) -> None:
        self.query_one(PlaylistTable).focus()

    def action_playlist_play(self) -> None:
        tracks = self._list_tracks.get("playlists-view", [])
        if not tracks:
            self.query_one(StatusBar).show("Nothing to play in this playlist.")
            return
        self._play_list_from(
            "playlists-view", next((i for i, t in enumerate(tracks) if t.available), 0)
        )

    def action_playlist_append(self) -> None:
        self.action_list_append()

    def action_playlist_remove_track(self) -> None:
        playlist_id = self.query_one(PlaylistNav).playlist_id
        song = self.query_one(PlaylistTable).highlighted_song
        if playlist_id is None or song is None:
            return
        if self._safe_db(
            lambda: self.library.remove_from_playlist(playlist_id, song.id)
        ):
            self.query_one(StatusBar).show(
                f"Removed {song.title} from the playlist "
                "(likes and downloads are unaffected)."
            )
        self._refresh_playlists()

    def action_playlist_move(self, delta: int) -> None:
        playlist_id = self.query_one(PlaylistNav).playlist_id
        table = self.query_one(PlaylistTable)
        song = table.highlighted_song
        if playlist_id is None or song is None:
            return
        if self._filter_text("playlists-view"):
            self.query_one(StatusBar).show("Clear the filter (esc) before reordering.")
            return
        new_index = self._safe_db(
            lambda: self.library.move_in_playlist(playlist_id, song.id, delta)
        )
        if new_index is not None:
            self._show_playlist()
            table.move_cursor(row=new_index)

    def _ask_name(self, title: str, value: str, then: Callable[[str], None]) -> None:
        def done(name: str | None) -> None:
            if name is not None:
                then(name)

        self.push_screen(TextPrompt(title, value, "playlist name"), done)

    def action_playlist_create(self, song: Song | None = None) -> None:
        def create(name: str) -> None:
            try:
                playlist = self.library.create_playlist(name)
                if song is not None:
                    self.library.add_to_playlist(playlist.id, song)
            except LibraryError as exc:
                self.query_one(StatusBar).show(str(exc), StatusLevel.ERROR)
                return
            note = f" with {song.title}" if song else ""
            self.query_one(StatusBar).show(
                f"Created playlist {playlist.name}{note}.", StatusLevel.SUCCESS
            )
            self._refresh_playlists()
            self.query_one(PlaylistNav).select_section(
                section_id_for_playlist(playlist.id)
            )

        self._ask_name("New playlist", "", create)

    def action_playlist_rename(self) -> None:
        playlist_id = self.query_one(PlaylistNav).playlist_id
        if playlist_id is None:
            self.query_one(StatusBar).show("Highlight a playlist to rename it.")
            return
        current = self._safe_db(lambda: self.library.playlist(playlist_id))
        if current is None:
            return

        def rename(name: str) -> None:
            try:
                playlist = self.library.rename_playlist(playlist_id, name)
            except LibraryError as exc:
                self.query_one(StatusBar).show(str(exc), StatusLevel.ERROR)
                return
            self.query_one(StatusBar).show(f"Renamed to {playlist.name}.")
            source = self._queue_source
            if source and source["id"] == playlist.id:
                source["name"] = playlist.name
                self._render_queue_source()
            self._refresh_playlists()

        self._ask_name("Rename playlist", current.name, rename)

    def action_playlist_delete(self) -> None:
        playlist_id = self.query_one(PlaylistNav).playlist_id
        if playlist_id is None:
            self.query_one(StatusBar).show("Highlight a playlist to delete it.")
            return
        playlist = self._safe_db(lambda: self.library.playlist(playlist_id))
        if playlist is None:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed and self.library.delete_playlist(playlist_id):
                self.query_one(StatusBar).show(
                    f"Deleted playlist {playlist.name}. "
                    "Liked songs and downloads are kept."
                )
                if self._queue_source and self._queue_source["id"] == playlist_id:
                    self._set_queue_source(None)  # the queue itself stays
                self._refresh_playlists()

        self.push_screen(
            ConfirmDialog(
                f"Delete playlist “{playlist.name}” ({playlist.count} tracks)? "
                "Likes and downloaded files are not affected."
            ),
            done,
        )

    def action_add_to_playlist(self) -> None:
        song = self._target_song()
        if song is None:
            self.query_one(StatusBar).show("Highlight a track first.")
            return
        playlists = self._safe_db(
            lambda: [(p.id, p.name, p.count) for p in self.library.playlists()]
        )
        if playlists is None:
            return

        def chosen(choice: int | str | None) -> None:
            if choice is None:
                return
            if choice == NEW_PLAYLIST:
                self.action_playlist_create(song)
                return
            try:
                added = self.library.add_to_playlist(int(choice), song)
                name = self.library.playlist(int(choice)).name
            except LibraryError as exc:
                self.query_one(StatusBar).show(str(exc), StatusLevel.ERROR)
                return
            self.query_one(StatusBar).show(
                f"Added {song.title} to {name}."
                if added
                else f"{song.title} is already in {name}.",
                StatusLevel.SUCCESS if added else StatusLevel.INFO,
            )
            if self._active_view() == "playlists-view":
                self._refresh_playlists()

        self.push_screen(PlaylistPicker(f"Add “{song.title}” to…", playlists), chosen)

    # ------------------------------------------------------------ likes

    def action_toggle_favorite(self, deck: bool = False) -> None:
        song = self._target_song(deck=deck)
        if song is None:
            return
        favorite = self._safe_db(lambda: self.library.toggle_favorite(song))
        if favorite is None:
            return
        (self._favorites.add if favorite else self._favorites.discard)(song.id)
        self.query_one(StatusBar).show(
            f"Liked {song.title}." if favorite else f"Unliked {song.title}."
        )
        self._render_playback(self.playback)
        if self._active_view() == "liked-view":
            self._refresh_liked()

    def _recent_table(self) -> RecentTable:
        return self.query_one(RecentTable)

    def action_clear_history(self) -> None:
        def done(confirmed: bool | None) -> None:
            if confirmed:
                removed = self._safe_db(self.library.clear_history) or 0
                self.query_one(StatusBar).show(f"Cleared {removed} history entries.")
                self._refresh_view(self._active_view())

        self.push_screen(
            ConfirmDialog(
                "Clear recently played and play counts? Likes, playlists, and "
                "downloads are kept."
            ),
            done,
        )
