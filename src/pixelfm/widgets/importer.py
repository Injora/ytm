"""Dialogs for importing playlists and reviewing their YouTube matches."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from pixelfm.models import format_duration
from pixelfm.services.library import ImportItem
from pixelfm.services.matching import Candidate
from pixelfm.services.spotify import REDIRECT_URI, SpotifyPlaylist
from pixelfm.widgets.dialogs import TextPrompt

STATUS_LABELS = {
    "matched": "[$success]matched[/]",
    "review": "[$warning]likely[/]",
    "pending": "[$text-muted]not matched yet[/]",
    "unmatched": "[$error]unmatched[/]",
    "unavailable": "[$text-muted]unavailable[/]",
    "duplicate": "[$text-muted]duplicate[/]",
    "skipped": "[$text-muted]skipped[/]",
}


PLAIN_STATUS = {
    "matched": "matched",
    "review": "likely",
    "pending": "not matched yet",
    "unmatched": "unmatched",
    "unavailable": "unavailable",
    "duplicate": "duplicate",
    "skipped": "skipped",
}


SOURCE_LABELS = {
    "spotify": "Spotify",
    "file": "export file",
    "screenshot": "screenshot",
}


def parse_row(text: str) -> tuple[str, tuple[str, ...]] | None:
    """ "Title — Artist, Artist" (or "Title - Artist") as typed when editing a
    row read from a screenshot."""
    for separator in (" \u2014 ", " \u2013 ", " - "):
        if separator in text:
            title, artists = text.rsplit(separator, 1)
            break
    else:
        title, artists = text, ""
    title = " ".join(title.split())
    if not title:
        return None
    return title, tuple(a.strip() for a in artists.split(",") if a.strip())


class ScreenshotReview(ModalScreen[SpotifyPlaylist | None]):
    """The songs read from screenshots, to check before adding them: leave
    out a misread row (space) or fix it (e). Dismisses with the songs kept,
    or None."""

    BINDINGS = [
        Binding("space", "toggle", "Keep / leave out"),
        Binding("e", "edit", "Edit"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, playlist: SpotifyPlaylist, empty: Sequence[str] = ()) -> None:
        super().__init__()
        self._playlist = playlist
        self._tracks = list(playlist.tracks)
        self._keep = [True] * len(self._tracks)
        self._empty = list(empty)

    def _label(self, index: int) -> str:
        track = self._tracks[index]
        mark = "[$success]✓[/]" if self._keep[index] else "[$text-muted]·[/]"
        length = format_duration(int(track.duration)) if track.duration else ""
        details = " · ".join(
            escape(part) for part in (track.artist, track.album) if part
        )
        title = escape(track.title)
        if not self._keep[index]:
            return f"{mark} [$text-muted][s]{title}[/s] — {details}[/]"
        tail = f" · {length}" if length else ""
        return f"{mark} {title}  [$text-muted]{details}{tail}[/]"

    def compose(self) -> ComposeResult:
        note = f" · no songs found in {', '.join(self._empty)}" if self._empty else ""
        with Vertical(classes="dialog help"):
            yield Static(
                f"[b $primary]{len(self._tracks)} songs found[/]"
                f"[$text-muted]{escape(note)}[/]",
                classes="dialog-title",
                id="screenshot-title",
            )
            yield OptionList(
                *[Option(self._label(i), id=str(i)) for i in range(len(self._tracks))],
                id="screenshot-rows",
            )
            yield Static(
                "[$text-muted]Read on this computer; check it before adding. "
                "space: keep / leave out · e: fix a row (Title — Artist) · "
                "enter: choose the playlist · esc: cancel[/]",
                classes="dialog-hint",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def _index(self) -> int | None:
        return self.query_one(OptionList).highlighted

    def _refresh(self, index: int) -> None:
        self.query_one(OptionList).replace_option_prompt_at_index(
            index, self._label(index)
        )

    def action_toggle(self) -> None:
        index = self._index()
        if index is not None:
            self._keep[index] = not self._keep[index]
            self._refresh(index)

    def action_edit(self) -> None:
        index = self._index()
        if index is None:
            return
        track = self._tracks[index]

        def done(text: str | None) -> None:
            parsed = parse_row(text or "")
            if parsed is None:
                return
            title, artists = parsed
            self._tracks[index] = dataclasses.replace(
                track, title=title, artists=artists
            )
            self._keep[index] = True
            self._refresh(index)

        current = f"{track.title} — {', '.join(track.artists)}".rstrip(" —")
        self.app.push_screen(
            TextPrompt("Fix this row: Title — Artist", current, "Title — Artist"),
            done,
        )

    @on(OptionList.OptionSelected)
    def _continue(self, event: OptionList.OptionSelected) -> None:
        kept = [t for t, keep in zip(self._tracks, self._keep, strict=True) if keep]
        if not kept:
            self.notify("Every row is left out: nothing to add.", severity="warning")
            return
        tracks = tuple(dataclasses.replace(t, position=i) for i, t in enumerate(kept))
        self.dismiss(
            dataclasses.replace(self._playlist, tracks=tracks, total=len(tracks))
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


class SpotifySetup(ModalScreen[str | None]):
    """One-time setup: the user's own Spotify developer app (Client ID only)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, client_id: str = "") -> None:
        super().__init__()
        self._client_id = client_id

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog help"):
            yield Static(
                "[b $primary]Connect Spotify (one time)[/]", classes="dialog-title"
            )
            yield Static(
                "Spotify only lets personal apps read playlists through your own "
                "free developer app. Its owner needs [b]Spotify Premium[/].\n\n"
                "  1. Open [b]developer.spotify.com/dashboard[/] and Create app.\n"
                f"  2. Redirect URI: [b]{REDIRECT_URI}[/] (exactly), API: Web API.\n"
                "  3. Copy the app's [b]Client ID[/] here. No secret is needed.\n\n"
                "Then PixelFM opens your browser to sign in on spotify.com; it never "
                "sees your password. Spotify lets such apps read the tracks of "
                "playlists [b]you own or collaborate on[/]."
            )
            yield Input(
                self._client_id, placeholder="Client ID (32 characters)", id="client-id"
            )
            yield Static(
                "[$text-muted]enter to save · esc to cancel[/]", classes="dialog-hint"
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoicePicker(ModalScreen[str | None]):
    """Pick one of several options (e.g. a playlist from an export file)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, choices: Sequence[tuple[str, str]]) -> None:
        super().__init__()
        self._title = title
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(
                f"[b $primary]{escape(self._title)}[/]", classes="dialog-title"
            )
            yield OptionList(
                *[Option(label, id=key) for key, label in self._choices], id="choices"
            )
            yield Static(
                "[$text-muted]enter to choose · esc to cancel[/]", classes="dialog-hint"
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ImportPreview(ModalScreen[str | None]):
    """What will be imported. Dismisses with "import", "match", or None."""

    BINDINGS = [
        Binding("i", "choose('import')", "Import"),
        Binding("m", "choose('match')", "Import and match"),
        Binding("escape", "choose(None)", "Cancel"),
    ]

    def __init__(self, playlist: SpotifyPlaylist) -> None:
        super().__init__()
        self._playlist = playlist

    def compose(self) -> ComposeResult:
        playlist = self._playlist
        tracks = playlist.tracks
        usable = sum(t.available for t in tracks)
        owner = f" · by {escape(playlist.owner)}" if playlist.owner else ""
        source = SOURCE_LABELS.get(playlist.source, "export file")
        skipped = len(tracks) - usable
        note = f" · {skipped} can't be imported" if skipped else ""
        lines = []
        for t in tracks[:500]:
            length = format_duration(int(t.duration)) if t.duration else "--:--"
            flag = "" if t.available else f"  [$text-muted]({escape(t.note)})[/]"
            local = "  [$text-muted](local file)[/]" if t.is_local else ""
            lines.append(
                Option(
                    f"[$text-muted]{t.position + 1:>3}[/] {escape(t.title)}  "
                    f"[$text-muted]{escape(t.artist)} · {length}[/]{local}{flag}"
                )
            )
        if len(tracks) > 500:
            lines.append(Option(f"[$text-muted]… and {len(tracks) - 500} more[/]"))
        with Vertical(classes="dialog help"):
            yield Static(
                f"[b $primary]{escape(playlist.name)}[/]  [$text-muted]{source}{owner}"
                f" · {len(tracks)} tracks{note}[/]",
                classes="dialog-title",
            )
            yield OptionList(*lines, id="preview-tracks")
            yield Static(
                "[$text-muted]Importing saves the metadata and keeps the order. "
                "Matching then looks each track up on YouTube in the background "
                "(nothing is downloaded); you can review every match.[/]",
                classes="dialog-hint",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("IMPORT (i)", id="import", compact=True, classes="action")
                yield Button(
                    "IMPORT AND MATCH (m)", id="match", compact=True, classes="action"
                )
                yield Button(
                    "CANCEL (esc)", id="cancel", compact=True, classes="action"
                )

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)

    def action_choose(self, choice: str | None) -> None:
        self.dismiss(choice)


class MatchReview(ModalScreen[tuple[str, int] | None]):
    """Every imported entry with its status. Dismisses with an action:
    ("pick", position), ("unmatch", position), or ("match-rest", -1)."""

    BINDINGS = [
        Binding("enter", "act('pick')", "Find a match"),
        Binding("u", "act('unmatch')", "Unmatch"),
        Binding("a", "rest", "Match the rest"),
        Binding("escape", "close", "Close"),
    ]

    def __init__(self, title: str, items: Sequence[ImportItem]) -> None:
        super().__init__()
        self._title = title
        self._items = list(items)

    def compose(self) -> ComposeResult:
        def label(item: ImportItem) -> str:
            plain = PLAIN_STATUS.get(item.status, item.status)
            status = STATUS_LABELS.get(item.status, item.status)
            pad = " " * max(1, 15 - len(plain))
            confidence = (
                f"{item.confidence:>4.0%}" if item.confidence is not None else "    "
            )
            note = (
                f"  [$text-muted]{escape(item.note)}[/]"
                if item.note and item.status != "matched"
                else ""
            )
            return (
                f"[$text-muted]{item.position + 1:>3}[/] {status}{pad}{confidence}  "
                f"{escape(item.title)} [$text-muted]— {escape(item.artist)}[/]{note}"
            )

        problems = [i for i in self._items if i.status not in ("matched",)]
        ordered = problems + [i for i in self._items if i.status == "matched"]
        with Vertical(classes="dialog help"):
            yield Static(
                f"[b $primary]{escape(self._title)}[/]", classes="dialog-title"
            )
            yield OptionList(
                *[Option(label(i), id=str(i.position)) for i in ordered], id="review"
            )
            yield Static(
                "[$text-muted]Unsure and unmatched first. enter: search YouTube and "
                "choose · u: unmatch · a: match everything not matched yet · esc[/]",
                classes="dialog-hint",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def _position(self) -> int | None:
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return None
        option_id = options.get_option_at_index(options.highlighted).id
        return int(option_id) if option_id is not None else None

    def action_act(self, kind: str) -> None:
        position = self._position()
        if position is not None:
            self.dismiss((kind, position))

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss(("pick", int(event.option.id)))

    def action_rest(self) -> None:
        self.dismiss(("match-rest", -1))

    def action_close(self) -> None:
        self.dismiss(None)


class CandidatePicker(ModalScreen[tuple[str, object] | None]):
    """YouTube candidates for one entry. Dismisses with ("choose", index),
    ("search", query), ("none", None) to leave it unmatched, or None."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(
        self, item: ImportItem, query: str, candidates: Sequence[Candidate]
    ) -> None:
        super().__init__()
        self._item = item
        self._query = query
        self._candidates = list(candidates)

    def compose(self) -> ComposeResult:
        item = self._item
        length = format_duration(int(item.duration)) if item.duration else "--:--"
        options = []
        for i, c in enumerate(self._candidates):
            song = c.song
            duration = format_duration(song.duration) if song.duration else "--:--"
            why = ", ".join(c.reasons) or "no strong signals"
            options.append(
                Option(
                    f"[b]{c.score:>4.0%}[/] {c.label:<6} {escape(song.title)}  "
                    f"[$text-muted]{escape(song.artist)} · {duration} · "
                    f"{escape(why)}[/]",
                    id=str(i),
                )
            )
        options.append(Option("[$accent]Leave it unmatched[/]", id="none"))
        with Vertical(classes="dialog help"):
            yield Static(
                f"[b $primary]{escape(item.title)}[/] [$text-muted]— "
                f"{escape(item.artist)} · {length}"
                + (f" · {escape(item.album)}" if item.album else "")
                + "[/]",
                classes="dialog-title",
            )
            yield Input(self._query, placeholder="search YouTube", id="candidate-query")
            yield OptionList(*options, id="candidates")
            yield Static(
                "[$text-muted]A percentage is how well a video fits; it is a guess, "
                "not a certainty. enter: use it · edit the search and press enter "
                "to search again · esc: back[/]",
                classes="dialog-hint",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(Input.Submitted)
    def _search(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(("search", event.value.strip()))

    @on(OptionList.OptionSelected)
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "none":
            self.dismiss(("none", None))
        elif event.option.id is not None:
            self.dismiss(("choose", int(event.option.id)))

    def action_cancel(self) -> None:
        self.dismiss(None)
