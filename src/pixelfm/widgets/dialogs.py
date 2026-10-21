"""Modal dialogs: text prompt, confirmation, playlist picker, and help."""

from __future__ import annotations

from collections.abc import Sequence

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

NEW_PLAYLIST = "__new__"


class TextPrompt(ModalScreen[str | None]):
    """Ask for one line of text. Dismisses with the text, or ``None``."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._value = value
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(
                f"[b $primary]{escape(self._title)}[/]", classes="dialog-title"
            )
            yield Input(self._value, placeholder=self._placeholder, id="prompt-input")
            yield Static(
                "[$text-muted]enter to save · esc to cancel[/]", classes="dialog-hint"
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmDialog(ModalScreen[bool]):
    """Yes/no confirmation. ``y``/enter confirms; ``n``/esc cancels."""

    BINDINGS = [
        Binding("y,enter", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(
                f"[b $warning]{escape(self._question)}[/]", classes="dialog-title"
            )
            yield Static(
                "[b $primary]y[/] yes   [b $primary]n[/] no", classes="dialog-hint"
            )

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class PlaylistPicker(ModalScreen[int | str | None]):
    """Choose a playlist (dismisses with its id) or :data:`NEW_PLAYLIST`."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, playlists: Sequence[tuple[int, str, int]]) -> None:
        super().__init__()
        self._title = title
        self._playlists = playlists

    def compose(self) -> ComposeResult:
        options = [
            Option(f"{escape(name)}  [$text-muted]{count}[/]", id=str(pid))
            for pid, name, count in self._playlists
        ]
        options.append(Option("[$accent]+ New playlist…[/]", id=NEW_PLAYLIST))
        with Vertical(classes="dialog"):
            yield Static(
                f"[b $primary]{escape(self._title)}[/]", classes="dialog-title"
            )
            yield OptionList(*options, id="picker")
            yield Static(
                "[$text-muted]enter to choose · esc to cancel[/]", classes="dialog-hint"
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        self.dismiss(NEW_PLAYLIST if option_id == NEW_PLAYLIST else int(option_id))

    def action_cancel(self) -> None:
        self.dismiss(None)


HELP = """\
[b $primary]Views[/]  [b]1[/] Booth  [b]2[/] Search  [b]3[/] Queue  [b]4[/] Playlists  [b]5[/] Liked
         [b]6[/] Downloads  [b]7[/] Recent  [b]8[/] Settings
  [b]/[/] search box   [b]ctrl+f[/] filter this list   [b]esc[/] clear filter / back
  [b]tab[/] moves focus (buttons, faders, lists)

[b $primary]Deck[/]
  [b]enter[/] play highlighted       [b]space[/] play / pause / resume / retry
  [b]n[/] next   [b]b[/] previous       [b]x[/] stop      [b]][/] [b][[/] seek ±10 s
  [b]r[/] repeat off/all/one     [b]z[/] shuffle queue (on/off)   timeline: click or ←/→
  [b]Z[/] shuffle playlist: play the highlighted (or current) playlist once in a
    fresh random order; the saved playlist and the [b]z[/] toggle are not changed

[b $primary]Mixer[/] (applied with a short rebuffer)
  [b]+[/] [b]-[/] volume   [b]}[/] [b]{[/] bass ±2 dB   [b])[/] [b]([/] treble ±2 dB   [b]N[/] normalize
  faders: focus with tab, then ←/→ or click

[b $primary]Tracks[/] (highlighted, or the deck's track)
  [b]a[/] add to queue   [b]A[/] play next   [b]f[/] like/unlike   [b]p[/] add to playlist
  [b]D[/] download (only audio you own or may download)

[b $primary]Lists[/]
  Queue:      [b]d[/] remove   [b]K[/]/[b]J[/] move   [b]C[/] clear (asks first)
  Playlists:  [b]enter[/] play all   [b]Z[/] shuffle   [b]A[/] append   [b]c[/] new   [b]e[/] rename   [b]d[/] delete
              [b]I[/] import (Spotify link, export file, screenshots)   [b]M[/] review matches   [b]X[/] stop matching
              tracks: [b]d[/] remove   [b]K[/]/[b]J[/] reorder   [b]h[/] back
  Liked:      [b]d[/] unlike   [b]o[/] sort   [b]A[/] append all
  Downloads:  [b]d[/] delete file   [b]L[/] relink   [b]X[/] clean missing   [b]c[/] cancel download

[b $primary]Display[/]
  [b]m[/] artwork style   [b]v[/] full / compact layout   [b]V[/] meters on/off
  [b]P[/] party mode on/off (style in Settings: ambient, pulse, strobe, rave)
  [b]R[/] RAVE MODE: on, then the next pattern (chase, dance floor, laser, bass, rainbow)
  Theme "music" (default) takes its colours from the current track's artwork.

[b $primary]Outside the terminal[/] (macOS)
  media keys, headphones, and Control Centre: play/pause, next, previous

[b $primary]Other[/]
  [b]?[/] this help   [b]q[/] or [b]ctrl+c[/] quit (saves the session: queue,
  position, view; the next start restores it paused)

[$text-muted]Shortcuts never fire while you are typing in a text box.[/]"""  # noqa: E501 (help screen layout)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,question_mark,q", "close", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog help"):
            yield Static("[b $primary]PixelFM keys[/]", classes="dialog-title")
            yield Static(HELP)

    def action_close(self) -> None:
        self.dismiss(None)


class WelcomeScreen(ModalScreen[None]):
    """First-run check: what is installed, what is missing, where data lives."""

    BINDINGS = [Binding("enter,escape,space", "close", "Start")]

    def __init__(
        self,
        checks: Sequence[tuple[str, bool, str, str]],
        paths: Sequence[tuple[str, str]],
    ) -> None:
        """``checks``: (name, found, detail, install); ``paths``: (label, path)."""
        super().__init__()
        self._checks = checks
        self._paths = paths

    def compose(self) -> ComposeResult:
        lines = []
        for name, found, detail, install in self._checks:
            if found:
                lines.append(
                    f"  [b $success]ok[/]       {escape(name)} "
                    f"[$text-muted]{escape(detail)}[/]"
                )
            else:
                lines.append(f"  [b $error]missing[/]  {escape(name)}")
                lines.append(
                    f"           [$text-muted]install:[/] [b]{escape(install)}[/]"
                )
        ready = all(found for _, found, _, _ in self._checks)
        summary = (
            "[$success]Everything needed for playback is installed.[/]"
            if ready
            else "[$warning]Search works now; audio needs the missing tools above.\n"
            "PixelFM never installs software without asking. Run "
            "[b]pixelfm --check[/b] after installing.[/]"
        )
        where = "\n".join(
            f"  [$text-muted]{escape(label):<9}[/] {escape(path)}"
            for label, path in self._paths
        )
        with VerticalScroll(classes="dialog help"):
            yield Static("[b $primary]Welcome to PixelFM[/]", classes="dialog-title")
            yield Static("\n".join(lines))
            yield Static(summary, classes="dialog-hint")
            yield Static(f"[b $primary]Your data[/]\n{where}", classes="dialog-hint")
            yield Static(
                "[b $primary]Start[/]  [b]/[/] search · [b]enter[/] play · "
                "[b]space[/] pause · [b]1 2 3[/] views · [b]?[/] all keys",
                classes="dialog-hint",
            )
            yield Static("[$text-muted]enter to continue[/]", classes="dialog-hint")

    def action_close(self) -> None:
        self.dismiss(None)
