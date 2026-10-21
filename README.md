# PixelFM 2.3: a cyberpunk DJ booth for your terminal

PixelFM is a full-screen terminal music player: detailed cover art (the real
picture in terminals that can draw images), an interface that takes its
colours from the artwork of whatever is playing, and real-time meters that
measure the audio you are actually hearing. Search YouTube, import playlists
from Spotify or from screenshots of any song list, build and shuffle
playlists, like and download tracks you're allowed to keep, flip on Party
Mode or RAVE MODE, control it with your Mac's media keys, and pick up exactly
where you left off after a restart.

![The DJ booth, playing a downloaded track](docs/screenshots/booth.svg)

*Real screenshot: Textual's SVG export of the running app. It shows fictional
demo tracks with generated cover art, and a synthesized audio file played
through the real ffplay engine, so the meters and spectrum are genuine
measurements. The blue/steel colours come from that cover.*

Built with [Textual](https://textual.textualize.io/),
[yt-dlp](https://github.com/yt-dlp/yt-dlp), and FFmpeg's `ffplay`. Launch it
with **`pixelfm`**, or its short form **`pfm`** (both do the same).

**New here?** The [Quick start](#quick-start) gets you playing in a few
minutes; the **[user guide](docs/GUIDE.md)** shows how to do everything step
by step. What changed: [CHANGELOG](CHANGELOG.md).

---

## Quick start

**1. Install** (macOS or Linux; Windows isn't supported yet):

```bash
# macOS (with Homebrew from brew.sh)
brew install python@3.13 node
# Debian / Ubuntu
sudo apt install python3.12 python3.12-venv nodejs npm

npm install -g pixelfm
```

**2. Start it:**

```bash
pixelfm        # or: pfm
```

The first start sets up a private Python environment (about a minute) and
offers to install FFmpeg (and, on Linux, Tesseract) if they are missing. It
asks before installing anything.

**3. Play something:** press `2`, type `daft punk around the world`, press
`enter`, pick a result with the arrows, and press `enter` again. Press `1` for
the booth, `space` to pause, `?` for every key, and `q` to quit; the next
start picks up where you left off.

**4. Bring your music:** press `4`, then `I`, and paste a Spotify playlist
link, or drop screenshots of any song list on the terminal. See
[Import a Spotify playlist](docs/GUIDE.md#import-a-spotify-playlist) and
[Import songs from screenshots](docs/GUIDE.md#import-songs-from-screenshots).

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
- **Party Mode** (`P`) and **RAVE MODE** (`R`). Booth lighting in
  coordinated neons, from a slow ambient drift to a fast, screen-wide light
  rig (about 16 fps) whose bass pulse follows the *measured* loudness.
- **Spotify playlist import** (`I`). Paste a playlist link (or an export
  file): PixelFM keeps the track list and order, matches each track to a
  YouTube video, and lets you review every match.
- **Import from screenshots** (`I`). Drop screenshots of any song list
  (Spotify, Apple Music, YouTube Music, a phone) and PixelFM reads the songs
  with your computer's own text recognition, then adds them to a new or an
  existing playlist, skipping the songs that are already in it.
- **macOS media controls.** Play/pause, next, and previous from the media
  keys, headphones, and Control Centre, which also shows the track and cover.
- **Real covers where possible.** Terminals with an image protocol (Kitty,
  Ghostty, WezTerm, iTerm2 3.5+) show the actual cover; others get a
  detailed block rendering.
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
| **Party Mode (ambient)** | **A shuffled playlist (teal cover)** | **RAVE MODE (colour chase)** |
| ![Party Mode](docs/screenshots/party.svg) | ![Shuffled playlist](docs/screenshots/booth-shuffled.svg) | ![RAVE](docs/screenshots/rave.svg) |
| **RAVE across the search view** | **Importing a Spotify playlist** | **Reviewing YouTube matches** |
| ![RAVE search](docs/screenshots/rave-search.svg) | ![Import preview](docs/screenshots/import-preview.svg) | ![Match review](docs/screenshots/match-review.svg) |

| **Screenshot import: check the songs** | **Choose the playlist** | **Added, with repeats skipped** |
| ![Screenshot review](docs/screenshots/screenshot-review.svg) | ![Choose a playlist](docs/screenshots/screenshot-target.svg) | ![Songs added](docs/screenshots/screenshot-added.svg) |

A short recording of normal mode switching to RAVE (real frames of the
running app, demo data): [docs/screenshots/rave-demo.gif](docs/screenshots/rave-demo.gif).

## How to…

Step-by-step instructions for each task are in the
[user guide](docs/GUIDE.md).

| I want to… | Do this | Guide |
| --- | --- | --- |
| Play a song | `2`, type, `enter`, pick a result, `enter` | [Play music](docs/GUIDE.md#play-music) |
| Pause, skip, seek, change volume | `space` · `n`/`b` · `[`/`]` · `+`/`-` | [Play music](docs/GUIDE.md#play-music) |
| Queue songs | `a` add · `A` play next · `3` see the queue | [Use the queue](docs/GUIDE.md#use-the-queue) |
| Like a song | `f` · `5` to see liked songs | [Like songs](docs/GUIDE.md#like-songs) |
| Make a playlist | `p` on a track → **+ New playlist…** | [Make and use playlists](docs/GUIDE.md#make-and-use-playlists) |
| Shuffle a playlist | `4`, highlight it, `Z` | [Make and use playlists](docs/GUIDE.md#make-and-use-playlists) |
| Import a Spotify playlist | `4`, `I`, paste the link, `m` | [Import a Spotify playlist](docs/GUIDE.md#import-a-spotify-playlist) |
| Add songs from a screenshot | `4`, `I`, drop the screenshot, `enter`, check, choose a playlist | [Import songs from screenshots](docs/GUIDE.md#import-songs-from-screenshots) |
| Fix a wrong YouTube match | `4`, highlight the playlist, `M`, `enter` on the track | [Review and fix matches](docs/GUIDE.md#review-and-fix-youtube-matches) |
| Save a track for offline | `D` (only audio you may download) | [Downloads](docs/GUIDE.md#download-tracks-and-play-offline) |
| Light up the screen | `P` Party Mode · `R` RAVE MODE | [Party Mode and RAVE MODE](docs/GUIDE.md#party-mode-and-rave-mode) |
| Change colours, layout, cover style | `8` Settings · `v` layout · `m` cover style | [Change how it looks](docs/GUIDE.md#change-how-it-looks) |
| Use the Mac's media keys | just press them while PixelFM runs | [Media keys](docs/GUIDE.md#control-it-from-your-macs-media-keys) |
| Carry on after quitting | `q`, then `pixelfm` and `space` | [Quit and pick up](docs/GUIDE.md#quit-and-pick-up-where-you-left-off) |
| Fix playback that stopped working | `pixelfm --update-ytdlp` | [Keep it working](docs/GUIDE.md#keep-it-working) |
| Install missing FFmpeg / Tesseract | `pixelfm --install-deps` | [The first start](docs/GUIDE.md#the-first-start) |
| Give PixelFM to a friend | `npm pack`, send the `.tgz` | [Share PixelFM](docs/GUIDE.md#share-pixelfm-with-a-friend) |

---

## Install

### Prerequisites (macOS or Linux)

| Tool | Why | macOS | Debian / Ubuntu |
| --- | --- | --- | --- |
| Python **3.12+** with `venv` | runs PixelFM | `brew install python@3.13` | `sudo apt install python3.12 python3.12-venv` |
| FFmpeg (`ffplay`) | plays audio | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| deno (or node/bun) | yt-dlp needs a JS runtime for YouTube | `brew install deno` | see [deno.land](https://deno.land) |
| Node.js **18+** | only for the npm install | `brew install node` | your distro or nodejs.org |
| Tesseract | reads songs from screenshots (optional) | not needed: built in | `sudo apt install tesseract-ocr` |

**The npm launcher offers to install FFmpeg (and, on Linux, Tesseract).**
On each start it checks for them, and if one is missing it says what it is
for and asks before running your system's package manager:

| System | What it checks | Installs with |
| --- | --- | --- |
| macOS | FFmpeg | Homebrew (`brew install ffmpeg`), or MacPorts |
| Linux | FFmpeg, Tesseract | apt, dnf, pacman, zypper, or apk (`sudo` asks for your password) |
| Windows | nothing: PixelFM doesn't run on Windows yet (it has its own text recognition) | |

It never installs without a yes, never without a terminal (it prints the
command instead), and never installs a package manager: on a Mac without
Homebrew it points you to [brew.sh](https://brew.sh). A "no" is remembered
until the next PixelFM version; `pixelfm --install-deps` asks again, and
`PIXELFM_NO_INSTALL=1` turns the question off. FFmpeg isn't bundled in the
npm package because ffplay needs the system's audio and SDL libraries (no
dependable static build exists for Linux) and would add about 80 MB.

Python and the app itself never install system software; `pixelfm --check`
and the first-launch checklist show what is missing, with the command for
your system.

### Recommended: npm

```bash
npm install -g pixelfm
pixelfm
```

This is **not** a fully self-contained installer. Here is exactly what
happens:

1. `npm install -g pixelfm` installs a small Node.js launcher plus the PixelFM
   Python wheel. The package has no npm dependencies and runs no install
   scripts.
2. The **first** `pixelfm` finds Python 3.12+ on your PATH (3.13, then 3.12, then
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
pixelfm --runtime-info       # where the environment is, Python version, ffplay
pixelfm --install-deps       # check FFmpeg (and Tesseract on Linux); offer to install
pixelfm --update-ytdlp       # update yt-dlp inside PixelFM's environment
pixelfm --reinstall-runtime  # rebuild the environment from the bundled wheel
```

If `pixelfm` is "command not found", npm's global bin folder isn't on your PATH.
`npm prefix -g` shows the prefix; add `<prefix>/bin` to `PATH` in `~/.zshrc`
yourself. PixelFM doesn't edit shell files.

> The npm package name `pixelfm` was checked on the public registry (no package
> by that name or a punctuation variant existed) on 2026-10-17. It has not been
> published yet.

### Alternative: pipx (Python only)

```bash
pipx install /path/to/ytm   # from a source checkout
pixelfm
```

### Upgrade

```bash
npm install -g pixelfm@latest   # the private environment rebuilds itself on next run
pixelfm --update-ytdlp          # when YouTube changes break playback, update yt-dlp first
```

The command was called `ytm` up to 2.1.0 and `party` from 2.1.1; from 2.3.0
it is `pixelfm`, with `pfm` as a short form. Upgrading removes the old
command.

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
pixelfm                    # the DJ booth
pixelfm --mock             # offline sample catalogue, simulated engine (labelled as such)
pixelfm --art glyph        # artwork style: color | mono | glyph
pixelfm --layout compact   # auto | full | compact
pixelfm --effects reduced  # auto | full | reduced
pixelfm --no-color         # monochrome (same as NO_COLOR=1)
pixelfm --images off       # character covers only (auto | off | sixel | kitty)
pixelfm --media-test       # macOS: check media keys / Control Centre for 60 s
pixelfm --check            # dependency check and every storage path
pixelfm --version
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
| `8` | **Settings** | theme, party/rave mode, layout, effects, meters, artwork, Spotify, downloads folder |

Switching views never interrupts playback. The tab strip and header show where
you are. Outside the booth, a mini player stays at the bottom.

Markers in track lists: `▶` playing, `♥` liked, `↓` downloaded, `≡` queued,
`✕` unavailable last time (or, in an imported playlist, not matched to a
YouTube video yet). In ASCII mode they are `>`, `*`, `v`, `+`, and `x`.

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
| Playlists, left | `enter` play all · `Z` shuffle · `I` import · `M` review matches · `X` stop matching · `A` append · `c` new · `e` rename · `d` delete (asks first) · `l` tracks |
| Playlists, right | `enter` play from here · `Z` shuffle · `M` review matches · `d` remove · `K`/`J` reorder · `h` back |
| Liked | `enter` play · `d` unlike · `o` sort · `A` append all |
| Downloads | `enter` play · `d` delete file (asks first) · `L` relink · `X` clean missing · `c` cancel download |
| Recent | `enter` play · `A` append all |
| any list | `ctrl+f` filter · `esc` clear filter / back |

**Display and app:** `P` Party Mode on/off · `R` RAVE MODE on, then next
pattern · `I` import a playlist · `m` artwork style · `v`
full/compact layout · `V` meters on/off · `y` lyrics on/off · `?` help · `q`, `ctrl+c`, or `ctrl+q`
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

**Cover art.**
- **The real picture** in terminals with an image protocol: the Kitty
  graphics protocol (Kitty, Ghostty, WezTerm) or Sixel (iTerm2 3.5+, WezTerm,
  foot, VS Code with `terminal.integrated.enableImages`). PixelFM asks the
  terminal at start-up (via `textual-image`) and uses whatever it supports.
- **Everywhere else**, including macOS Terminal.app (which can only draw
  characters), the cover is drawn with quadrant blocks: every cell shows 2×2
  image pixels in the two colours that fit them best, after smooth (Lanczos)
  downscaling, with no scanline striping.
- On a terminal 40+ rows tall the booth shows the cover at 32×16 cells. `m`
  switches to mono or glyph styles (characters, even in image terminals)
  until you quit; PixelFM always starts in colour unless `artwork_mode` in
  the config file says otherwise.
  `--images off` (or `PIXELFM_GRAPHICS=off`) always uses characters; for a
  terminal that supports images but doesn't say so, try `--images kitty` or
  `--images sixel`.

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

**RAVE MODE.** `R` turns the whole terminal into a light rig; `R` again
switches pattern, `P` turns it off at once. It lights the header (a full-width
light strip, the logo), the tabs, every panel border in the visible view, the
progress bar, the meters, the footer, and (once a second) the theme accents
used by selected rows and focused controls. All regions share one clock with
offset positions, so the light travels around the screen as one rig.

| Pattern | What it does |
| --- | --- |
| `auto` (default) | Plays each pattern for 8 s, with a coordinated sweep across the UI between them. |
| `chase` | COLOR CHASE: neon colours travel around the panels, each region a step behind the last. |
| `dance` | DANCE FLOOR: alternating cyan/magenta and blue/violet; at most 3 swaps a second. |
| `laser` | LASER SWEEP: a narrow beam runs across the strip, the logo, and the panels. |
| `bass` | BASS PULSE: brightness follows the **measured** loudness. Without measurements the header says **NO SIGNAL** and the lights don't pulse. There is no beat or BPM detection. |
| `rainbow` | RAINBOW CIRCUIT: every region cycles the whole neon palette, offset in time. |

Speed and cost (measured here, 140×42 terminal): the rig runs at about 14–16
frames a second, borders change colour 8 times a second (the deck 2 times,
because repainting it redraws the cover), and theme accents once a second.
That costs roughly a fifth of one CPU core and ~450 KB/s of terminal output,
against ~25 KB/s in normal mode. Nothing heavy runs per frame: no artwork
processing, database writes, or layout passes. With a Sixel cover on screen
the deck border and theme accents stay still, so the image is never re-sent.
Like Party Mode, RAVE is unavailable with reduced effects or `NO_COLOR`.

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

### Importing playlists (Spotify)

Press `I` (or **⤓ IMPORT** in the Playlists view) and paste a Spotify
playlist link, or the path to an export file. PixelFM shows a preview (name,
owner, track count, every track), then **Import** (save it) or **Import and
match** (save it, then find each track on YouTube in the background).

**What Spotify allows** (verified against Spotify's documentation, October
2026):
- Personal apps read playlists through the Spotify Web API in *Development
  Mode*. Each PixelFM user creates their own free Spotify developer app; its
  **owner needs Spotify Premium**, and it can have up to 5 users.
- Since March 2026 Spotify gives such apps the **track list only of
  playlists you own or collaborate on**. For anyone else's playlist (a
  friend's, Spotify's own) it returns only the name and owner, and PixelFM
  says so instead of trying to work around it. To import one anyway: in
  Spotify, open it, select all tracks, **Add to playlist → New playlist**, and
  import your copy.
- Wider access (Extended Quota Mode) is only for organisations with 250k+
  monthly users, so PixelFM can't offer it.

**One-time set-up** (PixelFM walks you through it on the first import):
1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
   and **Create app** (any name; API: Web API).
2. Add the redirect URI `http://127.0.0.1:8898/callback` exactly.
3. Copy the app's **Client ID** into PixelFM. No client secret is needed: the
   sign-in uses PKCE.
4. On import PixelFM opens your browser at spotify.com to sign in and approve
   read access to your playlists (`playlist-read-private`,
   `playlist-read-collaborative`). It **never sees your password**; the
   tokens go to the macOS Keychain. Settings shows the state and signs out.
   You can also set `PIXELFM_SPOTIFY_CLIENT_ID`.

**Export files instead** (no developer app needed):
- Spotify's own data download (Spotify Account → Privacy → *Download your
  data*): import `Playlist1.json`; you choose which playlist.
- A CSV with a header row: Exportify-style columns (`Track Name`,
  `Artist Name(s)`, `Album Name`, `Duration (ms)`, `Track URI`, `Explicit`)
  or simply `title`, `artist`, `album`, `duration`.

**What is kept.** Title, every artist, album, duration, album artwork, the
Spotify track id, the explicit flag, and the original position. Nothing is
silently dropped:
- Removed tracks are listed as *unavailable*.
- Podcast episodes are listed as *skipped*.
- A track that appears twice is listed as a *duplicate*: a PixelFM playlist
  holds each track once.
- Local files from your Spotify app are kept and matched by title and artist.
- The summary always shows the matched, unmatched, and skipped counts.
- If the name is already taken, the playlist is saved as "Name (2)"; nothing
  is overwritten.

**Matching on YouTube.** Spotify gives metadata, not audio. Each track is
searched as "artist - title" through PixelFM's normal YouTube search, two at
a time with a pause between, so a big playlist never floods YouTube. Each
candidate is scored on:
- the title words and artist names;
- the length, as a secondary signal;
- official uploads (the "Artist - Topic" studio recordings, the artist's own
  channel, VEVO);
- a strong penalty for other versions, unless the Spotify title asks for one
  ("Live", "Remix"): lyric videos, live, remixes, sped-up, slowed, covers,
  loops, and the like.

The best candidate is used if it is **strong** (80%+) or **likely**
(62%+, marked for review). Anything weaker stays **unmatched**: it keeps its
place in the playlist (marked `✕`) and is never played. Matches are guesses,
and the review screen shows each one's score and reasons.

**Review and fix** (`M`): every entry, unsure and unmatched first.
- `enter` searches YouTube for that track (edit the search and press enter
  again) and lets you pick a candidate or leave it unmatched.
- `u` unmatches an entry.
- `a` matches everything not matched yet.

Matching can be stopped with `X` (matches so far are kept) and resumed with
`M` → `a`. Nothing is downloaded during an import.

**Afterwards** an imported playlist is an ordinary PixelFM playlist: play
all, shuffle, queue, like, rename, delete, with its order and session
recovery as usual. Played tracks show the Spotify title, artists, album, and
album cover (which also drives the artwork colours).

### Importing from screenshots

Press `I` and drop one or more screenshots of a song list on the terminal
(or paste their paths). To use a screenshot on the clipboard (`⌃⇧⌘4` on a
Mac), leave the field empty and press enter.

1. **Reading.** PixelFM reads the text with the computer's own engine:
   Apple's Vision framework on macOS (built in, nothing to install), and
   Tesseract on Linux (the npm launcher offers to install it). The image
   never leaves your computer, and no AI service or account is involved.
   It recognises the usual layouts: the title with the artist underneath
   (Spotify, phones) or in the next column (Apple Music, YouTube Music).
   Headers, track numbers, dates, play counts, the explicit badge, and the
   playlist's own heading are left out; durations are kept when readable.
   Overlapping screenshots of one long list are merged without repeats.
2. **Check.** Every song read is listed before anything is saved: `space`
   leaves out a misread row, `e` fixes one (`Title — Artist`).
3. **Where to.** Choose **+ New playlist** (named after the screenshot's
   heading; you can change it) or an existing playlist. Songs that are
   already in that playlist are skipped: by title and artist (also when the
   entry is a YouTube video such as "Artist - Song (Official Video)"), and
   again after matching if a song's YouTube match is already in it.
4. **Matching** works exactly as for Spotify imports, including the review
   with `M`. The playlist's heading counts the matched and skipped songs.

Windows has its own text recognition too, but PixelFM doesn't run on
Windows yet, so there is no Windows reader.

### macOS media controls

While PixelFM runs on a Mac, these control it even when its terminal is in
the background:
- the keyboard's play/pause, next, and previous keys;
- headphone buttons;
- Control Centre and the menu-bar Now Playing item, which also show the
  title, artist, album, cover, length, and position.

**How it works.** A terminal program isn't a macOS media app, so PixelFM
ships a small native helper, `PixelFMMedia.app`:
- It is ~230 KB of Swift, a universal binary for Apple Silicon and Intel,
  with no Dock icon.
- It publishes `MPNowPlayingInfoCenter` metadata and receives
  `MPRemoteCommandCenter` commands.
- It talks to PixelFM over a pipe (JSON lines). Commands run the same actions
  as PixelFM's own keys, so there is one playback state and the queue order
  (including shuffle) is respected.
- Repeated key events within 0.25 s count once.
- The helper lives exactly as long as PixelFM: when PixelFM quits (or
  crashes) the helper clears Now Playing and exits.
- Nothing runs in the background afterwards. When PixelFM isn't running the
  keys go to whatever app macOS picks (often Apple Music).
- Needs macOS 12+. The helper is ad-hoc signed and built during packaging, so
  users never compile anything.

**Testing it on your Mac:** `pixelfm --media-test` makes PixelFM the Now
Playing item for 60 s and prints every command it receives. Press the keys
and use Control Centre's buttons.

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

Everything is local. The only account PixelFM ever uses is your own Spotify
sign-in, and only if you import a playlist from a link. Screenshots are read
on your computer and not kept.

| What | macOS | Linux |
| --- | --- | --- |
| Library (likes, playlists, history, queue, settings) | `~/Library/Application Support/PixelFM/library.sqlite3` | `~/.local/share/pixelfm/library.sqlite3` |
| Downloaded audio | `~/Music/PixelFM/` | `~/Music/PixelFM/` (or `$XDG_MUSIC_DIR/PixelFM`) |
| npm runtime (Python environment) | `~/Library/Application Support/PixelFM/runtime/` | `~/.local/share/pixelfm/runtime/` |
| Thumbnail cache (disposable) | `~/Library/Caches/PixelFM/` | `~/.cache/pixelfm/` |
| Log | `~/Library/Logs/PixelFM/pixelfm.log` | `~/.local/state/pixelfm/pixelfm.log` |
| Config you edit (optional) | `~/.config/pixelfm/config.toml` | same |
| Spotify sign-in tokens | macOS Keychain ("PixelFM Spotify") | Secret Service (`secret-tool`), else a 0600 file |
| Now Playing cover copies (disposable) | `~/Library/Caches/PixelFM/nowplaying/` | `~/.cache/pixelfm/nowplaying/` |

`pixelfm --check` prints the exact paths. Overrides: `PIXELFM_DATA_DIR`,
`PIXELFM_DOWNLOADS_DIR`, `PIXELFM_CACHE_DIR`, `PIXELFM_LOG_FILE`,
`PIXELFM_CONFIG`, `PIXELFM_RUNTIME_DIR`.

**What is stored.** The library holds stable YouTube video IDs and display
metadata. It never stores temporary stream URLs; a fresh stream is resolved
each time.

**Upgrades.**
- The schema is versioned. Version 3 (PixelFM 2.0) adds play counts,
  downloads, and the saved queue; version 4 (2.1) adds the session snapshot;
  version 5 (2.3) adds imported playlists (`imports`, `import_items`), and
  version 6 (2.3) records which addition each imported entry came with
  (`import_items.batch`), for screenshots added to existing playlists.
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
| `pixelfm: PixelFM needs Python 3.12 or newer` | Install Python (table above), or set `PIXELFM_PYTHON=/path/to/python3.12`. |
| "venv/ensurepip missing" | Debian/Ubuntu: `sudo apt install python3.12-venv` (or `python3-venv`). |
| First run fails while installing | It needs internet once. The message shows pip's last lines. Retry, or `pixelfm --reinstall-runtime`. |
| `pixelfm: command not found` | Add npm's global bin (`$(npm prefix -g)/bin`) to PATH. |
| "ffplay not found" | `pixelfm --install-deps`, or `brew install ffmpeg` / `sudo apt install ffmpeg`, or set `ffplay_path`. Search works meanwhile. |
| Screenshot: "needs Tesseract" (Linux) | `pixelfm --install-deps`, or `sudo apt install tesseract-ocr`. |
| Screenshot: "No songs found" or rows misread | Crop to the song list, use a larger window or zoom, and check the rows (`e` fixes one). Very small or blurry text can't be read. |
| Many tracks fail, "Sign in to confirm", "No playable audio format" | `pixelfm --update-ytdlp`. PixelFM never bypasses sign-in, age, or region checks, so some videos can't play. |
| Meters say NO MEASUREMENTS | Your FFmpeg build lacks `astats`/`ametadata`; PixelFM fell back to plain playback (see the log). |
| Meters say SIMULATED ENGINE | You're in `--mock` mode: there is no audio to measure. |
| A download fails | The status bar shows why. Live streams can't be saved, and protected videos fail by design. |
| Downloads show MISSING | The file moved or was deleted. `L` relinks it, `X` removes stale entries. |
| Colours look wrong | Try `color = "256"`, `pixelfm --no-color`, or `unicode = "never"`. A fixed theme (Settings → Theme) stops the colours following the artwork. |
| Party Mode won't start | It needs full effects: Settings → Effects → `full` (macOS *Reduce motion* makes `auto` reduced), and no `NO_COLOR`. |
| Spotify: "only gives the track list of playlists you own" | A Spotify rule for personal apps. Copy the playlist into one you own (select all → Add to playlist → New playlist), or import an export file. |
| Spotify: "owner … needs an active Premium subscription" | Your Spotify developer app's owner needs Premium. |
| Spotify: "isn't on your developer app's user list" | Add your account under User Management in the Spotify dashboard. |
| Spotify sign-in never comes back | The redirect URI in your Spotify app must be exactly `http://127.0.0.1:8898/callback`, and that port must be free. |
| Many tracks unmatched | Press `M`, pick a track, and edit the search (e.g. add the album or "official audio"). |
| Media keys don't reach PixelFM | Run `pixelfm --media-test`. If nothing is received, another app is Now Playing: pause it, or press play in PixelFM once. A source checkout needs `npm run build:helper`. |
| The cover is blocky | Your terminal draws only characters (macOS Terminal.app does). Real covers need Kitty, Ghostty, WezTerm, or iTerm2 3.5+. |
| "Couldn't resume …" after a restart | The video became unavailable or the network is down. The queue is kept: `space` retries, `n` skips. |
| Garbled terminal after `kill -9` | Run `reset`. Normal exits restore the terminal. |
| Anything else | `~/Library/Logs/PixelFM/pixelfm.log`; for more detail `PIXELFM_LOG_LEVEL=DEBUG pixelfm`. |

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
- **npm install.** It needs Python 3.12+ and internet on first run. FFmpeg
  (and Tesseract on Linux) are installed by the launcher only after you agree,
  with your package manager; yt-dlp's JavaScript runtime is covered by the
  Node.js that npm needs (or deno/bun). Dependencies are pinned to exact
  versions but are not hash-verified.
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
- **RAVE MODE cost.** About a fifth of one CPU core and ~450 KB/s of
  terminal output while it runs. Slow terminals may drop frames; playback and
  input are unaffected.
- **Real covers.** They need a terminal with the Kitty graphics protocol or
  Sixel. During development the image data was checked (complete Sixel
  images and Kitty commands are sent), but the pictures were not seen on a
  real image-capable terminal, because none was available. Terminal.app can
  only draw characters.
- **Spotify.** Only playlists you own or collaborate on, through your own
  developer app (Premium owner, up to 5 users): Spotify's rules, not
  PixelFM's. Export files work for anything you can export.
- **Matching.** YouTube matches are scored guesses. Some songs aren't on
  YouTube, have only unofficial uploads, or have several equally good
  versions; review "likely" and "unmatched" entries.
- **macOS media controls.** The helper, its protocol, and its start-up and
  shutdown were tested on macOS 26 (Apple Silicon). Whether the hardware keys
  and Control Centre reach it depends on macOS choosing PixelFM as the Now
  Playing app; this could not be tested automatically, because macOS ignores
  simulated key presses from tools without Accessibility permission. Check it
  with `pixelfm --media-test`. Intel Macs and macOS 12–15 are built for but
  untested.
- **Platforms.** macOS and Linux only. Tested on macOS (Apple Silicon) with
  Python 3.12, 3.13, and 3.14, FFmpeg 8.1, yt-dlp 2026.08.19, Textual 8.2, and
  Node 22. Linux has not been tested on real hardware.
- **Screenshot reading** was tested with Apple Vision and Tesseract 5.5 on
  screenshots in four layouts made for the tests, not on every app's real
  screens; check the rows before adding them. The Linux package-manager
  installs (apt, dnf, pacman, zypper, apk) are tested with fake package
  managers only.

## Manual check: real audio

1. `pixelfm --check` shows everything `ok`.
2. Run `pixelfm`, press `2`, search `daft punk around the world`, press `enter`,
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
   Run `pixelfm` again. The same queue appears, the deck shows **PAUSED · restored
   at m:ss**, nothing plays, and `space` resumes from that point.
8. Press `4`, highlight a playlist, and press `Z`. The queue shows the
   playlist in a new order and it starts playing; the playlist itself keeps
   its order. Press `Z` again for another order.
9. Press `P` and watch the booth lights; with `pulse` (Settings) they follow
   the music. Press `P` again: they stop at once.
10. Press `R`: RAVE MODE lights the whole screen. Press `R` a few times to
    change pattern; with `bass` the lights follow the music. `P` stops it.
11. With music playing, switch to another app and press the keyboard's
    play/pause key, then next. Open Control Centre: PixelFM's track and cover
    are there, and its buttons work. (`pixelfm --media-test` checks this
    without music.)
12. Press `4`, then `I`, and paste a link to a Spotify playlist you own.
    Follow the one-time set-up, choose **Import and match**, and press `M`
    to review the matches.
13. Take a screenshot of a song list (`⌃⇧⌘4` on a Mac copies it to the
    clipboard). Press `4`, `I`, and `enter` on the empty field. Check the
    songs, choose an existing playlist, and confirm that songs already in it
    are reported as skipped.

## Security and privacy

- **Passwords and secrets.** PixelFM never asks for a Spotify password; you
  sign in on spotify.com. No client secret exists (PKCE), and none is in the
  package. Tokens are stored in the macOS Keychain (the Secret Service on
  Linux), never in the library or logs, and are written through stdin rather
  than on a command line. Settings → Spotify sign-in removes them.
- **What is sent.** Spotify is only asked for the playlists you import, with
  read-only scopes. YouTube receives searches, the same as typing them.
  Screenshots are read on your machine and never uploaded. Nothing else
  leaves your machine.
- **Sign-in callback.** It is a one-shot server on `127.0.0.1` (unreachable
  from the network) that checks the `state` value and closes after one
  request.
- **Media helper.** A local child process with no network access, talking
  only to PixelFM through a pipe. It exits with PixelFM. The screenshot
  reader next to it (`pixelfm-ocr`) reads one image and exits.
- **Installing tools.** Only the npm launcher, only after you answer yes in
  the terminal, and only with your system's own package manager.

## Development

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # Python tests (no network, no audio); ~640 tests
ruff format . && ruff check .
npm test                  # launcher tests (node:test; no network)
PIXELFM_TEST_BOOTSTRAP=1 npm test   # + a real first-run setup (needs internet)
npm run build:helper      # macOS: the media-controls and screenshot helpers (Xcode CLT)
npm pack                  # builds the helper + wheel (prepack) and the tarball
python -m pixelfm --mock  # run from source with sample data
```

Layout:

```
bin/pixelfm.js, lib/launcher.js  npm launcher (private venv, signals, diagnostics)
lib/system-deps.js            FFmpeg/Tesseract per OS: check, ask, install
native/macos/                 Swift helpers: media controls, screenshot text (build-helper.js)
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
    library.py                SQLite library, session, imports, migrations (v1-v6)
    palette.py, party.py      artwork palettes, Party Mode and RAVE lighting
    spotify.py, matching.py   Spotify links/sign-in/API/exports, YouTube matching
    screenshot.py             reading songs from screenshots (Vision / Tesseract)
    media_session.py          macOS Now Playing / media keys (talks to the helper)
    graphics.py, credentials.py  terminal image detection, token storage
    streams.py, youtube.py    yt-dlp search and stream resolution (local-first)
    artwork.py, cache.py      thumbnails, cover renderer, caches
  queue.py, state.py          queue model and playback state machine
tests/                        pytest suite (fakes.py: fake ffplay, resolver, reaper)
tests/fixtures/screenshots/   test screenshots and what Vision/Tesseract read from them
test/                         launcher and system-tools tests (node:test)
docs/GUIDE.md                 the user guide (how to do everything)
docs/screenshots/             real SVG screenshots used in the docs
CHANGELOG.md                  what changed in each version
```

## License

MIT. See [LICENSE](LICENSE). PixelFM uses
[textual-image](https://github.com/lnqs/textual-image) (LGPL-3.0, installed
unmodified from PyPI) for real covers. PixelFM is not affiliated with YouTube or Google.
Downloading content is your responsibility: only download audio you own or are
authorized to download.
