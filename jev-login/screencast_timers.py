"""Put the screencast on the traced clock, then burn in a timer per pane.

  vhs screencast.tape && ./screencast.sh stop && uv run python screencast_timers.py

VHS records a browser-hosted terminal, and what it shows can trail the real
one by a frame or two. To keep the clocks exact, the video is retimed
piecewise through moments whose true times the panes' own `--trace` files
give:

- Enter: the left pane's spinner line, drawn the moment the REPL reads it;
- Jev's result appearing: Enter + its traced `login` duration;
- each chat tool call's line appearing, found by its green bullet: the k-th
  bullet on screen is the k-th tool call in the trace;
- the chat pane's last change: Enter + its traced turn.

The script prints each anchor's traced time beside where it appeared on
screen, so the size of the correction is visible on every run.
"""

import argparse
import itertools
import subprocess
from pathlib import Path

from demo import load_trace, login_window

HERE = Path(__file__).parent
RESULTS = HERE / "results"
WIDTH = 1920

# Regions (x, y, w, h) at the tape's 1080p and font size.
RESULT_BOX = (20, 385, 560, 240)  # where Jev's JSON lands, below the prompt
# The yellow spinner glyph on the line the REPL draws on Enter. Completion
# hints shown while typing are grey, so yellow means Enter.
SPINNER_BOX = (20, 385, 40, 30)
CHAT_BOX = (965, 420, 940, 620)  # the chat pane below its prompt
# The column the chat pane's `● [tool: …]` bullets sit in, and nothing else does.
BULLETS = (965, 420, 14, 620)
SCALE = 4  # the chat box is compared downscaled: a cursor blink stays small


