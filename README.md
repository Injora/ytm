# PixelFM 2.1: a cyberpunk DJ booth for your terminal

PixelFM is a full-screen terminal music player: detailed cover art, an interface
that takes its colours from the artwork of whatever is playing, and real-time
meters that measure the audio you are actually hearing. Search YouTube, build
and shuffle playlists, like and download tracks you're allowed to keep, flip on
Party Mode, and pick up exactly where you left off after a restart.

![The DJ booth, playing a downloaded track](docs/screenshots/booth.svg)

*Real screenshot: Textual's SVG export of the running app. It shows fictional
demo tracks with generated cover art, and a synthesized audio file played
through the real ffplay engine, so the meters and spectrum are genuine
measurements. The blue/steel colours come from that cover.*

Built with [Textual](https://textual.textualize.io/),
[yt-dlp](https://github.com/yt-dlp/yt-dlp), and FFmpeg's `ffplay`. Launch it
with **`party`**.

---

## What it does

- **DJ booth (default screen).** A deck with cover art, title and artist, an
  elapsed/remaining timeline you can click to seek, and transport buttons. The
  buttons are prev, play/pause, next, shuffle, repeat, like, and save. Each
  does the same thing as its key.
- **Measured audio, never faked.**
  - *Stereo L/R meters* show RMS level with peak hold, from per-channel `astats`.
  - *A 7-band analyzer* (63 Hz – 12 kHz) shows the RMS level of band-pass
    filtered copies of the signal. That is real frequency-resolved data, not
    an FFT spectrum.
  - *A loudness history strip.*

  All three come from the same ffplay process, are timestamped, and are
  matched to the playback clock. Without measurements they say so: NO SIGNAL,
  SILENCE, SIMULATED ENGINE, METERS OFF.
- **Mixer.** Volume, bass, treble (±12 dB), and loudness normalization, all
  applied by FFmpeg filters inside ffplay.
- **Colours from the music.** The theme follows the current track's artwork:
  blue-and-grey covers give electric blue and steel, red/orange covers give
  crimson and amber, black-and-white covers give silver and graphite. Every
  text colour is checked for contrast, and changes crossfade.
- **Party Mode** (`P`). Booth lighting in coordinated neons: ambient drift,
  pulses that follow the *measured* loudness, or a rate-limited strobe.
- **Library.** Playlists (create, rename, delete, reorder, filter, **shuffle**),
  Liked Songs (sort, filter), and Recently Played with play counts.
- **Session recovery.** Quit, press `ctrl+c`, close the terminal, or crash.
  The next start restores the queue in the same order, the same track, view,
  and selections, **paused at the same position**, ready for `space`.
- **Downloads.** An explicit Save action with real progress, cancellation, and
  missing-file handling. Downloaded tracks play **offline** from the local file.
- **Terminal-native.** Full and compact layouts, keyboard-only operation,
  `NO_COLOR` and ASCII fallbacks, reduced effects, and clean exit on `q`,
  `ctrl+c`, and termination signals.

PixelFM has **one deck**. There is no second deck, crossfader, beatmatching,
BPM, or key detection, because the audio engine plays one stream at a time and
PixelFM won't pretend otherwise.

| Search | Queue | Playlists |
| --- | --- | --- |
| ![Search](docs/screenshots/search.svg) | ![Queue](docs/screenshots/queue.svg) | ![Playlists](docs/screenshots/playlists.svg) |
| **Liked** | **Downloads** | **Recently played** |
| ![Liked](docs/screenshots/liked.svg) | ![Downloads](docs/screenshots/downloads.svg) | ![Recent](docs/screenshots/recent.svg) |
| **Settings** | **Compact layout (92×28)** | **Party Mode, compact** |
| ![Settings](docs/screenshots/settings.svg) | ![Compact booth](docs/screenshots/compact-booth.svg) | ![Compact party](docs/screenshots/compact-party.svg) |
| **Party Mode (ambient)** | **A shuffled playlist (teal cover)** | |
| ![Party Mode](docs/screenshots/party.svg) | ![Shuffled playlist](docs/screenshots/booth-shuffled.svg) | |

---

## Install

### Prerequisites (macOS or Linux)

| Tool | Why | macOS | Debian / Ubuntu |
| --- | --- | --- | --- |
| Python **3.12+** with `venv` | runs PixelFM | `brew install python@3.13` | `sudo apt install python3.12 python3.12-venv` |
| FFmpeg (`ffplay`) | plays audio | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| deno (or node/bun) | yt-dlp needs a JS runtime for YouTube | `brew install deno` | see [deno.land](https://deno.land) |
| Node.js **18+** | only for the npm install | `brew install node` | your distro or nodejs.org |

PixelFM never installs system software or runs privileged commands. It tells
you what is missing (`party --check`, and a checklist on first launch).

### Recommended: npm

```bash
npm install -g pixelfm
party
```

This is **not** a fully self-contained installer. Here is exactly what
happens:

1. `npm install -g pixelfm` installs a small Node.js launcher plus the PixelFM
   Python wheel. The package has no npm dependencies and runs no install
   scripts.
2. The **first** `party` finds Python 3.12+ on your PATH (3.13, then 3.12, then
   3.14, then `python3`; override with `PIXELFM_PYTHON=/path/to/python`). It
   creates a private virtual environment and installs the bundled wheel with
   the **exact dependency versions** pinned in `python/requirements.lock` from
   PyPI. This takes about 20–60 s, needs internet once, and never touches your
   system Python or any other virtualenv.
3. Later runs start immediately. A new version of the package rebuilds the
   environment automatically.

The private environment lives in `~/Library/Application Support/PixelFM/runtime`
on macOS and `~/.local/share/pixelfm/runtime` on Linux (override with
`PIXELFM_RUNTIME_DIR`).

Launcher-only commands:

```bash
party --runtime-info       # where the environment is, Python version, ffplay
party --update-ytdlp       # update yt-dlp inside PixelFM's environment
party --reinstall-runtime  # rebuild the environment from the bundled wheel
```

If `party` is "command not found", npm's global bin folder isn't on your PATH.
`npm prefix -g` shows the prefix; add `<prefix>/bin` to `PATH` in `~/.zshrc`
yourself. PixelFM doesn't edit shell files.

> The npm package name `pixelfm` was checked on the public registry (no package
> by that name or a punctuation variant existed) on 2026-10-17. It has not been
> published yet.

### Alternative: pipx (Python only)

```bash
pipx install /path/to/ytm   # from a source checkout
party
```

### Upgrade

```bash
npm install -g pixelfm@latest   # the private environment rebuilds itself on next run
party --update-ytdlp            # when YouTube changes break playback, update yt-dlp first
```

Up to 2.1.0 the command was called `ytm`; from 2.1.1 it is `party`. Upgrading
removes the old `ytm` command.

Your library, downloads, and settings are kept across upgrades. Database
upgrades write a backup first (see [Your data](#your-data)).

### Uninstall

```bash
npm uninstall -g pixelfm
rm -rf ~/Library/Application\ Support/PixelFM/runtime   # macOS: the private environment
rm -rf ~/.local/share/pixelfm/runtime                   # Linux
```

Your library, downloads, caches, and logs are **not** deleted. Remove the
folders under [Your data](#your-data) if you want a clean slate.

---

## Usage

```bash
party                    # the DJ booth
party --mock             # offline sample catalogue, simulated engine (labelled as such)
party --art glyph        # artwork style: color | mono | glyph
party --layout compact   # auto | full | compact
party --effects reduced  # auto | full | reduced
party --no-color         # monochrome (same as NO_COLOR=1)
party --check            # dependency check and every storage path
party --version
```

Press **2** (or **/**) to search, **enter** to play, **1** to come back to the
booth, and **?** for every key.

## Navigation

| Key | View | |
| --- | --- | --- |
| `1` | **DJ Booth** | deck, signal meters, mixer, queue panel (default) |
| `2` | **Search** | YouTube search and results |
| `3` | **Queue** | everything queued: played (dim), playing (`▶`), upcoming |
| `4` | **Playlists** | your playlists (left) and their tracks (right) |
| `5` | **Liked** | liked songs, sortable |
| `6` | **Downloads** | saved files with sizes, status, and storage info |
| `7` | **Recent** | recently played with play counts |
| `8` | **Settings** | theme, party mode, layout, effects, meters, artwork, downloads folder |

Switching views never interrupts playback. The tab strip and header show where
you are. Outside the booth, a mini player stays at the bottom.

Markers in track lists: `▶` playing, `♥` liked, `↓` downloaded, `≡` queued,
`✕` unavailable last time. In ASCII mode they are `>`, `*`, `v`, `+`, and `x`.

## Keyboard shortcuts

Shortcuts never fire while you type in a text box; press `esc` first. `tab`
moves focus between buttons, faders, and lists, so everything works without a
mouse. The mouse also works: click buttons, the timeline, and faders.

**Playback and deck**

| Key | Action |
| --- | --- |
| `space` | play / pause; resumes a restored session; retries after an error |
| `enter` | play the highlighted track |
| `n` / `b` | next / previous (`b` restarts the track if more than 3 s in) |
| `x` | stop |
| `]` / `[` | seek ±10 s (or click the timeline; `←`/`→` when it is focused) |
| `r` / `z` | repeat off → all → one / shuffle mode for the upcoming queue (on/off) |
| `Z` | **shuffle playlist**: play the highlighted playlist (or the one the queue came from) once, in a fresh random order |
| `f` | like / unlike (the highlighted track, else the deck's) |
| `D` | download (asks you to confirm your rights the first time) |
| `a` / `A` / `p` | add to queue / play next / add to a playlist |

**Mixer**

| Key | Action |
| --- | --- |
| `+` `=` / `-` | volume ±5 |
| `}` / `{` | bass ±2 dB |
| `)` / `(` | treble ±2 dB |
| `N` | loudness normalization on/off |
| faders | `tab` to focus, then `←`/`→` or click |

**Lists**

| View | Keys |
| --- | --- |
| Queue (and the booth's queue panel) | `d` remove · `K`/`J` move · `C` clear (asks first) |
| Playlists, left | `enter` play all · `Z` shuffle · `A` append · `c` new · `e` rename · `d` delete (asks first) · `l` tracks |
| Playlists, right | `enter` play from here · `Z` shuffle · `d` remove · `K`/`J` reorder · `h` back |
| Liked | `enter` play · `d` unlike · `o` sort · `A` append all |
| Downloads | `enter` play · `d` delete file (asks first) · `L` relink · `X` clean missing · `c` cancel download |
| Recent | `enter` play · `A` append all |
| any list | `ctrl+f` filter · `esc` clear filter / back |

**Display and app:** `P` Party Mode on/off · `m` artwork style · `v`
full/compact layout · `V` meters on/off · `?` help · `q`, `ctrl+c`, or `ctrl+q`
quit (the session is saved first).

---

## The booth in detail

**Deck states.** The deck shows one of:
- **BUFFERING**, **RESOLVING STREAM**, **SEEKING**, or **APPLYING EFFECTS**:
  ffplay is (re)starting.
- **PLAYING**, **PAUSED**, **STOPPED**, or **COMPLETED**.
- **FAILED**, with the reason, and *space to retry* when retrying can help.
- **PAUSED · restored at m:ss**: last session's track, loaded but not playing
  (plus *was playing when PixelFM closed* if it was).

Long titles wrap to two lines, then truncate.

**Signal meters.** All levels come from ffplay's own filter graph, measured on
the audio being played, after the EQ and before the output volume
("pre-fader"):

| Display | Source | Rate |
| --- | --- | --- |
| L/R meters | `astats` per-channel RMS and peak level | 20 per second |
| 7-band analyzer | RMS in seven band-pass filters (63, 160, 400 Hz, 1, 2.5, 6.3, 12 kHz; ~1.3 octaves wide) | 10 per second |
| `lvl` strip | mean RMS of both channels over time | 20 per second |

ffplay decodes about half a second ahead, so each block carries its
presentation timestamp. PixelFM shows the block that is audible now. Fall-off
and peak hold are display smoothing applied to those real values. The UI
animates at 15 fps only while playing (or loading), and stops otherwise. The
cost is a few percent of one CPU core inside ffplay.

**Mixer.** ffplay has no runtime control channel. Volume, bass, treble, and
normalization are fixed when ffplay starts, so changing them restarts the
stream at the current position after a short debounce. Expect a brief
rebuffer, typically 0.3–1 s. Pause and seek work the same way: pause stops
ffplay and remembers the position, because `SIGSTOP` was tested and makes
ffplay skip audio.

**Cover art.** Terminals draw text cells, so the cover is drawn with
quadrant block characters: every cell shows 2×2 image pixels, using the two
colours that best fit them, after smooth (Lanczos) downscaling. That is twice
the detail of the usual half-block approach, with no scanline striping. On a
terminal 40+ rows tall the booth shows the cover at 32×16 cells (64×32 image
pixels). `m` switches to mono or glyph styles. Showing the actual photo would
need a terminal graphics protocol (Kitty, Ghostty, WezTerm, iTerm2); macOS
Terminal.app has none, so PixelFM does not use one yet.

**Colours from the artwork.** With **Theme: music** (the default), PixelFM
extracts a palette from the current track's thumbnail:
- It reduces a small copy of the image to 12 colours, picks the most vivid,
  well-represented one as the main accent, and a second accent with a
  different hue. Grey in the artwork gives a steel/slate second accent; no
  second colour gives a neighbouring hue; almost no colour at all gives
  silver, white, and graphite.
- Backgrounds and panels are near-black shades of the accent hue; borders,
  selection, and muted text get their own roles. Every text colour is
  lightened until it reaches WCAG contrast 4.5:1 on the background (body text
  12:1).
- Extraction runs in a worker thread and is cached per artwork, so the UI
  never waits. A new track crossfades in about a second (instantly with
  reduced effects). No artwork, or unreadable artwork, gives the default cyan
  and magenta theme. `NO_COLOR` turns colour off entirely.
- To keep a fixed look, pick another theme in **Settings**: `pixelfm`
  (cyan/magenta), `pixelfm-phosphor` (the 1.x green), or a Textual theme.

**Party Mode.** `P` (or the **PARTY MODE** button in the header) switches the
booth's lighting on and off instantly; it never touches the queue, the track,
or playback. Pick the style in **Settings → Party mode**:

| Style | What it does |
| --- | --- |
| `ambient` (default) | Slow colour drift across the panel borders and a light strip, cycling cyan, magenta, violet, electric blue, red, and amber in scenes with different patterns (sweep, chase, split, breathe). |
| `pulse` | Brightness follows the **measured** loudness from the meters. With nothing measured (paused, `--mock`) the lights stay steady and the header says **NO SIGNAL**. |
| `strobe` | Brief colour swaps on loudness onsets, at most **2 per second**; without measurements, a calm swap once a second. |

Safety limits, enforced in code: at most 8 lighting frames per second; only
borders, the light strip, the timeline, and the meters animate; the whole
theme changes at most once every 2 seconds; backgrounds never flash and no
light is brighter than the neon colours themselves (no white). Party Mode is
unavailable with reduced effects (including macOS *Reduce motion*) or
`NO_COLOR`. It is remembered between sessions; `P` turns it off at once.

---

## Library

**Playlists.**
- Create from the Playlists view (`c`) or from any track (`p` → "+ New
  playlist…").
- Names are unique (case-insensitive), and a track appears once per playlist;
  duplicate adds are refused with a message.
- Reorder with `K`/`J` (clear the filter first).
- Deleting a playlist asks first and **never** deletes liked songs or
  downloaded files.

**Shuffle a playlist** (`Z`, or **⇄ SHUFFLE** in the Playlists view and in the
booth's queue panel). It plays the playlist once in a fresh random order:
- **Algorithm.** An unbiased Fisher–Yates shuffle. Every track appears exactly
  once, the saved playlist order is **never** changed, and a seeded random
  source makes it testable.
- **Variety.** When another order exists, it never starts with the track that
  is playing (no back-to-back repeat), never returns the playlist's own order,
  and doesn't repeat the previous shuffle. Orders that break a rule are
  redrawn, so the result stays uniformly random among the allowed orders.
- **Queue.** The shuffled tracks go right after the current entry and start
  immediately. Upcoming copies of the same tracks are dropped, so nothing is
  queued twice; other tracks you queued stay after the shuffled block. `n`
  and `b` walk the shuffled order; the old track becomes history.
- **Unavailable tracks.** Tracks that were unavailable last time are skipped,
  and the status line says how many. An empty playlist, or one whose tracks
  are all unavailable, explains why nothing plays. A single track just plays.
- **When tracks finish.** Playback continues through the shuffled order, then
  any other queued tracks. Repeat **all** loops the queue *in the same
  shuffled order* (so `b` stays predictable), and repeat **one** repeats the
  current track. Press `Z` again for a new order.
- **Global shuffle (`z`) is separate.** It shuffles whatever is upcoming while
  it is on, and turning it off restores the queue order, in which a shuffled
  playlist keeps its shuffled order. `Z` never switches `z` on or off.

**Liked songs.** Liking is independent of downloading: unliking keeps a
download, and deleting a download keeps the like. Sort by date added, title,
or artist (`o`).

**Recently played.**
- A play is counted once, when audio actually starts. Pause/resume, seek, and
  EQ changes don't add plays.
- Counts are kept per track. The history log keeps the newest 1,000 plays.

### Session recovery

**What is saved.**
- The queue in exact order (stable YouTube ids, never search-result rows) and
  which entry is current.
- The playback position, duration, whether it was playing or paused, and
  whether it was a local file.
- The view you were on, the selected queue row, the highlighted playlist and
  track, and the playlist the queue came from (and whether it was shuffled).
- Volume, EQ, repeat, shuffle, theme, and Party Mode are settings, saved the
  moment they change.

**When.**
- Within half a second of a change (queue edits, track changes, pause/resume,
  seeking, view and selection changes).
- At least every 5 seconds while playing.
- On exit: `q`, `ctrl+c`, closing the terminal (SIGHUP), and SIGTERM. In each
  case the position is read *before* ffplay is stopped.
- A last-chance save in the crash handler. Unchanged snapshots aren't
  rewritten.

**How.** The queue and the snapshot are written in **one SQLite transaction**
(table `session`, separate from playlists), so an interrupted write leaves the
previous snapshot intact. A snapshot that is unreadable, from a newer PixelFM,
or for a different track than the saved queue's current entry is ignored
field by field: the queue survives, and a stale position starts that track
from 0:00 rather than from the wrong place.

**On the next start** the queue, current entry, view, and selections come
back; the deck shows the track **PAUSED · restored at 1:17** and nothing plays
until you press `space`. Play reopens the source *at* the saved position:
- a downloaded file is played locally from there;
- a YouTube track gets a fresh stream URL and ffplay starts at that time.

If the source is gone or the network is down, the queue and position are
kept, and the status line says what happened (`space` retries, `n` skips, `3`
edits the queue). A missing downloaded file falls back to streaming.

**What `ctrl+c` does.** Inside PixelFM it is a key, not a crash: it saves the
session, stops ffplay (and its watchdog), restores the terminal, and exits
with status 0, with no traceback. A `ctrl+c` while PixelFM is still starting
or already closing exits quietly with status 130 after a last save.

### Downloads

- **Rights.** Press `D` (or **↓ SAVE**) on a track you **own or are
  authorized to download**. The first time, PixelFM explains this and asks you
  to confirm. YouTube's terms generally don't allow downloading without
  permission.
- **Restrictions.** PixelFM does not bypass DRM, sign-in, age, region, or other
  access restrictions; yt-dlp never selects DRM-protected formats. Live
  streams and tracks of unknown length can't be downloaded.
- **Progress.** Downloads run one at a time in the background with real
  progress (bytes or %), shown in the deck, the Downloads view, and the status
  bar. `c` cancels.
- **Integrity.** Files are written as `<name>.part` and renamed only when
  complete, so an interrupted or cancelled download is never marked finished;
  its partial file is removed.
- **No overwrites.** Files are named `<channel> - <title> [<id>].m4a`, with
  PixelFM's stable track id. Nothing is ever overwritten.
- **Location.** `~/Music/PixelFM` by default. Change it in **Settings → Downloads
  folder**, with `downloads_dir` in the config, or with `PIXELFM_DOWNLOADS_DIR`.
- **Offline.** A downloaded track plays from the local file without internet
  (the header shows **LOCAL FILE**).
- **Missing files.** Files deleted or moved outside PixelFM are detected,
  marked **MISSING**, and streamed instead. Relink one with `L`, or remove
  stale entries with `X`.
- **Storage.** The Downloads view shows the file count, total size, free disk
  space, and folder.

---

## Your data

Everything is local; no account or cloud service is involved.

| What | macOS | Linux |
| --- | --- | --- |
| Library (likes, playlists, history, queue, settings) | `~/Library/Application Support/PixelFM/library.sqlite3` | `~/.local/share/pixelfm/library.sqlite3` |
| Downloaded audio | `~/Music/PixelFM/` | `~/Music/PixelFM/` (or `$XDG_MUSIC_DIR/PixelFM`) |
| npm runtime (Python environment) | `~/Library/Application Support/PixelFM/runtime/` | `~/.local/share/pixelfm/runtime/` |
| Thumbnail cache (disposable) | `~/Library/Caches/PixelFM/` | `~/.cache/pixelfm/` |
| Log | `~/Library/Logs/PixelFM/pixelfm.log` | `~/.local/state/pixelfm/pixelfm.log` |
| Config you edit (optional) | `~/.config/pixelfm/config.toml` | same |

`party --check` prints the exact paths. Overrides: `PIXELFM_DATA_DIR`,
`PIXELFM_DOWNLOADS_DIR`, `PIXELFM_CACHE_DIR`, `PIXELFM_LOG_FILE`,
`PIXELFM_CONFIG`, `PIXELFM_RUNTIME_DIR`.

**What is stored.** The library holds stable YouTube video IDs and display
metadata. It never stores temporary stream URLs; a fresh stream is resolved
each time.

**Upgrades.**
- The schema is versioned. Version 3 (PixelFM 2.0) adds play counts,
  downloads, and the saved queue; version 4 (2.1) adds the session snapshot.
- Before upgrading, PixelFM writes `library.sqlite3.v<old>.bak` next to the
  library, and each migration is one transaction.
- A library from a *newer* PixelFM, or an unreadable file, is never modified;
  PixelFM runs with a temporary in-memory library and says so.

**Backup.** With PixelFM closed, copy `library.sqlite3`. While it is running:
`sqlite3 ~/Library/Application\ Support/PixelFM/library.sqlite3 ".backup '$HOME/pixelfm-backup.sqlite3'"`.
Back up `~/Music/PixelFM` separately for your audio files.

## Configuration

Optional `~/.config/pixelfm/config.toml`. Every key is optional, and an
invalid value falls back to its default with a warning; the rest still apply.
Changes made in the **Settings** view are saved in the library and override the
file. Command-line flags override both for one session.

```toml
theme = "music"          # music (from the artwork) | pixelfm (cyan/magenta) | pixelfm-phosphor (1.x green) | any Textual theme
provider = "youtube"     # or "mock"
volume = 80              # initial volume (the last-used volume is remembered)
ffplay_path = ""         # empty: ffplay on PATH
downloads_dir = ""       # empty: ~/Music/PixelFM
layout = "auto"          # auto | full | compact
effects = "auto"         # auto (follows Reduce motion) | full | reduced
visualizer = true        # meters and spectrum (need full effects)
artwork_mode = "color"   # color | mono | glyph
artwork_width = 16       # cells (the booth shows 2x on terminals 40+ rows tall)
artwork_height = 8
color = "auto"           # auto | truecolor | 256
unicode = "auto"         # auto | always | never
search_limit = 20
search_timeout = 20
```

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `party: PixelFM needs Python 3.12 or newer` | Install Python (table above), or set `PIXELFM_PYTHON=/path/to/python3.12`. |
| "venv/ensurepip missing" | Debian/Ubuntu: `sudo apt install python3.12-venv` (or `python3-venv`). |
| First run fails while installing | It needs internet once. The message shows pip's last lines. Retry, or `party --reinstall-runtime`. |
| `party: command not found` | Add npm's global bin (`$(npm prefix -g)/bin`) to PATH. |
| "ffplay not found" | `brew install ffmpeg` / `sudo apt install ffmpeg`, or set `ffplay_path`. Search works meanwhile. |
| Many tracks fail, "Sign in to confirm", "No playable audio format" | `party --update-ytdlp`. PixelFM never bypasses sign-in, age, or region checks, so some videos can't play. |
| Meters say NO MEASUREMENTS | Your FFmpeg build lacks `astats`/`ametadata`; PixelFM fell back to plain playback (see the log). |
| Meters say SIMULATED ENGINE | You're in `--mock` mode: there is no audio to measure. |
| A download fails | The status bar shows why. Live streams can't be saved, and protected videos fail by design. |
| Downloads show MISSING | The file moved or was deleted. `L` relinks it, `X` removes stale entries. |
| Colours look wrong | Try `color = "256"`, `party --no-color`, or `unicode = "never"`. A fixed theme (Settings → Theme) stops the colours following the artwork. |
| Party Mode won't start | It needs full effects: Settings → Effects → `full` (macOS *Reduce motion* makes `auto` reduced), and no `NO_COLOR`. |
| "Couldn't resume …" after a restart | The video became unavailable or the network is down. The queue is kept: `space` retries, `n` skips. |
| Garbled terminal after `kill -9` | Run `reset`. Normal exits restore the terminal. |
| Anything else | `~/Library/Logs/PixelFM/pixelfm.log`; for more detail `PIXELFM_LOG_LEVEL=DEBUG party`. |

## Known limitations

- **One deck.** There is no second deck, crossfader, beatmatching, BPM, key
  detection, or cue/preview output. The engine plays one stream.
- **Rebuffering.** Pause/resume, seek, volume, and EQ restart ffplay at the
  current position, with a short rebuffer (0.3–1 s).
- **What is measured.** The analyzer shows seven band-pass RMS levels, not an
  FFT spectrum. Meters measure the decoded signal before the output volume;
  they don't measure what your speakers emit, and they don't know the output
  device. The header always says "system default".
- **Audio checked by hand.** During development, playback was verified by
  process, clock, and measured levels with volume at 0. Audible output could
  not be checked automatically. Please run the manual check below on your
  machine.
- **Downloads.** Downloads keep the original audio stream (usually m4a/AAC)
  and don't re-encode or tag files. One download runs at a time.
- **npm install.** It needs Python 3.12+, internet on first run, and
  FFmpeg/deno installed separately. Dependencies are pinned to exact versions
  but are not hash-verified.
- **Database work.** Library queries run on the UI thread; they are small
  SQLite operations. File checks, deletions, and downloads run in background
  threads.
- **Resuming online tracks.** YouTube stream URLs expire, so resuming always
  resolves a new one (1–3 s with the network) and ffplay seeks into it. The
  resume point is accurate to about a second, after a short rebuffer. Videos
  removed or made private since can't resume (the queue is kept). Live
  streams and tracks of unknown length restart at the beginning.
- **Crash recovery.** A save happens at least every 5 s while playing, so a
  power cut or `kill -9` can lose up to about 5 s of position (never the
  queue, which is saved within half a second of each change). Two PixelFM
  windows share one library; the one that saves last wins.
- **Colours.** The palette comes from YouTube's thumbnail, which is not always
  the album cover. Party colours change the theme at most every 2 s, so
  selected rows and text shift slowly while borders and the light strip move
  smoothly.
- **Party Mode and audio.** Pulse and strobe react only to measured loudness
  (no beat or BPM detection). With the simulated engine, or before
  measurements arrive, the lights stay calm.
- **Platforms.** macOS and Linux only. Tested on macOS (Apple Silicon) with
  Python 3.12, 3.13, and 3.14, FFmpeg 8.1, yt-dlp 2026.08.19, Textual 8.2, and
  Node 22. Linux has not been tested on real hardware.

## Manual check: real audio

1. `party --check` shows everything `ok`.
2. Run `party`, press `2`, search `daft punk around the world`, press `enter`,
   then `1`. You hear audio, the deck says PLAYING, the timeline advances in
   step, and the L/R meters and bands move with the music.
3. Press `}` three times. After a short rebuffer the bass is clearly louder,
   and the low bands rise. Press `{` three times to undo.
4. Press `space` to pause, and audio stops; press it again and it resumes from
   the same point. Press `+` and `-` to change the volume.
5. Run `pgrep -lx ffplay` in another terminal. It shows exactly one process
   while playing and none while paused.
6. Press `D` on a track you're allowed to download. Watch the progress, then
   press `6`. Turn Wi-Fi off and play it: the header says LOCAL FILE and the
   audio plays.
7. Queue a few tracks, play one to about a minute, and quit with `ctrl+c`.
   Run `party` again. The same queue appears, the deck shows **PAUSED · restored
   at m:ss**, nothing plays, and `space` resumes from that point.
8. Press `4`, highlight a playlist, and press `Z`. The queue shows the
   playlist in a new order and it starts playing; the playlist itself keeps
   its order. Press `Z` again for another order.
9. Press `P` and watch the booth lights; with `pulse` (Settings) they follow
   the music. Press `P` again: they stop at once.

## Development

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # Python tests (no network, no audio); ~520 tests
ruff format . && ruff check .
npm test                  # launcher tests (node:test; no network)
PIXELFM_TEST_BOOTSTRAP=1 npm test   # + a real first-run setup (needs internet)
npm pack                  # builds the wheel (prepack) and the npm tarball
python -m pixelfm --mock  # run from source with sample data
```

Layout:

```
bin/party.js, lib/launcher.js   npm launcher (private venv, signals, diagnostics)
python/requirements.lock      exact runtime dependency versions for the launcher
src/pixelfm/
  app.py                      Textual app: composition, playback flow, queue
  shuffle.py                  Fisher-Yates playlist shuffle
  ui/                         booth, library views, downloads, settings, theming/party
  widgets/                    booth widgets, track tables, dialogs, now playing, artwork
  services/
    ffplay.py                 FFplayPlayer: subprocess lifecycle, recovery, filters
    metering.py               astats filter graphs and the measurement parser
    downloads.py              DownloadManager and the yt-dlp downloader
    library.py                SQLite library, session snapshot, migrations (v1-v4)
    palette.py, party.py      artwork palettes and Party Mode lighting
    streams.py, youtube.py    yt-dlp search and stream resolution (local-first)
    artwork.py, cache.py      thumbnails, cover renderer, caches
  queue.py, state.py          queue model and playback state machine
tests/                        pytest suite (fakes.py: fake ffplay, resolver, reaper)
test/launcher.test.js         launcher tests
docs/screenshots/             real SVG screenshots used in this README
```

## License

MIT. See [LICENSE](LICENSE). PixelFM is not affiliated with YouTube or Google.
Downloading content is your responsibility: only download audio you own or are
authorized to download.
