"""Downloads: starting, progress, the Downloaded Songs view, missing files."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from textual.widgets import Static

from pixelfm.paths import display_path
from pixelfm.services.downloads import (
    ACTIVE_STATUSES,
    DownloadProgress,
    DownloadStatus,
    default_downloads_dir,
    eligible_for_download,
    human_size,
    storage_summary,
)
from pixelfm.services.library import DownloadRecord, LibraryTrack
from pixelfm.widgets import (
    ConfirmDialog,
    DownloadsTable,
    StatusBar,
    StatusLevel,
    TextPrompt,
)

if TYPE_CHECKING:
    from pixelfm.models import Song

DOWNLOAD_NOTICE = (
    "Only download audio you own or have permission to download. YouTube's "
    "terms generally don't allow downloading without permission. PixelFM "
    "doesn't bypass DRM or access restrictions. Continue?"
)


class DownloadsMixin:
    """Mixed into :class:`~pixelfm.app.PixelFMApp`."""

    def _downloads_dir(self) -> Path:
        configured = self._pref("downloads_dir", self.config.downloads_dir)
        if isinstance(configured, str) and configured.strip():
            return Path(configured).expanduser()
        return default_downloads_dir()

    def _local_path(self, song_id: str) -> Path | None:
        """Lookup for the local-first resolver."""
        return self._download_paths.get(song_id)

    def _on_download_missing(self, song: Song, path: Path) -> None:
        """The resolver found a recorded download missing; it will stream."""
        self._missing_downloads.add(song.id)
        self.query_one(StatusBar).show(
            f"Downloaded file for {song.title} is missing; streaming instead. "
            "Relink or remove it in Downloads (6)."
        )

    # --------------------------------------------------------- starting

    def action_download(self, deck: bool = False) -> None:
        song = self._target_song(deck=deck)
        status = self.query_one(StatusBar)
        if song is None:
            status.show("Highlight a track to download it.")
            return
        if reason := eligible_for_download(song):
            status.show(reason)
            return
        if song.id in self._download_paths and song.id not in self._missing_downloads:
            status.show(f"{song.title} is already downloaded (see Downloads, 6).")
            return
        if self.downloads.is_active(song.id):
            status.show(
                f"{song.title} is already downloading. Press c in Downloads to cancel."
            )
            return
        if song.id in self._unavailable:
            status.show(f"{song.title} was unavailable last time; trying anyway.")

        def start(confirmed: bool | None = True) -> None:
            if not confirmed:
                return
            self._save_pref("download_notice_accepted", True)
            if self.downloads.enqueue(song):
                status.show(
                    f"Downloading {song.title} to "
                    f"{display_path(self._downloads_dir())}",
                    StatusLevel.LOADING,
                )

        if self._pref("download_notice_accepted", False) is True:
            start()
        else:
            self.push_screen(ConfirmDialog(DOWNLOAD_NOTICE), start)

    def _on_download_update(self, progress: DownloadProgress) -> None:
        status = self.query_one(StatusBar)
        song = progress.song
        if progress.status is DownloadStatus.DONE and progress.path is not None:
            path, size = progress.path, progress.downloaded

            def record() -> bool:
                self.library.add_download(song, path, size, None)
                return True

            if self._safe_db(record):
                self._download_paths[song.id] = path
                self._missing_downloads.discard(song.id)
                status.show(
                    f"Saved {song.title} ({human_size(size)}).", StatusLevel.SUCCESS
                )
        elif progress.status is DownloadStatus.FAILED:
            status.show(
                f"Download failed for {song.title}: {progress.error}", StatusLevel.ERROR
            )
        elif progress.status is DownloadStatus.CANCELLED:
            status.show(f"Download cancelled: {song.title}. No partial file was kept.")
        if self._active_view() == "downloads-view":
            self._render_active_downloads()
            if progress.status not in ACTIVE_STATUSES:
                self._refresh_downloads()
        self._render_playback(self.playback)

    # ------------------------------------------------------------- view

    def _render_active_downloads(self) -> None:
        jobs = [j for j in self.downloads.jobs.values() if j.status in ACTIVE_STATUSES]
        lines = []
        for job in jobs:
            if job.status is DownloadStatus.QUEUED:
                state = "queued"
            elif job.fraction is not None:
                state = f"{job.fraction * 100:3.0f}% of {human_size(job.total or 0)}"
            else:
                state = f"{human_size(job.downloaded)} so far"
            lines.append(
                f"[$primary]{self.glyphs.download}[/] {job.song.title}  "
                f"[$text-muted]{state}[/]"
            )
        widget = self.query_one("#downloads-active", Static)
        widget.update(
            "\n".join(lines)
            + (
                "\n[$text-muted]c cancels the newest active download[/]"
                if lines
                else ""
            )
        )
        widget.set_class(bool(lines), "-visible")

    def _refresh_downloads(self) -> None:
        records = self._safe_db(self.library.downloads) or []
        self._download_records = {r.song.id: r for r in records}
        self._download_paths = {r.song.id: r.path for r in records}
        self._render_downloads()
        self._render_active_downloads()
        self.check_download_files()

    def _render_downloads(self) -> None:
        records = list(self._download_records.values())
        tracks: list[LibraryTrack] = [r.track for r in records]

        def extra(track: LibraryTrack) -> str:
            if track.song.id in self._missing_downloads:
                return "MISSING"
            record = self._download_records.get(track.song.id)
            return human_size(record.size) if record else ""

        self._show_list(
            "downloads-view",
            tracks,
            extra,
            "No downloads yet. Press D on a track you own or may download "
            "(the booth's ↓ SAVE button does the same).",
        )

    def check_download_files(self) -> None:
        """Check every recorded file off the UI thread, then re-render."""
        records = list(self._download_records.values())
        directory = self._downloads_dir()

        async def check() -> None:
            def scan() -> tuple[set[str], str]:
                missing = {r.song.id for r in records if not r.path.is_file()}
                present = [r.size for r in records if r.song.id not in missing]
                return missing, storage_summary(directory, present)

            missing, summary = await asyncio.to_thread(scan)
            self._missing_downloads = missing
            note = (
                f" · [$error]{len(missing)} missing (X to clean, L to relink)[/]"
                if missing
                else ""
            )
            self.query_one("#downloads-info", Static).update(
                f"[b $primary]{self.glyphs.download} DOWNLOADED SONGS[/]  "
                f"[$text-muted]{summary}[/]{note}"
            )
            if self._active_view() == "downloads-view":
                self._render_downloads()

        self.run_worker(
            check(), group="download-scan", exclusive=True, exit_on_error=False
        )

    def _highlighted_download(self) -> DownloadRecord | None:
        song = self.query_one(DownloadsTable).highlighted_song
        return self._download_records.get(song.id) if song else None

    def action_download_cancel(self) -> None:
        active = [
            j for j in self.downloads.jobs.values() if j.status in ACTIVE_STATUSES
        ]
        highlighted = self.query_one(DownloadsTable).highlighted_song
        target = next(
            (j for j in active if highlighted and j.song.id == highlighted.id), None
        )
        target = target or (active[-1] if active else None)
        if target is None:
            self.query_one(StatusBar).show("No download is running.")
            return
        self.downloads.cancel(target.song.id)
        self.query_one(StatusBar).show(f"Cancelling {target.song.title}…")

    def action_download_delete(self) -> None:
        record = self._highlighted_download()
        if record is None:
            return
        missing = record.song.id in self._missing_downloads
        question = (
            f"Remove the missing download “{record.song.title}” from the list?"
            if missing
            else f"Delete the downloaded file for “{record.song.title}” "
            f"({human_size(record.size)})? Likes and playlists keep the track."
        )

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                return

            async def delete() -> None:
                if not missing:
                    with contextlib.suppress(FileNotFoundError):
                        await asyncio.to_thread(record.path.unlink)
                self._safe_db(lambda: self.library.remove_download(record.song.id))
                self._download_paths.pop(record.song.id, None)
                self._missing_downloads.discard(record.song.id)
                self.query_one(StatusBar).show(
                    f"Removed download: {record.song.title}."
                )
                self._refresh_downloads()
                self._render_playback(self.playback)

            self.run_worker(delete(), exit_on_error=False)

        self.push_screen(ConfirmDialog(question), done)

    def action_download_relink(self) -> None:
        record = self._highlighted_download()
        if record is None:
            return

        def relink(text: str | None) -> None:
            if not text:
                return
            path = Path(text).expanduser()

            async def apply() -> None:
                ok = await asyncio.to_thread(path.is_file)
                if not ok:
                    self.query_one(StatusBar).show(
                        f"No file at {path}.", StatusLevel.ERROR
                    )
                    return
                size = (await asyncio.to_thread(path.stat)).st_size
                self._safe_db(
                    lambda: self.library.relink_download(record.song.id, path, size)
                )
                self.query_one(StatusBar).show(
                    f"Relinked {record.song.title} to {display_path(path)}.",
                    StatusLevel.SUCCESS,
                )
                self._refresh_downloads()

            self.run_worker(apply(), exit_on_error=False)

        self.push_screen(
            TextPrompt("Relink: path to the audio file", str(record.path), "file path"),
            relink,
        )

    def action_download_clean_missing(self) -> None:
        missing = [
            self._download_records[i]
            for i in self._missing_downloads
            if i in self._download_records
        ]
        if not missing:
            self.query_one(StatusBar).show("No missing files.")
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                for record in missing:
                    self._safe_db(
                        lambda r=record: self.library.remove_download(r.song.id)
                    )
                    self._download_paths.pop(record.song.id, None)
                self._missing_downloads.clear()
                self.query_one(StatusBar).show(
                    f"Removed {len(missing)} missing entries."
                )
                self._refresh_downloads()

        self.push_screen(
            ConfirmDialog(f"Remove {len(missing)} entries whose files are missing?"),
            done,
        )
