"""Synthetic playlist screenshots in common layouts, for the screenshot tests.

Run on macOS (it uses Helvetica): python make_shots.py <output folder>. The
*.vision.json and *.tesseract.tsv fixtures are what the real engines read
from these images.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
F = "/System/Library/Fonts/Helvetica.ttc"
S = 2  # retina


def font(px, bold=False):
    return ImageFont.truetype(F, px * S, index=1 if bold else 0)


SONGS = [
    ("Blinding Lights", "The Weeknd", "After Hours", "3:20"),
    ("Levitating (feat. DaBaby)", "Dua Lipa, DaBaby", "Future Nostalgia", "3:23"),
    ("bad guy", "Billie Eilish", "WHEN WE ALL FALL ASLEEP", "3:14"),
    ("Mr. Brightside", "The Killers", "Hot Fuss", "3:42"),
    ("Heat Waves", "Glass Animals", "Dreamland", "3:58"),
    ("Tum Hi Ho", "Arijit Singh", "Aashiqui 2", "4:22"),
    ("Don't Start Now", "Dua Lipa", "Future Nostalgia", "3:03"),
    ("Sweater Weather", "The Neighbourhood", "I Love You.", "4:00"),
]


def spotify_desktop():
    W, H = 1200, 760
    im = Image.new("RGB", (W * S, H * S), (18, 18, 18))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W * S, 230 * S], fill=(60, 40, 90))
    d.text((40 * S, 40 * S), "Public Playlist", font=font(13), fill=(255, 255, 255))
    d.text((40 * S, 62 * S), "Road Trip", font=font(64, True), fill=(255, 255, 255))
    d.text(
        (40 * S, 150 * S),
        "Injora • 8 songs, 29 min 12 sec",
        font=font(14),
        fill=(220, 220, 220),
    )
    y0 = 260
    for x, t in (
        (40, "#"),
        (90, "Title"),
        (560, "Album"),
        (880, "Date added"),
        (1110, "🕒"),
    ):
        if t != "🕒":
            d.text((x * S, y0 * S), t, font=font(14), fill=(170, 170, 170))
    d.line([(30 * S, 285 * S), (1170 * S, 285 * S)], fill=(50, 50, 50), width=S)
    for i, (t, a, al, dur) in enumerate(SONGS):
        y = 300 + i * 56
        d.text((44 * S, (y + 12) * S), str(i + 1), font=font(15), fill=(170, 170, 170))
        d.rectangle([90 * S, y * S, 130 * S, (y + 40) * S], fill=(90 + i * 15, 60, 120))
        d.text((145 * S, (y + 1) * S), t, font=font(16), fill=(255, 255, 255))
        if i == 2:
            d.rectangle(
                [145 * S, (y + 24) * S, 157 * S, (y + 36) * S], fill=(170, 170, 170)
            )
            d.text((148 * S, (y + 24) * S), "E", font=font(10, True), fill=(18, 18, 18))
            d.text((163 * S, (y + 23) * S), a, font=font(14), fill=(170, 170, 170))
        else:
            d.text((145 * S, (y + 23) * S), a, font=font(14), fill=(170, 170, 170))
        d.text((560 * S, (y + 12) * S), al, font=font(14), fill=(170, 170, 170))
        d.text(
            (880 * S, (y + 12) * S),
            f"Oct {10 + i}, 2026",
            font=font(14),
            fill=(170, 170, 170),
        )
        d.text((1110 * S, (y + 12) * S), dur, font=font(14), fill=(170, 170, 170))
    im.save(OUT / "spotify-desktop.png")


def apple_music():
    W, H = 1100, 600
    im = Image.new("RGB", (W * S, H * S), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((30 * S, 30 * S), "Favourites", font=font(30, True), fill=(0, 0, 0))
    d.text((30 * S, 72 * S), "Playlist · 8 songs", font=font(14), fill=(120, 120, 120))
    y0 = 120
    for x, t in ((30, "Song"), (420, "Artist"), (680, "Album"), (1000, "Time")):
        d.text((x * S, y0 * S), t, font=font(12, True), fill=(120, 120, 120))
    for i, (t, a, al, dur) in enumerate(SONGS):
        y = 145 + i * 34
        if i % 2:
            d.rectangle(
                [20 * S, (y - 6) * S, 1080 * S, (y + 22) * S], fill=(244, 244, 246)
            )
        d.text((30 * S, y * S), t, font=font(14), fill=(0, 0, 0))
        d.text((420 * S, y * S), a, font=font(14), fill=(90, 90, 90))
        d.text((680 * S, y * S), al, font=font(14), fill=(90, 90, 90))
        d.text((1000 * S, y * S), dur, font=font(14), fill=(90, 90, 90))
    im.save(OUT / "apple-music.png")


def spotify_mobile():
    W, H = 390, 844
    im = Image.new("RGB", (W * S, H * S), (18, 18, 18))
    d = ImageDraw.Draw(im)
    d.text((16 * S, 60 * S), "Road Trip", font=font(24, True), fill=(255, 255, 255))
    d.text((16 * S, 96 * S), "Injora", font=font(13, True), fill=(255, 255, 255))
    d.text((16 * S, 116 * S), "29 min", font=font(12), fill=(170, 170, 170))
    for i, (t, a, _album, _length) in enumerate(SONGS[:7]):
        y = 170 + i * 64
        d.rectangle([16 * S, y * S, 64 * S, (y + 48) * S], fill=(80 + i * 20, 50, 100))
        d.text((76 * S, (y + 5) * S), t, font=font(16), fill=(255, 255, 255))
        d.text((76 * S, (y + 27) * S), a, font=font(13), fill=(170, 170, 170))
        d.text(
            (356 * S, (y + 14) * S),
            "⋯" if False else "...",
            font=font(16),
            fill=(170, 170, 170),
        )
    im.save(OUT / "spotify-mobile.png")


def youtube_music():
    W, H = 1200, 560
    im = Image.new("RGB", (W * S, H * S), (3, 3, 3))
    d = ImageDraw.Draw(im)
    d.text((40 * S, 30 * S), "Chill Mix", font=font(32, True), fill=(255, 255, 255))
    d.text(
        (40 * S, 78 * S),
        "Playlist • Injora • 2026",
        font=font(14),
        fill=(170, 170, 170),
    )
    for i, (t, a, al, dur) in enumerate(SONGS):
        y = 130 + i * 50
        d.rectangle([40 * S, y * S, 80 * S, (y + 40) * S], fill=(70 + i * 15, 70, 70))
        d.text((100 * S, (y + 11) * S), t, font=font(15, True), fill=(255, 255, 255))
        d.text((480 * S, (y + 11) * S), a, font=font(15), fill=(170, 170, 170))
        d.text((780 * S, (y + 11) * S), al, font=font(15), fill=(170, 170, 170))
        d.text((1120 * S, (y + 11) * S), dur, font=font(15), fill=(170, 170, 170))
    im.save(OUT / "youtube-music.png")


spotify_desktop()
apple_music()
spotify_mobile()
youtube_music()
print("ok")
