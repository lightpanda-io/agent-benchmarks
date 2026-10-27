"""Put the screencast on the real clock, then burn in a timer per pane.

  vhs screencast.tape && ./screencast.sh stop && uv run python screencast_timers.py

VHS records a browser-hosted terminal, and what it shows can trail the real
one by a frame or two. To keep the clocks exact, the video is retimed first,
piecewise, through moments whose true times the panes' own `--trace` files
give:

- Enter: the left pane's spinner line, drawn the moment the REPL reads it;
- Jev's result appearing: Enter + its traced `login` duration;
- each chat tool call's line appearing, found by its green bullet: the k-th
  bullet on screen is the k-th tool call in the trace;
- the chat pane's last burst of output: Enter + its traced turn.

Each anchor lands within a frame of its traced time, and the script prints
them side by side so that is checked, not assumed.
"""

import argparse
import itertools
import json
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
RESULTS = HERE / "results"
WIDTH, HEIGHT = 1920, 1080

# Regions at the tape's 1080p and font size. Jev's JSON lands in the
# first; the second is the chat pane below its prompt line.
RESULT_BOX = (20, 385, 560, 240)  # x, y, w, h
# Where the yellow spinner glyph sits on the line the REPL draws on Enter.
# Completion hints shown while typing are grey, so yellow means Enter.
SPINNER_BOX = (20, 385, 40, 30)
CHAT_BOX = (965, 420, 940, 620)
# The column the chat pane's `● [tool: …]` bullets sit in, and nothing else does.
BULLETS = (965, 420, 14, 620)
SCALE = 4  # the chat box is compared downscaled: a cursor blink stays small


def records(trace):
    return [json.loads(line) for line in trace.read_text().splitlines() if line]


def login_start(recs):
    login = next(r for r in recs if r.get("name") == "login")
    return login["t"] - login["ms"]


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


def chat_bursts(video, fps):
    """Times at which the chat pane gained output: a line or more, not a cursor blink."""
    bursts, prev = [], None
    for i, frame in enumerate(frames(video, CHAT_BOX, SCALE)):
        if prev is not None:
            changed = sum(1 for a, b in zip(frame, prev, strict=True) if abs(a - b) > 12)
            if changed > 60:
                bursts.append(i / fps)
        prev = frame
    return bursts


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


def pane(x, start, seconds, colour, regular, medium):
    return [
        f"drawtext=fontfile={regular}:text='time inside /login':fontsize=24:"
        f"fontcolor=#8a8a8a:x={x}:y=852",
        f"drawtext=fontfile={medium}:text='{clock(start, seconds)}':fontsize=104:"
        f"fontcolor={colour}:x={x}:y=890",
        f"drawtext=fontfile={regular}:text='● signed in':fontsize=28:"
        f"fontcolor=#5fb872:x={x}:y=1010:enable='gte(t\\,{start + seconds:.3f})'",
    ]


def retime(points, enter, v):
    if v <= points[0][0]:
        return v
    for (v0, r0), (v1, r1) in itertools.pairwise(points):
        if v <= v1:
            return enter + r0 + (v - v0) * (r1 - r0) / (v1 - v0)
    return enter + points[-1][1] + (v - points[-1][0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default=RESULTS / "jev-login-race.mp4", type=Path)
    parser.add_argument("--out", default=RESULTS / "jev-login-race-timed.mp4", type=Path)
    args = parser.parse_args()

    jev_recs = records(RESULTS / "screencast-jev.jsonl")
    chat_recs = records(RESULTS / "screencast-chat.jsonl")
    jev = (max(r["t"] for r in jev_recs) - login_start(jev_recs)) / 1000
    chat_start = login_start(chat_recs)
    chat = (max(r["t"] for r in chat_recs) - chat_start) / 1000

    tools = [r for r in chat_recs
             if r["type"] == "tool" and r["name"] != "login" and r["t"] >= chat_start]

    # Anchors: (video time, real time since Enter). The result fixes Enter.
    fps = frame_rate(args.video)
    enter = enter_time(args.video, fps)
    v_res = result_time(args.video, fps)
    anchors = [(enter, 0.0), (v_res, jev)]
    for v, r in zip(bullet_times(args.video, len(tools), fps), tools, strict=True):
        anchors.append((v, (r["t"] - chat_start) / 1000))
    anchors.append((chat_bursts(args.video, fps)[-1], chat))
    # Same-moment calls share a frame; keep the mapping strictly increasing.
    points = [anchors[0]]
    for v, r in anchors[1:]:
        if v > points[-1][0] and r > points[-1][1]:
            points.append((v, r))

    # Piecewise linear: on time before the result, through each anchor, then
    # on time again after the last one.
    v_last, r_last = points[-1]
    remap = f"{enter + r_last:.3f}+(T-{v_last:.3f})"
    for (v0, r0), (v1, r1) in reversed(list(itertools.pairwise(points))):
        seg = f"{enter + r0:.3f}+(T-{v0:.3f})*{(r1 - r0) / (v1 - v0):.5f}"
        remap = f"if(lte(T\\,{v1:.3f})\\,{seg}\\,{remap})"
    remap = f"if(lte(T\\,{enter:.3f})\\,T\\,{remap})"

    regular, medium = font("Regular"), font("Medium")
    filters = ([f"setpts='{remap}/TB'", f"fps={fps:g}"]
               + pane(40, enter, jev, "#6cc7e2", regular, medium)
               + pane(WIDTH // 2 + 20, enter, chat, "#b4afb6", regular, medium))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as script:
        script.write(",\n".join(filters))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(args.video), "-/vf", script.name,
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(args.out)],
        check=True)
    Path(script.name).unlink()

    print(f"enter at {enter:.2f}s · Jev {jev:.2f}s · chat {chat:.2f}s")
    print(f"on screen the chat turn took {anchors[-1][0] - enter:.2f}s; "
          f"retimed through {len(points)} anchors")
    print("anchor            traced   video before → after")
    names = ["Enter", "Jev result"] + [r["name"] for r in tools] + ["chat turn ends"]
    for name, (v, r) in zip(names, anchors, strict=True):
        after = retime(points, enter, v) - enter
        print(f"  {name:<17} {r:6.2f}s   {v - enter:6.2f}s → {after:6.2f}s")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
