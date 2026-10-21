"""Importing playlists (Spotify links, export files, screenshots) and matching
on YouTube."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import tempfile
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual import work
from textual.markup import escape

from pixelfm.models import Song
from pixelfm.services.library import ImportItem, LibraryError
from pixelfm.services.matching import LIKELY, STRONG, Matcher, query_for
from pixelfm.services.screenshot import (
    ScreenshotError,
    clipboard_image,
    default_reader,
    looks_like_images,
    read_screenshots,
    split_paths,
)
from pixelfm.services.spotify import (
    NeedsLogin,
    NotAllowed,
    SpotifyClient,
    SpotifyError,
    SpotifyPlaylist,
    SpotifyTrack,
    parse_playlist_link,
    read_export,
)
from pixelfm.widgets import ConfirmDialog, PlaylistNav, StatusLevel, TextPrompt
from pixelfm.widgets.importer import (
    CandidatePicker,
    ChoicePicker,
    ImportPreview,
    MatchReview,
    ScreenshotReview,
    SpotifySetup,
)
from pixelfm.widgets.views import section_id_for_playlist

if TYPE_CHECKING:
    from pixelfm.services.matching import Candidate

log = logging.getLogger(__name__)

EXPORT_SUFFIXES = (".json", ".csv")


def as_track(item: ImportItem) -> SpotifyTrack:
    return SpotifyTrack(
        item.position,
        item.title,
        item.artists,
        item.album,
        item.duration,
        item.spotify_id,
        item.artwork_url,
        item.explicit,
        item.is_local,
    )


def matched_song(item: ImportItem | SpotifyTrack, video: Song) -> Song:
    """The playable entry: the YouTube video, shown with the imported title,
    artists, and (album) artwork."""
    artist = ", ".join(item.artists) or video.artist
    return Song(
        id=video.id,
        title=item.title,
        artist=artist,
        duration=video.duration or round(item.duration or 0),
        source_url=video.source_url,
        thumbnail_url=item.artwork_url or video.thumbnail_url,
    )


class ImportMixin:
    """Mixed into :class:`~pixelfm.app.PixelFMApp`."""

    # ----------------------------------------------------------- set-up

    def _spotify_client_id(self) -> str:
        return (
            os.environ.get("PIXELFM_SPOTIFY_CLIENT_ID")
            or self._pref("spotify_client_id", "")
            or ""
        ).strip()

    def _spotify(self) -> SpotifyClient | None:
        client_id = self._spotify_client_id()
        if not client_id:
            return None
        if self._spotify_client is None or self._spotify_client.client_id != client_id:
            self._spotify_client = SpotifyClient(
                client_id, self.token_store, **self._spotify_options
            )
        return self._spotify_client

    def action_spotify_setup(self, then: Any = None) -> None:
        def done(client_id: str | None) -> None:
            if not client_id:
                return
            self._save_pref("spotify_client_id", client_id)
            self._status("Spotify client ID saved.", StatusLevel.SUCCESS)
            self._refresh_settings()
            if then is not None:
                then()

        self.push_screen(SpotifySetup(self._spotify_client_id()), done)

    def action_spotify_sign_out(self) -> None:
        client = self._spotify()
        if client is not None:
            client.logout()
        self._status("Signed out of Spotify (the stored sign-in was removed).")
        self._refresh_settings()

    # ------------------------------------------------------------ import

    def action_import_playlist(self) -> None:
        """``I`` / ⤓ IMPORT: paste a Spotify link or an export file path."""
        if self._importing is not None and not self._importing.is_finished:
            self._status("An import is already running.")
            return

        def submitted(text: str | None) -> None:
            if text is not None:  # empty: the screenshot on the clipboard
                self._importing = self.import_source(text.strip())

        self.push_screen(
            TextPrompt(
                "Import a playlist: paste a Spotify playlist link or the path to an "
                "export file (.json / .csv), or drop screenshots of a song list "
                "here. Leave it empty to use a screenshot on the clipboard.",
                "",
                "https://open.spotify.com/playlist/…  or  screenshot.png",
            ),
            submitted,
        )

    @work(group="import", exclusive=True, exit_on_error=False)
    async def import_source(self, text: str) -> None:
        try:
            await self._import_source(text)
        except Exception as exc:  # never fail silently
            log.exception("import failed")
            self._status(f"Import failed: {exc}", StatusLevel.ERROR)

    async def _import_source(self, text: str) -> None:
        if not text or looks_like_images(text):
            await self._import_screenshots(split_paths(text))
            return
        path = Path(text).expanduser()  # noqa: ASYNC240 (no I/O: string work)
        is_file = await asyncio.to_thread(path.is_file)
        if path.suffix.lower() in EXPORT_SUFFIXES or is_file:
            await self._import_file(path)
        else:
            await self._import_link(text)

    async def _import_file(self, path: Path) -> None:
        try:
            playlists = await asyncio.to_thread(read_export, path)
        except SpotifyError as exc:
            self._status(str(exc), StatusLevel.ERROR)
            return
        if len(playlists) == 1:
            self._preview(playlists[0])
            return
        choices = [
            (str(i), f"{escape(p.name)}  [$text-muted]{p.total} tracks[/]")
            for i, p in enumerate(playlists)
        ]

        def chosen(key: str | None) -> None:
            if key is not None:
                self._preview(playlists[int(key)])

        self.push_screen(
            ChoicePicker(
                f"{len(playlists)} playlists in {path.name}: import which?", choices
            ),
            chosen,
        )

    async def _import_link(self, text: str) -> None:
        try:
            playlist_id = parse_playlist_link(text)
            log.info("import: Spotify playlist %s", playlist_id)
        except SpotifyError as exc:
            self._status(str(exc), StatusLevel.ERROR)
            return
        client = self._spotify()
        if client is None:
            self._status("Spotify needs a one-time setup first.")
            self.action_spotify_setup(then=lambda: self._retry_import(text))
            return
        for attempt in range(2):
            try:
                if not client.signed_in:
                    self._status(
                        "Opening your browser to sign in to Spotify (PixelFM never "
                        "sees your password)…",
                        StatusLevel.LOADING,
                    )
                    await client.login(self._open_browser)
                self._status("Reading the playlist from Spotify…", StatusLevel.LOADING)

                def progress(done: int, total: int) -> None:
                    self._status(
                        f"Reading the playlist from Spotify… {done}/{total}",
                        StatusLevel.LOADING,
                    )

                playlist = await client.playlist(playlist_id, on_progress=progress)
            except NeedsLogin as exc:
                log.warning("spotify sign-in not usable: %s", exc)
                client.logout()
                if attempt == 0:
                    continue
                self._status("Spotify sign-in failed; try again.", StatusLevel.ERROR)
                return
            except NotAllowed as exc:
                self._status(
                    "Spotify won't share this playlist's tracks.", StatusLevel.ERROR
                )
                self.push_screen(ConfirmDialog(f"{exc} (press y or n to close)"))
                return
            except SpotifyError as exc:
                log.warning("import failed: %s", exc)
                self._status(str(exc), StatusLevel.ERROR)
                return
            break
        else:  # pragma: no cover
            return
        if not playlist.tracks:
            self._status(f"“{playlist.name}” is empty: nothing to import.")
            return
        self._preview(playlist)

    async def _import_screenshots(self, paths: list[Path]) -> None:
        """Read songs from screenshots (none: the clipboard's), let the user
        check them, then add them to a new or an existing playlist."""
        try:
            reader = default_reader()
        except ScreenshotError as exc:
            self._status(str(exc), StatusLevel.ERROR)
            return
        with tempfile.TemporaryDirectory(prefix="pixelfm-clipboard-") as tmp:
            if not paths:
                pasted = Path(tmp) / "clipboard.png"
                try:
                    found = await asyncio.to_thread(clipboard_image, reader, pasted)
                except ScreenshotError as exc:
                    self._status(str(exc), StatusLevel.ERROR)
                    return
                if not found:
                    self._status(
                        "There is no image on the clipboard. Take a screenshot to "
                        "the clipboard (⌃⇧⌘4 on a Mac), or drop the image file here.",
                        StatusLevel.ERROR,
                    )
                    return
                paths = [pasted]
            count = f"{len(paths)} screenshots" if len(paths) > 1 else "the screenshot"
            self._status(
                f"Reading {count} with {reader.name} (on this computer)…",
                StatusLevel.LOADING,
            )
            try:
                playlist, empty = await asyncio.to_thread(
                    read_screenshots, paths, reader
                )
            except ScreenshotError as exc:
                log.warning("screenshot import failed: %s", exc)
                self._status(str(exc), StatusLevel.ERROR)
                return
        if not playlist.tracks:
            self._status(
                "No songs found in the screenshot. Crop it to the song list (titles "
                "and artists readable) and try again.",
                StatusLevel.ERROR,
            )
            return
        self._status(f"Found {len(playlist.tracks)} songs. Check them, then enter.")

        def reviewed(checked: SpotifyPlaylist | None) -> None:
            if checked is not None:
                self._choose_target(checked)

        self.push_screen(ScreenshotReview(playlist, empty), reviewed)

    def _choose_target(self, playlist: SpotifyPlaylist) -> None:
        existing = self._safe_load(self.library.playlists, [])
        choices = [("new", "[b]+ New playlist[/]")] + [
            (
                str(p.id),
                f"{escape(p.name)}  [$text-muted]{p.count} "
                f"song{'' if p.count == 1 else 's'}[/]",
            )
            for p in existing
        ]

        def chosen(key: str | None) -> None:
            if key == "new":
                self._name_new(playlist)
            elif key is not None:
                self._add_import(int(key), playlist)

        self.push_screen(
            ChoicePicker(
                f"Add {len(playlist.tracks)} songs to which playlist? Songs "
                "already in it are skipped.",
                choices,
            ),
            chosen,
        )

    def _name_new(self, playlist: SpotifyPlaylist) -> None:
        def named(name: str | None) -> None:
            if name is not None and name.strip():
                self._save_import(
                    dataclasses.replace(playlist, name=name.strip()), match=True
                )

        self.push_screen(
            TextPrompt("Name the new playlist", playlist.name, "Playlist name"),
            named,
        )

    def _add_import(self, playlist_id: int, playlist: SpotifyPlaylist) -> None:
        try:
            result = self.library.add_import(playlist_id, playlist)
        except (LibraryError, Exception) as exc:  # nothing is half-written
            log.exception("adding to a playlist failed")
            self._status(f"Couldn't add the songs: {exc}", StatusLevel.ERROR)
            return
        name = result.playlist.name
        already = (
            f"; {result.already} already in it (skipped)" if result.already else ""
        )
        if not result.added:
            self._status(
                f"Nothing new: every song is already in “{name}”.", StatusLevel.SUCCESS
            )
        else:
            self._status(
                f"Added {result.added} songs to “{name}”{already}. "
                "Matching on YouTube…",
                StatusLevel.SUCCESS,
            )
        self._unavailable = self._safe_load(self.library.unavailable_ids, set())
        self.action_show_view("playlists-view")
        self._refresh_playlists()
        self.query_one(PlaylistNav).select_section(section_id_for_playlist(playlist_id))
        self._show_playlist()
        if result.added:
            self.match_playlist(playlist_id)

    def _retry_import(self, text: str) -> None:
        self._importing = self.import_source(text)

    def _open_browser(self, url: str) -> None:
        opened = webbrowser.open(url)
        log.info("spotify sign-in: browser %s", "opened" if opened else "did not open")
        if not opened:
            self._status(f"Open this link to sign in: {url}", StatusLevel.LOADING)

    def _preview(self, playlist: SpotifyPlaylist) -> None:
        def chosen(choice: str | None) -> None:
            if choice in ("import", "match"):
                self._save_import(playlist, match=choice == "match")

        self.push_screen(ImportPreview(playlist), chosen)

    def _save_import(self, playlist: SpotifyPlaylist, *, match: bool) -> None:
        try:
            created, info = self.library.create_import(playlist)
        except (LibraryError, Exception) as exc:  # nothing is half-written
            log.exception("import failed")
            self._status(f"Import failed: {exc}", StatusLevel.ERROR)
            return
        renamed = (
            f" (saved as “{created.name}”: that name was taken)"
            if created.name != playlist.name.strip()
            else ""
        )
        self._status(
            f"Imported “{playlist.name}”: {info.total} tracks, "
            f"{info.skipped} skipped{renamed}."
            + (" Matching on YouTube…" if match else " Press M to match on YouTube."),
            StatusLevel.SUCCESS,
        )
        self._unavailable = self._safe_load(self.library.unavailable_ids, set())
        self.action_show_view("playlists-view")
        self._refresh_playlists()
        self.query_one(PlaylistNav).select_section(section_id_for_playlist(created.id))
        self._show_playlist()
        if match:
            self.match_playlist(created.id)

    # ---------------------------------------------------------- matching

    def _matcher(self) -> Matcher:
        if self._matcher_instance is None:
            self._matcher_instance = Matcher(self.provider.search)
        return self._matcher_instance

    @work(group="matching", exclusive=True, exit_on_error=False)
    async def match_playlist(self, playlist_id: int) -> None:
        """Match every entry not matched yet, a couple at a time. Cancelling
        (X) keeps what is done; the rest stays "not matched yet"."""
        items = self.library.import_items(playlist_id, ("pending", "unmatched"))
        if not items:
            self._status("Every track in this playlist is already matched.")
            return
        self._matching = (playlist_id, 0, len(items))
        matcher = self._matcher()
        done = matched = 0

        async def one(item: ImportItem) -> None:
            nonlocal done, matched
            try:
                best = await matcher.best(as_track(item))
            except Exception as exc:  # network trouble: leave it for later
                log.info("match failed for %s: %s", item.title, exc)
                best = None
            if best is not None:
                status = "matched" if best.score >= STRONG else "review"
                stored = self.library.set_match(
                    playlist_id,
                    item.position,
                    matched_song(item, best.song),
                    status=status,
                    confidence=round(best.score, 3),
                    note=", ".join(best.reasons[:3]),
                )
                matched += stored in ("matched", "review")
            else:
                self.library.set_match(
                    playlist_id,
                    item.position,
                    None,
                    status="unmatched",
                    note="no confident YouTube match",
                )
            done += 1
            self._matching = (playlist_id, done, len(items))
            self._status(
                f"Matching on YouTube: {done}/{len(items)} · {matched} found · "
                "X stops (matches so far are kept)",
                StatusLevel.LOADING,
            )
            self._refresh_import_views(playlist_id)

        try:
            await asyncio.gather(*(one(item) for item in items))
        finally:
            self._matching = None
            self._refresh_import_views(playlist_id)
        info = self.library.import_info(playlist_id)
        if info is not None:
            self._status(
                f"Matching done: {info.matched} matched, {info.unmatched} unmatched, "
                f"{info.skipped} skipped. Press M to review.",
                StatusLevel.SUCCESS,
            )

    def action_stop_matching(self) -> None:
        if self._matching is None:
            self._status("No matching is running.")
            return
        self.workers.cancel_group(self, "matching")
        self._matching = None
        self._status("Matching stopped. Matches so far are kept; M resumes.")

    def _refresh_import_views(self, playlist_id: int) -> None:
        # Placeholders are "unavailable" (marked ✕); matched ones no longer are.
        self._unavailable = self._safe_load(self.library.unavailable_ids, set())
        if self._active_view() == "playlists-view":
            nav = self.query_one(PlaylistNav)
            if nav.playlist_id == playlist_id:
                self._show_playlist()

    def _import_heading(self, playlist_id: int) -> str:
        """Extra heading text for imported playlists (empty otherwise)."""
        info = self._safe_db(lambda: self.library.import_info(playlist_id))
        if info is None:
            return ""
        source = {"spotify": "Spotify", "screenshot": "screenshot"}.get(
            info.source, "file"
        )
        parts = [f"{source}: {info.matched} matched"]
        if info.unmatched:
            parts.append(f"{info.unmatched} unmatched")
        if info.skipped:
            parts.append(f"{info.skipped} skipped")
        if self._matching and self._matching[0] == playlist_id:
            parts.append(f"matching {self._matching[1]}/{self._matching[2]} · X stops")
        else:
            parts.append("M review")
        return " · ".join(parts)

    # ------------------------------------------------------------ review

    def action_review_matches(self) -> None:
        """``M``: every imported entry and its match; fix any of them."""
        playlist_id = self.query_one(PlaylistNav).playlist_id
        info = (
            self._safe_db(lambda: self.library.import_info(playlist_id))
            if playlist_id is not None
            else None
        )
        if playlist_id is None or info is None:
            self._status("Highlight an imported playlist to review its matches.")
            return
        items = self.library.import_items(playlist_id)

        def done(result: tuple[str, int] | None) -> None:
            if result is None:
                return
            kind, position = result
            if kind == "match-rest":
                self.match_playlist(playlist_id)
            elif kind == "unmatch":
                self.library.set_match(
                    playlist_id,
                    position,
                    None,
                    status="unmatched",
                    note="unmatched by you",
                )
                self._refresh_import_views(playlist_id)
                self.action_review_matches()
            elif kind == "pick":
                item = next(i for i in items if i.position == position)
                self.find_candidates(playlist_id, item, query_for(as_track(item)))

        title = (
            f"{info.source_name}: {info.matched} matched · "
            f"{info.unmatched} unmatched · {info.skipped} skipped"
        )
        self.push_screen(MatchReview(title, items), done)

    @work(group="candidates", exclusive=True, exit_on_error=False)
    async def find_candidates(
        self, playlist_id: int, item: ImportItem, query: str
    ) -> None:
        self._status(f"Searching YouTube for {query}…", StatusLevel.LOADING)
        try:
            candidates = await self._matcher().candidates(as_track(item), query)
        except Exception as exc:
            self._status(f"YouTube search failed: {exc}", StatusLevel.ERROR)
            return
        self._status(f"{len(candidates)} candidates for {item.title}.")

        def done(result: tuple[str, object] | None) -> None:
            if result is None:
                self.action_review_matches()
                return
            kind, value = result
            if kind == "search":
                self.find_candidates(playlist_id, item, str(value))
            elif kind == "none":
                self.library.set_match(
                    playlist_id,
                    item.position,
                    None,
                    status="unmatched",
                    note="left unmatched by you",
                )
                self._refresh_import_views(playlist_id)
                self.action_review_matches()
            elif kind == "choose":
                chosen: Candidate = candidates[int(value)]  # type: ignore[arg-type]
                stored = self.library.set_match(
                    playlist_id,
                    item.position,
                    matched_song(item, chosen.song),
                    status="matched",
                    confidence=round(chosen.score, 3),
                    note="chosen by you",
                )
                self._status(
                    f"Matched {item.title}."
                    if stored == "matched"
                    else "That video is already another entry of this playlist.",
                    StatusLevel.SUCCESS if stored == "matched" else StatusLevel.ERROR,
                )
                self._refresh_import_views(playlist_id)
                self.action_review_matches()

        self.push_screen(CandidatePicker(item, query, candidates), done)

    # ------------------------------------------------------ now playing

    def _display_meta(self, song: Song) -> dict[str, str]:
        """Imported tracks show their Spotify metadata in system controls."""
        return self._safe_load(lambda: self.library.import_meta(song.id), None) or {}


__all__ = ["LIKELY", "ImportMixin", "as_track", "matched_song"]
