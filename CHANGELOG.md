# Changelog

## 2.3.1

- The colour (pixelated) cover is always the default. The artwork style
  (`m`) is no longer remembered between runs, so a stray key press can't
  leave the cover as ASCII; set `artwork_mode` in the config file for a
  different permanent style.

## 2.3.0

Includes everything planned for 2.2 (RAVE MODE), which was never released
on its own.

**New**
- **Import from screenshots.** Drop screenshots of a song list (Spotify,
  Apple Music, YouTube Music, a phone) on `I`, or use one on the clipboard.
  The songs are read on your computer (Apple Vision on macOS, Tesseract on
  Linux), checked by you, and added to a new or an existing playlist;
  songs already in it are skipped. See
  [the guide](docs/GUIDE.md#import-songs-from-screenshots).
- **Spotify playlist import** (`I`) from a link or an export file, with
  YouTube matching and a review screen (`M`).
- **macOS media controls:** media keys, headphones, and Control Centre.
- **RAVE MODE** (`R`): the whole terminal as a light rig, with six
  patterns.
- **Real cover pictures** in terminals that can draw images (Kitty,
  Ghostty, WezTerm, iTerm2 3.5+).
- **The npm launcher installs missing tools, after asking:** FFmpeg on
  macOS and Linux, and Tesseract on Linux, with the system's package
  manager. New `pixelfm --install-deps`.
- [User guide](docs/GUIDE.md) with step-by-step instructions.

**Changed**
- The command is now **`pixelfm`**, with **`pfm`** as a short form. It was
  `party` (2.1.1) and `ytm` (up to 2.1.0); upgrading removes the old one.
- `pixelfm --check` gives install commands for your system (apt, dnf,
  pacman, ...), and lists the screenshot reader (optional).
- Launcher commands (`--install-deps`, `--update-ytdlp`, ...) run from a
  source checkout now say what to do there instead of failing.
- Library schema 6 (2.1 used 4): adds imported playlists and which addition
  each imported entry came with. The library is backed up before the
  upgrade.

## 2.1.1

- The start command was renamed from `ytm` to `party`.

## 2.1.0

- Shuffle a playlist (`Z`), colours from the artwork, Party Mode (`P`),
  session recovery, and sharper covers.