def frames(video, box, scale=1, pix="gray"):
    x, y, w, h = box
    w2, h2 = w // scale, h // scale
    vf = f"crop={w}:{h}:{x}:{y}" + (f",scale={w2}:{h2}:flags=area" if scale > 1 else "")
    proc = subprocess.Popen(["ffmpeg", "-v", "quiet", "-i", str(video), "-vf", f"{vf},format={pix}",
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    size = w2 * h2 * (3 if pix == "rgb24" else 1)
    try:
        while chunk := proc.stdout.read(size):
            if len(chunk) == size:
                yield chunk
    finally:
        proc.kill()
        proc.wait()


def frame_rate(video):
    """VHS writes its own rate (25 fps for `Set Framerate 20`), so read it."""
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=avg_frame_rate", "-of", "csv=p=0", str(video)],
                          check=True, capture_output=True, text=True).stdout.strip()
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


def enter_time(video, fps):
    """The left pane's spinner appearing: the frame the REPL read Enter."""
    for i, frame in enumerate(frames(video, SPINNER_BOX, pix="rgb24")):
        if any(frame[j] > 180 and frame[j + 1] > 130 and frame[j + 2] < 110
               for j in range(0, len(frame), 3)):
            return i / fps
    raise SystemExit("never saw the spinner in the left pane")


def result_time(video, fps):
    """Jev's JSON appearing: #f7f7ef text, brighter than the dim spinner line."""
    for i, frame in enumerate(frames(video, RESULT_BOX)):
        if max(frame) > 220:
            return i / fps
    raise SystemExit("never saw Jev's result in the left pane")


def last_change(video, fps):
    """When the chat pane last gained output: a line or more, not a cursor blink."""
    last, prev = None, None
    for i, frame in enumerate(frames(video, CHAT_BOX, SCALE)):
        if (prev is not None and frame != prev
                and sum(1 for a, b in zip(frame, prev, strict=True) if abs(a - b) > 12) > 60):
            last = i / fps
        prev = frame
    return last


def bullet_times(video, count, fps):
    """When the 1st, 2nd, … green tool-call bullet appeared in the chat pane."""
    greens = []
    for frame in frames(video, BULLETS, pix="rgb24"):
        greens.append(sum(1 for i in range(0, len(frame), 3)
                          if frame[i + 1] > 140 and frame[i] < 130 and frame[i + 2] < 150))
    # One bullet's worth, from the first frame with any: the final frame also
    # holds the REPL's green prompt in this column.
    per = next(g for g in greens if g)
    seen = [round(g / per) for g in greens]
    return [next(i for i, n in enumerate(seen) if n >= k) / fps for k in range(1, count + 1)]


def font(style):
    return subprocess.run(["fc-match", "-f", "%{file}", f"Fira Code:style={style}"],
                          check=True, capture_output=True, text=True).stdout


def clock(start, seconds):
    e = f"clip(t-{start:.3f}\\,0\\,{seconds:.3f})"
    return f"%{{eif\\:trunc({e})\\:d}}.%{{eif\\:trunc(mod({e}*100\\,100))\\:d\\:2}}s"


def pane(x, start, seconds, colour, fonts):
    regular, medium = fonts
    return [
        f"drawtext=fontfile={regular}:text='time inside /login':fontsize=24:"
        f"fontcolor=#8a8a8a:x={x}:y=852",
        f"drawtext=fontfile={medium}:text='{clock(start, seconds)}':fontsize=104:"
        f"fontcolor={colour}:x={x}:y=890",
        f"drawtext=fontfile={regular}:text='● signed in':fontsize=28:"
        f"fontcolor=#5fb872:x={x}:y=1010:enable='gte(t\\,{start + seconds:.3f})'",
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default=RESULTS / "jev-login-race.mp4", type=Path)
    parser.add_argument("--out", default=RESULTS / "jev-login-race-timed.mp4", type=Path)
    args = parser.parse_args()

    _, _, jev_ms = login_window(load_trace(RESULTS / "screencast-jev.jsonl"))
    during, chat_start, chat_ms = login_window(load_trace(RESULTS / "screencast-chat.jsonl"))
    jev, chat = jev_ms / 1000, chat_ms / 1000
    tools = [r for r in during if r["type"] == "tool" and r["name"] != "login"]

    # (name, video time, traced time since Enter)
    fps = frame_rate(args.video)
    enter = enter_time(args.video, fps)
    anchors = [("Enter", enter, 0.0), ("Jev result", result_time(args.video, fps), jev)]
    for v, r in zip(bullet_times(args.video, len(tools), fps), tools, strict=True):
        anchors.append((r["name"], v, (r["t"] - chat_start) / 1000))
    anchors.append(("chat turn ends", last_change(args.video, fps), chat))
    # Same-moment calls share a frame; keep the mapping strictly increasing.
    points, kept = [anchors[0][1:]], {0}
    for i, (_, v, r) in enumerate(anchors[1:], 1):
        if v > points[-1][0] and r > points[-1][1]:
            points.append((v, r))
            kept.add(i)

    v_last, r_last = points[-1]
    remap = f"{enter + r_last:.3f}+(T-{v_last:.3f})"
    for (v0, r0), (v1, r1) in reversed(list(itertools.pairwise(points))):
        seg = f"{enter + r0:.3f}+(T-{v0:.3f})*{(r1 - r0) / (v1 - v0):.5f}"
        remap = f"if(lte(T\\,{v1:.3f})\\,{seg}\\,{remap})"
    remap = f"if(lte(T\\,{enter:.3f})\\,T\\,{remap})"

    fonts = font("Regular"), font("Medium")
    filters = ([f"setpts='{remap}/TB'", f"fps={fps:g}"]
               + pane(40, enter, jev, "#6cc7e2", fonts)
               + pane(WIDTH // 2 + 20, enter, chat, "#b4afb6", fonts))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(args.video), "-vf", ",".join(filters),
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(args.out)],
        check=True)

    print(f"enter at {enter:.2f}s · Jev {jev:.2f}s · chat {chat:.2f}s")
    print("anchor            traced   on screen")
    for i, (name, v, r) in enumerate(anchors):
        note = "" if i in kept else "   (shares a frame with the one before)"
        print(f"  {name:<17} {r:6.2f}s   {v - enter:6.2f}s{note}")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
