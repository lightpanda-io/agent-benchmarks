"""Append an end card to the local screencast: every decider's median /login.

  uv run --with pillow python screencast_endcard.py

The numbers are the README's "Local decider" table (5 fixture runs each,
2026-11-11) and are typed in here, not measured: the card summarises runs the
race video does not show.
"""

import argparse
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
RESULTS = HERE / "results"
W, H = 1920, 1080

BG, INK, MUTED = "#1c1c1c", "#f7f7ef", "#8a8a8a"
LOCAL, HOSTED = "#6cc7e2", "#6b6b6b"

# (decider, median seconds, local, note)
ROWS = [
    ("Jev", 0.51, False, ""),
    ("StartLux 2B", 0.58, True, ""),
    ("StartLux 4B", 1.35, True, ""),
    ("StartLux 9B", 2.40, True, ""),
    ("Claude Haiku 5.5", 5.06, False, ""),
]
FOOTNOTE = ("Median of 5 runs each on the HN-shaped login fixture, 2026-11-11; Haiku's is the median of the chat arm",
            "across those sessions. Local models on a Quadro RTX 5000 reached from a laptop over Wi-Fi; Jev on TypeSafe's API.")


def font(style, size):
    path = subprocess.run(["fc-match", "-f", "%{file}", f"Fira Code:style={style}"],
                          check=True, capture_output=True, text=True).stdout
    return ImageFont.truetype(path, size)


def card(path):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    title, label, value, small = font("Medium", 52), font("Regular", 30), font("Medium", 30), font("Regular", 22)

    d.text((120, 110), "Time inside /login, one decision model each", font=title, fill=INK)
    d.text((120, 185), "median of 5 runs · lower is faster", font=small, fill=MUTED)
    for x, colour, name in ((1300, LOCAL, "local GPU"), (1530, HOSTED, "hosted API")):
        d.rounded_rectangle((x, 192, x + 22, 214), radius=4, fill=colour)
        d.text((x + 34, 187), name, font=small, fill=MUTED)

    x0, x1 = 600, 1640
    scale = (x1 - x0) / max(s for _, s, _, _ in ROWS)
    y, step, bar = 300, 100, 34
    for name, secs, local, note in ROWS:
        d.text((x0 - 30, y + bar / 2), name, font=label, fill=INK, anchor="rm")
        end = x0 + max(secs * scale, 8)
        d.rounded_rectangle((x0, y, end, y + bar), radius=4, fill=LOCAL if local else HOSTED)
        d.text((end + 18, y + bar / 2), f"{secs:.2f}s", font=value, fill=INK, anchor="lm")
        tag = ("local" if local else "hosted") + (f" · {note}" if note else "")
        d.text((end + 140, y + bar / 2), tag, font=small, fill=MUTED, anchor="lm")
        y += step
    d.line((x0, 290, x0, y - step + bar + 10), fill="#3a3a3a", width=2)

    for i, line in enumerate(FOOTNOTE):
        d.text((120, 940 + i * 32), line, font=font("Regular", 20), fill=MUTED)
    img.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default=RESULTS / "jev-login-local-race-timed.mp4", type=Path)
    parser.add_argument("--out", default=RESULTS / "jev-login-local-race-card.mp4", type=Path)
    parser.add_argument("--seconds", type=float, default=5)
    args = parser.parse_args()

    png = args.out.with_suffix(".png")
    card(png)
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=r_frame_rate", "-of", "csv=p=0", str(args.video)],
                          check=True, capture_output=True, text=True).stdout.strip()
    fade = f"fade=t=in:st=0:d=0.5:color={BG}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(args.video),
         "-loop", "1", "-t", str(args.seconds), "-framerate", rate, "-i", str(png),
         "-filter_complex", f"[1:v]format=yuv420p,{fade}[c];[0:v][c]concat=n=2:v=1[v]",
         "-map", "[v]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(args.out)],
        check=True)
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
