# PixelFM user guide

Step-by-step instructions for everything PixelFM does, from installing it to
importing a playlist from a screenshot. For the full reference (every key,
the data files, the technical details) see the [README](../README.md).

Start PixelFM with **`pixelfm`**, or the short form **`pfm`**. Both do
exactly the same thing.

## Contents

- [Install PixelFM](#install-pixelfm)
- [The first start](#the-first-start)
- [Find your way around](#find-your-way-around)
- [Play music](#play-music)
- [Use the queue](#use-the-queue)
- [Like songs](#like-songs)
- [Make and use playlists](#make-and-use-playlists)
- [Import a Spotify playlist](#import-a-spotify-playlist)
- [Import songs from screenshots](#import-songs-from-screenshots)
- [Review and fix YouTube matches](#review-and-fix-youtube-matches)
- [Download tracks and play offline](#download-tracks-and-play-offline)
- [Party Mode and RAVE MODE](#party-mode-and-rave-mode)
- [Change how it looks](#change-how-it-looks)
- [Control it from your Mac's media keys](#control-it-from-your-macs-media-keys)
- [Quit and pick up where you left off](#quit-and-pick-up-where-you-left-off)
- [Keep it working](#keep-it-working)
- [Share PixelFM with a friend](#share-pixelfm-with-a-friend)
- [Questions](#questions)

---

## Install PixelFM

PixelFM runs on **macOS** and **Linux**. It doesn't run on Windows yet.

### On a Mac

1. **Install Homebrew** if you don't have it: follow the one-line install on
   [brew.sh](https://brew.sh). (PixelFM uses it to install FFmpeg but never
   installs Homebrew itself.)
2. **Install Python and Node.js:**
   ```sh
   brew install python@3.13 node
   ```
3. **Install PixelFM:**
   ```sh
   npm install -g pixelfm
   ```
   (Until PixelFM is published on npm, install the package file you were
   given instead: `npm install -g ./pixelfm-2.3.1.tgz`. See
   [Share PixelFM with a friend](#share-pixelfm-with-a-friend).)
4. **Start it:**
   ```sh
   pixelfm
   ```

You don't need to install FFmpeg or anything for screenshots yourself: on
the first start PixelFM checks and offers to install FFmpeg, and macOS
reads screenshots with its own built-in text recognition.

### On Linux (Debian, Ubuntu, and similar)

1. **Install Python and Node.js:**
   ```sh
   sudo apt install python3.12 python3.12-venv nodejs npm
   ```
   (Use `python3 python3-venv` if your release already ships Python 3.12 or
   newer. On Fedora: `sudo dnf install python3.12 nodejs`.)
2. **Install PixelFM** and **start it:**
   ```sh
   npm install -g pixelfm
   pixelfm
   ```

On the first start PixelFM offers to install **FFmpeg** (to play audio) and
**Tesseract** (to read screenshots) with your package manager: apt, dnf,
pacman, zypper, or apk. `sudo` asks for your password.

### From a source checkout (for development)

```sh
cd ~/ytm
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
npm run build:helper     # macOS: media keys and the screenshot reader
pixelfm
```

This `pixelfm` starts the app directly, without the npm launcher, so it
never offers to install anything; install FFmpeg yourself
(`brew install ffmpeg`).

---

## The first start

The first `pixelfm` does a one-time set-up, and tells you what it is doing:

1. **System tools.** If FFmpeg is missing (on Linux, also Tesseract), it
   says what it is for and asks:
   ```
   PixelFM uses FFmpeg (audio playback), which is not installed.
   Install now with `brew install ffmpeg`? [Y/n]
   ```
   Press **enter** (or `y`) to install, or `n` to skip. A "no" is
   remembered until the next PixelFM version; run `pixelfm --install-deps`
   to be asked again.
2. **Python environment.** It creates a private Python environment for
   PixelFM (about 20–60 seconds, needs internet once). Your system Python is
   never touched.
3. **Welcome screen.** The app opens with a checklist of what is installed
   and where your data is kept. Press **enter** to start.

Later starts skip all of this and open straight away.

To check everything at any time:

```sh
pixelfm --check            # what is installed, and where your data is
pixelfm --install-deps     # install missing FFmpeg / Tesseract (asks first)
```

---

## Find your way around

PixelFM has eight views. Press a number key to switch; playback never
stops when you switch.

| Key | View | What's there |
| --- | --- | --- |
| `1` | Booth | the deck (cover, title, timeline), meters, mixer, and the queue |
| `2` | Search | YouTube search and results |
| `3` | Queue | what has played, what is playing, what comes next |
| `4` | Playlists | your playlists on the left, their tracks on the right |
| `5` | Liked | songs you liked |
| `6` | Downloads | tracks saved to your computer |
| `7` | Recent | recently played, with play counts |
| `8` | Settings | theme, Party Mode, layout, Spotify, downloads folder, and more |

- **`?`** shows every key. **`esc`** goes back or clears a filter.
- **`tab`** moves between buttons, faders, and lists; the mouse works too.
- The **status line** at the bottom tells you what just happened and what to
  do next. The line under it lists the keys for the current view.
- Shortcut keys don't fire while you are typing in a text box: press `esc`
  first.

---

## Play music

**Search and play:**

1. Press **`2`** (or **`/`**) and type what you want, for example
   `daft punk around the world`.
2. Press **enter** to search.
3. Move to a result with the arrow keys and press **enter** to play it.
4. Press **`1`** to see the booth: the cover, the timeline, and the meters
   moving with the music.

**While it plays:**

| Do this | Key |
| --- | --- |
| Pause / resume | `space` |
| Next / previous track | `n` / `b` (`b` restarts the track if it is more than 3 s in) |
| Jump back / forward 10 seconds | `[` / `]` (or click the timeline) |
| Stop | `x` |
| Volume up / down | `+` / `-` |
| More / less bass | `}` / `{` |
| More / less treble | `)` / `(` |
| Even out loud and quiet tracks | `N` (loudness normalization) |
| Repeat off → all → one | `r` |

Changing the volume or the EQ restarts the stream at the same point, so
expect a short pause (under a second).

---

## Use the queue

The queue is the list of what plays next.

1. Highlight any track (in Search, a playlist, Liked, ...).
2. Press **`a`** to add it to the end of the queue, or **`A`** to play it
   next.
3. Press **`3`** to see the queue. Played tracks are dim, the playing one has
   `▶`.
4. In the queue: **`K`** / **`J`** move a track up / down, **`d`** removes
   it, **`C`** clears the queue (it asks first).

**`z`** shuffles what is coming up (press again to put it back in order).

---

## Like songs

- Press **`f`** on a highlighted track (or on what is playing) to like it;
  `f` again unlikes it. Liked tracks show `♥`.
- Press **`5`** to see them. **`o`** sorts them by date, title, or artist;
  **`A`** adds them all to the queue.

Liking and downloading are separate: unliking never deletes a download.

---

## Make and use playlists

**Create a playlist and add songs:**

1. Highlight a track anywhere and press **`p`**.
2. Choose a playlist, or **+ New playlist…** and type a name.

Or press **`4`**, then **`c`** to create an empty playlist.

**In the Playlists view (`4`):**

| Do this | Key |
| --- | --- |
| Play the whole playlist | `enter` (on the playlist) |
| Play it once in a random order | `Z` (or the **⇄ SHUFFLE** button) |
| Add it to the end of the queue | `A` |
| Open its tracks | `l` (and `h` to go back) |
| Play from a track | `enter` (on the track) |
| Move a track up / down | `K` / `J` |
| Remove a track | `d` (on the track) |
| Rename / delete the playlist | `e` / `d` (deleting asks first) |
| Find a track in a long playlist | `ctrl+f`, then type |

**Shuffle a playlist (`Z`).** Each press gives a fresh random order in which
every track plays exactly once. The saved playlist keeps its own order.
Press `Z` again for a new order.

A playlist holds each song once. Deleting a playlist never deletes liked
songs or downloaded files.

---

## Import a Spotify playlist

PixelFM copies the list of tracks from Spotify, then finds each one on
YouTube to play it. You can do this with a link (needs a one-time set-up)
or with an export file (no set-up).

### With a link

**One-time set-up** (PixelFM walks you through it the first time):

1. Open [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard),
   log in, and choose **Create app**. Any name works; tick **Web API**.
2. Under **Redirect URIs**, add exactly `http://127.0.0.1:8898/callback`.
3. Copy the app's **Client ID**. You don't need the client secret.

The app's owner needs **Spotify Premium** (a Spotify rule for personal
apps).

**Import:**

1. In Spotify, open your playlist and choose **Share → Copy link to
   playlist**.
2. In PixelFM press **`4`**, then **`I`** (or the **⤓ IMPORT** button).
3. Paste the link and press **enter**.
4. The first time, paste your **Client ID** when asked. Your browser then
   opens spotify.com: log in and choose **Agree**. Go back to the terminal.
   (PixelFM never sees your password; the sign-in is kept in the macOS
   Keychain.)
5. A preview shows the playlist and every track. Press **`m`** to import it
   and match it on YouTube (or **`i`** to import without matching yet).
6. The playlist appears in the Playlists view and fills in as matching runs.

**Only your own playlists.** Spotify only gives personal apps the tracks of
playlists you own or collaborate on. For anyone else's playlist: open it in
Spotify, select all tracks, choose **Add to playlist → New playlist**, and
import your copy. Or use a screenshot (below).

### With an export file

1. Get the file: Spotify's own data download (Spotify Account → Privacy →
   *Download your data*), which includes `Playlist1.json`, or a CSV from a
   tool like Exportify.
2. Press **`4`**, then **`I`**, and paste the file's path (or drag the file
   onto the terminal). Press **enter**.
3. If the file has several playlists, choose one. Then press **`m`**.

---

## Import songs from screenshots

Take a screenshot of any song list (Spotify, Apple Music, YouTube Music,
your phone) and PixelFM reads the songs off it and adds them to a new or an
existing playlist. Songs that are already in that playlist are skipped.

The text is read **on your computer** with its built-in text recognition
(Apple Vision on macOS, Tesseract on Linux). The image is never uploaded,
and no AI service or account is used.

### Step by step

1. **Take the screenshot.** Show the song list with titles and artists
   readable, then:
   - **Mac, to a file:** `⌘⇧4` and drag over the list. The file goes to your
     Desktop.
   - **Mac, to the clipboard:** `⌃⇧⌘4` and drag over the list.
   - **Phone:** take a screenshot and send it to your computer (AirDrop,
     for example).
   For a long list, scroll and take several screenshots; overlapping ones
   are fine.
2. **Open the import.** In PixelFM press **`4`**, then **`I`**.
3. **Give it the screenshots:**
   - **Files:** drag one or more screenshot files from Finder onto the
     terminal window (that types their paths), then press **enter**.
   - **Clipboard:** leave the field empty and press **enter**.
4. **Check the songs.** PixelFM lists every song it read, with the artist,
   album, and length when it could read them:

   ![Checking the songs read from a screenshot](screenshots/screenshot-review.svg)

   - **`space`** leaves out a row that is wrong (it is shown crossed out),
     or puts it back.
   - **`e`** fixes a row: type `Title — Artist` (a plain `-` works too) and
     press **enter**.
   - Press **enter** to continue, or **`esc`** to cancel.
5. **Choose where the songs go:**

   ![Choosing the playlist](screenshots/screenshot-target.svg)

   - **+ New playlist**: type a name (PixelFM suggests the playlist name it
     read from the screenshot) and press **enter**.
   - **An existing playlist**: the new songs are added at the end, and the
     ones already in it are skipped. The status line says how many, for
     example *Added 5 songs to "Road Trip"; 2 already in it (skipped).*
6. **Matching.** PixelFM finds each new song on YouTube in the background,
   exactly as for a Spotify import. The playlist's heading shows how many
   were matched and skipped; press **`M`** to [review the
   matches](#review-and-fix-youtube-matches).

   ![The songs added to the playlist](screenshots/screenshot-added.svg)

### How "already in the playlist" is decided

A song is skipped when the playlist already has it:

- **By name:** the same title and artist. This also catches YouTube-style
  entries: "Blinding Lights" by The Weeknd is the same song as
  *The Weeknd - Blinding Lights (Official Video)* from *TheWeekndVEVO*, or
  *Blinding Lights* from *The Weeknd - Topic*.
- **By YouTube video:** if a song's match turns out to be a video the
  playlist already has, the new copy is skipped too.
- A different version (live, remix, a cover by someone else) is a different
  song and is added.

Skipped songs are listed in the review (`M`) as *duplicate*, with *already
in the playlist*.

### Tips for a good reading

- Crop to the song list. Headers, track numbers, dates, play counts, the
  explicit (`E`) badge, and the playlist's title are ignored, but less
  clutter is always better.
- Bigger text reads better: enlarge the window or zoom in before taking the
  screenshot.
- Titles Spotify cuts off with "…" are read as far as they are visible;
  matching usually still finds the song, or fix it with `e`.
- On Linux, reading screenshots from the clipboard needs `wl-clipboard`
  (Wayland) or `xclip` (X11); files always work.

---

## Review and fix YouTube matches

After an import, each track is matched to a YouTube video and marked:

| Mark | Meaning |
| --- | --- |
| **matched** | a strong match (80%+): the official recording or very close |
| **likely** | probably right (62%+), worth a look |
| **unmatched** | nothing good enough was found; it shows `✕` and is skipped when playing |
| **duplicate / skipped / unavailable** | not added (already in the playlist, a podcast, removed from Spotify) |

**To review:**

1. Press **`4`**, highlight the imported playlist, and press **`M`**.
   Uncertain and unmatched tracks are listed first.
2. Highlight a track and press **enter**: PixelFM searches YouTube and shows
   the candidates with a score and the reasons.
3. Press **enter** on the right video, or choose **Leave it unmatched**.
   Not there? Edit the search (add the album, or "official audio") and press
   **enter** to search again.
4. **`u`** unmatches a track; **`a`** matches every track not matched yet.

**`X`** stops matching (what is done is kept); **`M`** → **`a`** carries on
later. Matching never downloads anything.

---

## Download tracks and play offline

Only download audio you **own or are allowed to download**: YouTube's terms
generally don't allow downloading without permission.

1. Highlight a track and press **`D`** (or the **↓ SAVE** button). The first
   time, PixelFM asks you to confirm you have the right to download it.
2. The progress shows in the deck, the status bar, and the Downloads view
   (`6`). **`c`** cancels.
3. Downloaded tracks show `↓` and play from the file, even without internet
   (the header says **LOCAL FILE**).

In the Downloads view (`6`): **`d`** deletes a file (asks first), **`L`**
relinks a file you moved, **`X`** removes entries whose file is gone. Files
are saved in `~/Music/PixelFM`; change that in **Settings → Downloads
folder**.

---

## Party Mode and RAVE MODE

- **`P`** turns **Party Mode** on or off: the booth's borders and light strip
  drift through neon colours. It never changes what is playing.
- Choose the style in **Settings → Party mode**: `ambient` (slow drift),
  `pulse` (follows the measured loudness), `strobe` (short colour swaps on
  loud moments, at most twice a second), or `rave`.
- **`R`** turns on **RAVE MODE**: the whole screen becomes a light rig.
  Press **`R`** again for the next pattern: chase, dance floor, laser sweep,
  bass pulse, rainbow, or `auto` (all of them in turn). **`P`** turns it off
  at once.

Both need full effects. If they won't start, set **Settings → Effects** to
`full` (macOS's *Reduce motion* setting makes `auto` choose reduced).

---

## Change how it looks

Press **`8`** for Settings, highlight a row, and press **enter** (or `→`)
to change it; `←` goes back a step. Changes are saved at once.

| Setting | Choices |
| --- | --- |
| Theme | `music` (colours from the current cover, the default), `pixelfm` (cyan/magenta), `pixelfm-phosphor` (green), or a Textual theme |
| Party mode | off, ambient, pulse, strobe, rave |
| Rave pattern | auto, chase, dance, laser, bass, rainbow |
| Layout | auto, full, compact (`v` switches) |
| Effects | auto, full, reduced |
| Meters & spectrum | on / off (`V`) |
| Artwork style | color, mono, glyph (`m`); not saved, so PixelFM always starts in color |
| Liked songs order | date added, title, artist |
| Spotify client ID / sign-in | set it up, or sign out |
| Downloads folder | where downloads go |
| Clear play history | asks first |
| Volume | `←` / `→` |

**Real cover pictures.** Terminals that can draw images (Kitty, Ghostty,
WezTerm, iTerm2 3.5+) show the actual cover. macOS Terminal.app can only draw
characters, so it shows a detailed block version.

---

## Control it from your Mac's media keys

While PixelFM runs on a Mac, these work even when the terminal is in the
background:

- the keyboard's **play/pause**, **next**, and **previous** keys;
- headphone buttons (AirPods, wired remotes);
- **Control Centre** and the menu-bar **Now Playing** item, which also show
  the title, artist, cover, and position.

If the keys go to another app (often Apple Music), pause that app or press
play in PixelFM once. To test: `pixelfm --media-test` listens for 60
seconds and prints every key it receives.

---

## Quit and pick up where you left off

- Quit with **`q`** (or `ctrl+c`, or just close the terminal).
- The next `pixelfm` brings back the queue, the track, the view, and the
  position. The deck shows **PAUSED · restored at m:ss**; press **`space`**
  to carry on from that point.

---

## Keep it working

| When | Run |
| --- | --- |
| Many tracks stop playing ("Sign in to confirm", "No playable audio format") | `pixelfm --update-ytdlp` |
| Something seems missing | `pixelfm --check` |
| FFmpeg (or Tesseract on Linux) is missing | `pixelfm --install-deps` |
| A new PixelFM version is out | `npm install -g pixelfm@latest` |
| The set-up seems broken | `pixelfm --reinstall-runtime` |
| Where is everything? | `pixelfm --runtime-info` |

These are commands of the npm install. In a source checkout (or a pipx
install) `pixelfm --update-ytdlp` and `pixelfm --install-deps` print what to
run there instead.

Upgrades keep your library, playlists, likes, downloads, and settings; the
library is backed up before it is upgraded.

Something went wrong? The log is `~/Library/Logs/PixelFM/pixelfm.log`
(Linux: `~/.local/state/pixelfm/pixelfm.log`). For more detail run
`PIXELFM_LOG_LEVEL=DEBUG pixelfm`. The README has a
[troubleshooting table](../README.md#troubleshooting).

---

## Share PixelFM with a friend

Until PixelFM is published on npm, send the package file:

1. **Build it** (on your Mac, in the source checkout; needs the Xcode
   Command Line Tools for the macOS helpers):
   ```sh
   cd ~/ytm
   npm pack
   ```
   This makes `pixelfm-2.3.1.tgz`.
2. **Send** that file to your friend.
3. **Your friend** installs Python 3.12+ and Node.js (see
   [Install PixelFM](#install-pixelfm)), then runs:
   ```sh
   npm install -g ./pixelfm-2.3.1.tgz
   pixelfm
   ```
   On the first start PixelFM offers to install FFmpeg (and Tesseract on
   Linux) for them.

To import from Spotify links, your friend needs their own free Spotify
developer app (or can be added as one of up to 5 users of yours, under
**User Management** in your Spotify dashboard). Export files and screenshots
need no set-up.

---

## Questions

**Is `pfm` different from `pixelfm`?**
No. `pfm` is just shorter; every option works with both (`pfm --check`).

**Does PixelFM download music?**
Only when you press `D`. Everything else streams. Matching and importing
never download anything.

**Does it send my screenshots anywhere?**
No. They are read on your computer and not kept.

**Does it need my Spotify password?**
No. You sign in on spotify.com in your browser; PixelFM only keeps a sign-in
token in the macOS Keychain (the Secret Service on Linux). **Settings →
Spotify sign-in** removes it.

**Why does a track show `✕`?**
It is unavailable (removed, private, or blocked in your region), or, in an
imported playlist, it has no YouTube match yet. Press `M` to find one.

**Why does PixelFM ask before installing FFmpeg instead of including it?**
FFmpeg's player needs your system's audio libraries, so it has to come from
your system's package manager. PixelFM never installs anything without a yes.

**Does it work on Windows?**
Not yet. PixelFM runs on macOS and Linux.

**Can I use two decks or crossfade?**
No. PixelFM has one deck: its audio engine plays one stream at a time.

**Where is my data?**
On your computer only. `pixelfm --check` prints every location; the README's
[Your data](../README.md#your-data) section explains each one and how to
back it up.
