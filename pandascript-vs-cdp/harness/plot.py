"""Strip plots of per-run benchmark timings, one SVG per live task.

Usage: uv run --with matplotlib python harness/plot.py [results-prefix] [figure-prefix]
Reads results/<prefix>-<task>-{cold,warm}/raw.jsonl (default prefix: stock).
figure-prefix, when given, is prepended to output basenames (e.g. `v7py py-`
writes figures/py-scrape.svg) so two campaigns' figures can coexist.
"""

import json
import math
import pathlib
import statistics
import sys

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker

ROOT = pathlib.Path(__file__).parent.parent
OUT = ROOT / "figures"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
ACCENT = "#2a78d6"   # first-party rows (PandaScript, Python bindings)
NEUTRAL = "#8a897f"  # other configurations (identity lives in the row labels)

FIRST_PARTY = {"pandascript", "lightpanda-py"}

ROWS = [
    ("pandascript", "PandaScript"),
    ("lightpanda-py", "Python bindings (pip install lightpanda)"),
    ("puppeteer-lightpanda", "Puppeteer → Lightpanda"),
    ("playwright-lightpanda", "Playwright → Lightpanda"),
    ("playwright-py-lightpanda", "Playwright-Python → Lightpanda"),
    ("puppeteer-chrome", "Puppeteer → Chrome"),
    ("playwright-chrome", "Playwright → Chrome"),
    ("playwright-py-chrome", "Playwright-Python → Chrome"),
    ("browseruse-lightpanda", "browser-use CLI → Lightpanda"),
    ("browseruse-chrome", "browser-use CLI → Chrome"),
]

TASKS = {
    "scrape": "Hacker News scrape: 6 page loads, live site",
    "retail": "Retail price monitoring (outdoorvoices.com): 4 page loads, live site",
    "news": "News monitoring (apnews.com): 4 page loads, live site",
}

matplotlib.rcParams.update({
    "svg.fonttype": "none",
    "font.family": "sans-serif",
    "font.size": 10,
    "text.color": INK,
    "axes.edgecolor": GRID,
    "xtick.color": INK_2,
    "ytick.color": INK,
})


def load(task, mode, prefix):
    def read(path):
        runs = {}
        if not path.exists():  # a mode that was not run (v13 retail: cold only)
            return runs
        for line in path.read_text().splitlines():
            r = json.loads(line)
            if r.get("warmup") or not r.get("ok"):
                continue
            runs.setdefault(r["config"], []).append(r["ms"] / 1000.0)
        return runs

    runs = read(ROOT / "results" / f"{prefix}-{task}-{mode}" / "raw.jsonl")
    # Warm-mode rows for process-per-run configs come from the -agentcache
    # supplement when it exists (persistent per-session cache dir — the
    # warm-state analogue of a held browser; same source as the tables).
    supplement = ROOT / "results" / f"{prefix}-{task}-{mode}-agentcache" / "raw.jsonl"
    if mode == "warm" and supplement.exists():
        runs.update(read(supplement))
    return runs


def draw(task, title, prefix, fig_prefix=""):
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.1), sharex=True, sharey=True)
    fig.patch.set_facecolor(SURFACE)

    # Only lay out rows for configs the dataset actually ran — a campaign
    # without e.g. the Python legs shouldn't render empty lanes.
    present = set(load(task, "cold", prefix)) | set(load(task, "warm", prefix))
    rows = [r for r in ROWS if r[0] in present]

    xmax = 0.0
    for ax, mode in zip(axes, ("cold", "warm")):
        runs = load(task, mode, prefix)
        ax.set_facecolor(SURFACE)
        for y, (config, label) in enumerate(reversed(rows)):
            values = runs.get(config, [])
            if not values:
                continue
            xmax = max(xmax, max(values))
            color = ACCENT if config in FIRST_PARTY else NEUTRAL
            # deterministic jitter: spread runs across a narrow band by index
            jitter = [((i % 5) - 2) * 0.07 for i in range(len(values))]
            ax.scatter(values, [y + j for j in jitter], s=22, color=color,
                       alpha=0.55, linewidths=0, zorder=3)
            med = statistics.median(values)
            ax.plot([med, med], [y - 0.28, y + 0.28], color=color,
                    linewidth=2.4, zorder=4, solid_capstyle="round")
        ax.set_title("Cold start" if mode == "cold" else "Warm browser",
                     fontsize=10, color=INK_2, loc="left", pad=8)
        ax.set_xlim(left=0)
        ax.grid(axis="x", color=GRID, linewidth=0.7, zorder=0)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", length=0)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(GRID)

    axes[0].set_yticks(range(len(rows)))
    axes[0].set_yticklabels([label for _, label in reversed(rows)], fontsize=9.5)
    for tick, (config, _) in zip(axes[0].get_yticklabels(), reversed(rows)):
        tick.set_color(INK if config in FIRST_PARTY else INK_2)
        if config in FIRST_PARTY:
            tick.set_fontweight("bold")
    axes[0].set_xlim(0, xmax * 1.06)
    for ax in axes:
        ax.set_xlabel("seconds per run", fontsize=9, color=INK_2)

    fig.suptitle(title, fontsize=11.5, x=0.005, y=1.02, ha="left", color=INK)
    fig.tight_layout()

    OUT.mkdir(exist_ok=True)
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{fig_prefix}{task}.{ext}", bbox_inches="tight",
                    facecolor=SURFACE, dpi=160)
    plt.close(fig)
    print(f"wrote figures/{fig_prefix}{task}.svg")


MEMORY_TASKS = {"scrape": "HN scrape", "retail": "Retail (outdoorvoices)", "news": "News (apnews)"}


def draw_memory(prefix, fig_prefix="", overrides=None):
    # Tasks with a dataset override come from that dataset's memory probe.
    sources = {task: (overrides or {}).get(task, prefix) for task in MEMORY_TASKS}
    path = ROOT / "results" / f"{prefix}-memory" / "raw.jsonl"
    if not path.exists():
        print(f"skip memory figure: no {path}")
        return
    by = {}
    lines = []
    for src in sorted(set(sources.values())):
        src_path = ROOT / "results" / f"{src}-memory" / "raw.jsonl"
        if src_path.exists():
            lines += [(src, l) for l in src_path.read_text().splitlines()]
    for src, line in lines:
        r = json.loads(line)
        if sources.get(r["task"], prefix) != src:
            continue
        if r.get("ok"):
            by.setdefault((r["task"], r["config"]), []).append(r["peak_pss_mb"])

    fig, axes = plt.subplots(1, len(MEMORY_TASKS), figsize=(9.2, 3.3), sharey=True)
    fig.patch.set_facecolor(SURFACE)

    rows = [r for r in ROWS if any((t, r[0]) in by for t in MEMORY_TASKS)]

    for ax, (task, subtitle) in zip(axes, MEMORY_TASKS.items()):
        ax.set_facecolor(SURFACE)
        for y, (config, label) in enumerate(reversed(rows)):
            values = by.get((task, config))
            if not values:
                continue
            med = statistics.median(values)
            color = ACCENT if config in FIRST_PARTY else NEUTRAL
            ax.barh(y, med, height=0.55, color=color, zorder=3)
            ax.annotate(f"{med:,.0f}", (med, y), xytext=(4, 0),
                        textcoords="offset points", va="center",
                        fontsize=8.5, color=INK_2, zorder=4)
        ax.set_title(subtitle, fontsize=10, color=INK_2, loc="left", pad=8)
        ax.set_xlim(left=0)
        ax.margins(x=0.14)
        ax.grid(axis="x", color=GRID, linewidth=0.7, zorder=0)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", length=0)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.set_xlabel("peak memory, MB (PSS)", fontsize=9, color=INK_2)

    axes[0].set_yticks(range(len(rows)))
    axes[0].set_yticklabels([label for _, label in reversed(rows)], fontsize=9.5)
    for tick, (config, _) in zip(axes[0].get_yticklabels(), reversed(rows)):
        tick.set_color(INK if config in FIRST_PARTY else INK_2)
        if config in FIRST_PARTY:
            tick.set_fontweight("bold")

    fig.suptitle("Peak memory over the whole process tree: median of 5 cold runs",
                 fontsize=11.5, x=0.005, y=1.02, ha="left", color=INK)
    fig.tight_layout()

    OUT.mkdir(exist_ok=True)
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{fig_prefix}memory.{ext}", bbox_inches="tight",
                    facecolor=SURFACE, dpi=160)
    plt.close(fig)
    print(f"wrote figures/{fig_prefix}memory.svg")


def load_parallel(prefix, task, mode, topology):
    """{config: {N: median batch seconds}} over results/<prefix>-<task>-<mode>-x<N>-<topology>."""
    out = {}
    # "-paced" dirs hold a later run of pandascript-paced with pandascript as
    # its same-run control; only configs not already present are taken from
    # them, so the control stays in the report but the figure keeps the
    # main campaign's pandascript row.
    dirs = sorted((ROOT / "results").glob(f"{prefix}-{task}-{mode}-x*-{topology}")) + \
        sorted((ROOT / "results").glob(f"{prefix}-{task}-{mode}-x*-{topology}-paced"))
    for d in dirs:
        n = int(d.name.rsplit("-x", 1)[1].split("-")[0])
        by, passed, total = {}, {}, {}
        for line in (d / "raw.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r.get("warmup"):
                continue
            n_ok = r.get("n_ok", n if r.get("ok") else 0)
            passed[r["config"]] = passed.get(r["config"], 0) + n_ok
            total[r["config"]] = total.get(r["config"], 0) + n
            # Same rule as report.py: a batch counts when at least half its
            # flows passed the content gate.
            if n_ok * 2 >= n:
                by.setdefault(r["config"], []).append(r["ms"] / 1000.0)
        for config, ms in by.items():
            if n in out.get(config, {}):
                continue
            out.setdefault(config, {})[n] = (statistics.median(ms), passed[config] / total[config])
    return out


def _label_ends(ax, ends, ymax, min_gap=0.045):
    """Direct labels at the right end of lines, pushed apart so none overprint:
    ends = [(y, x, label)], sorted by y; neighbours closer than min_gap of the
    axis height are spaced out."""
    placed = []
    for y, x, label in sorted(ends):
        ly = y
        if placed and ly < placed[-1] + min_gap * ymax:
            ly = placed[-1] + min_gap * ymax
        placed.append(ly)
        ax.annotate(label, (x, ly), xytext=(6, 0), textcoords="offset points",
                    fontsize=8, va="center", color=INK_2, annotation_clip=False)


LABELLED = ("pandascript", "puppeteer-chrome", "playwright-chrome", "browseruse-chrome")


# One hue per configuration, fixed (validated as adjacent pairs in the order
# the lines stack at N=16: PandaScript, the three lightpanda legs, the three
# Chrome legs). Cool hues = the Lightpanda engine, warm hues = Chrome.
PARALLEL_SERIES = [
    ("pandascript", "PandaScript", "#2a78d6"),
    # Same entity, different start policy: same hue, dashed. Where a task
    # has a paced run, draw_parallel drops the instant-start row and shows
    # this one as plain "PandaScript".
    ("pandascript-paced", "PandaScript, starts 30 ms apart", "#2a78d6", "--"),
    ("puppeteer-lightpanda", "Puppeteer → Lightpanda", "#1baf7a"),
    ("playwright-lightpanda", "Playwright → Lightpanda", "#008300"),
    ("browseruse-lightpanda", "browser-use CLI → Lightpanda", "#4a3aa7"),
    ("puppeteer-chrome", "Puppeteer → Chrome", "#e87ba4"),
    ("playwright-chrome", "Playwright → Chrome", "#eda100"),
    ("browseruse-chrome", "browser-use CLI → Chrome", "#e34948"),
]


def draw_parallel(prefix, task="login_fx", mode="cold", fig_prefix="", zoom_s=None):
    """Batch wall clock against batch size, one line per configuration, linear
    axes: a straight line through the origin is 'already CPU-bound' (twice the
    flows, twice the time), flat is 'still has headroom'. Left: one engine per
    flow; right: all flows on one engine."""
    panels = [("process", "one engine per flow"), ("shared", "all flows on one engine")]
    data = {top: load_parallel(prefix, task, mode, top) for top, _ in panels}
    panels = [(top, sub) for top, sub in panels if data[top]]
    if not panels:
        return
    # With a paced run present, the instant-start PandaScript row is the
    # site's burst limiter measuring itself, not the engine: drop it and
    # show the paced run as the PandaScript line (the blog post says why).
    paced = any("pandascript-paced" in data[top] for top, _ in panels)
    series_spec = PARALLEL_SERIES
    if paced:
        for d in data.values():
            d.pop("pandascript", None)
        series_spec = [("pandascript-paced", "PandaScript", "#2a78d6") if c == "pandascript-paced" else (c, l, col, *st)
                       for c, l, col, *st in PARALLEL_SERIES]
    ymax = max(v[0] for top in data.values() for series in top.values() for v in series.values())
    # Optional second row: the same data with the y-axis cut at zoom_s, so the
    # lines crushed against the floor of the full-scale row get room. Same
    # axes, same colours; the Chrome lines visibly leave the frame.
    rows = [("full", ymax * 1.08)] + ([("zoom", zoom_s)] if zoom_s else [])
    fig, grid = plt.subplots(len(rows), len(panels), figsize=(5.5 * len(panels), 4.8 * len(rows)),
                             sharex=True, squeeze=False)
    handles = []
    for (row_kind, ylim), axes in zip(rows, grid):
        for ax, (top, subtitle) in zip(axes, panels):
            ends = []
            for config, label, color, *style in series_spec:
                series = data[top].get(config)
                if not series:
                    continue
                xs = sorted(series)
                ys = [series[x][0] for x in xs]
                (h,) = ax.plot(xs, ys, style[0] if style else "-", color=color, lw=2, label=label,
                               solid_capstyle="round")
                # Pass rates (series[x][1]) stay in the report; the figure
                # does not mark them.
                ax.plot(xs, ys, "o", ms=6, mec="white", mew=1.2, mfc=color, zorder=3)
                if row_kind == "full" and ax is axes[0]:
                    handles.append(h)
                # Direct labels on the four the eye lands on first (full row)
                # and on whatever ends inside the frame (zoom row); the
                # legend carries all seven.
                x = xs[-1]
                if (row_kind == "full" and config in LABELLED) or (row_kind == "zoom" and series[x][0] < ylim) \
                        or config == "pandascript-paced":
                    ends.append((series[x][0], x, label))
            _label_ends(ax, ends, ylim)
            ax.set_xticks([1, 4, 8, 16])
            ax.set_xlim(0, 17.5)
            ax.set_ylim(0, ylim)
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
                lambda v, _: f"{v:g} s" if v else "0"))
            # The topology subtitle only earns its place when there are two
            # panels to tell apart.
            if row_kind == "full":
                if len(panels) > 1:
                    ax.set_title(subtitle, fontsize=10, loc="left", color=INK)
            else:
                ax.set_title(f"{subtitle} — same data, 0 to {zoom_s:g} s" if len(panels) > 1
                             else f"same data, 0 to {zoom_s:g} s", fontsize=10, loc="left", color=INK_2)
            ax.grid(True, axis="y", color="#e6e5df", lw=0.6)
            ax.spines[["top", "right"]].set_visible(False)
            ax.spines[["left", "bottom"]].set_color("#c9c8c0")
            ax.tick_params(colors=INK_2, labelsize=9)
        axes[0].set_ylabel("time to finish the whole batch (seconds)", color=INK_2)
    for ax in grid[-1]:
        ax.set_xlabel("concurrent flows in the batch")
    fig.legend(handles=handles, loc="lower center", ncol=4 if len(panels) == 2 else 2, frameon=False,
               fontsize=8.5, bbox_to_anchor=(0.5, (-0.06 if len(panels) == 2 else -0.16) / len(rows)),
               handlelength=2.2, columnspacing=1.6)
    runs = "10" if task == "login_fx" else "8"
    fig.suptitle(f"{task}: time to finish N flows started at once ({mode} runs, median of {runs})",
                 fontsize=11.5, x=0.005, y=1.01, ha="left", color=INK)
    fig.tight_layout(rect=(0, 0.02, 0.93 if len(panels) == 2 else 0.86, 1))
    stem = f"{fig_prefix}parallel-{task}-{mode}" + ("-zoom" if zoom_s else "")
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{stem}.{ext}", bbox_inches="tight", dpi=160 if ext == "png" else None)
    plt.close(fig)
    print(f"wrote figures/{stem}.svg")


def load_parallel_memory(prefix, task, topology):
    """{config: {N: (median peak MB, median cpu-s, median engine cpu-s)}} over
    results/<prefix>-memory-x<N>-<topology> (task filter for the live probe)."""
    out = {}
    dirs = list((ROOT / "results").glob(f"{prefix}-memory-x*-{topology}")) + \
        list((ROOT / "results").glob(f"{prefix}-memory-live-x*-{topology}")) + \
        list((ROOT / "results").glob(f"{prefix}-memory-live-x*-{topology}-paced"))
    for d in sorted(dirs):
        n = int(d.name.rsplit("-x", 1)[1].split("-")[0])
        by = {}
        for line in (d / "raw.jsonl").read_text().splitlines():
            r = json.loads(line)
            # Same rule as the timing chart: a batch counts when at least
            # half its flows passed the content gate (a live site rejecting
            # part of a burst does not change what the batch cost).
            if r.get("n_ok", n if r.get("ok") else 0) * 2 >= n and r.get("task", task) == task:
                by.setdefault(r["config"], []).append((r["peak_pss_mb"], r["cpu_s"], r.get("cpu_engine_s", 0.0)))
        for config, v in by.items():
            if n in out.get(config, {}):
                continue
            out.setdefault(config, {})[n] = tuple(statistics.median(x[i] for x in v) for i in range(3))
    return out


def draw_parallel_memory(prefix, task="login_fx", fig_prefix="", zoom=False):
    """Batch peak memory (top row) and batch CPU seconds (bottom row) against
    batch size, one line per configuration, per topology. Memory is the peak
    PSS over every engine and script in the batch; CPU is the batch's total
    utime+stime. Cold runs; median of 5."""
    panels = [("process", "one engine per flow"), ("shared", "all flows on one engine")]
    data = {top: load_parallel_memory(prefix, task, top) for top, _ in panels}
    panels = [(top, sub) for top, sub in panels if data[top]]
    if not panels:
        return
    metrics = [(0, "peak memory of the whole batch", lambda v: f"{v:g} MB" if v < 1000 else f"{v / 1000:g} GB", 100),
               (1, "CPU time of the whole batch", lambda v: f"{v:g} s", 1)]

    def limits(idx, step):
        """Full-scale limit, and the zoom limit: room for every series whose
        end point sits in the bottom third of the full scale (the Lightpanda
        engine legs), rounded up to `step`, so the Chrome lines leave the
        frame."""
        ends = [series[max(series)][idx] for top in data.values() for series in top.values()]
        full = max(ends) * 1.08
        low = [e for e in ends if e < max(ends) * 0.35]
        return full, (math.ceil(max(low) * 1.12 / step) * step if low else full)

    # Rows: each metric at full scale, then (zoom) each metric cut to the
    # low range — a single-panel task lays the zoom beside the full panel.
    rows = []
    for idx, ylabel, fmt, step in metrics:
        full, low = limits(idx, step)
        rows.append((idx, ylabel, fmt, full, "full"))
        if zoom:
            rows.append((idx, ylabel, fmt, low, "zoom"))
    side_by_side = zoom and len(panels) == 1
    if side_by_side:
        # 2 rows (metrics) x 2 columns (full, zoom)
        fig, grid = plt.subplots(2, 2, figsize=(11, 9.2), sharex=True, squeeze=False)
        cells = [(grid[i // 2][i % 2], rows[i]) for i in range(4)]
    else:
        fig, grid = plt.subplots(len(rows), len(panels), figsize=(5.5 * len(panels), 4.6 * len(rows)),
                                 sharex=True, squeeze=False)
        cells = [(grid[r][c], rows[r]) for r in range(len(rows)) for c in range(len(panels))]
    handles = []
    for cell_i, (ax, (idx, ylabel, fmt, ylim, kind)) in enumerate(cells):
        top, subtitle = panels[0] if side_by_side else panels[cell_i % len(panels)]
        if True:
            ends = []
            for config, label, color, *style in PARALLEL_SERIES:
                series = data[top].get(config)
                if not series:
                    continue
                xs = sorted(series)
                (h,) = ax.plot(xs, [series[x][idx] for x in xs], style[0] if style else "-", color=color,
                               marker="o", ms=6, mec="white", mew=1.2, lw=2, label=label, solid_capstyle="round")
                if cell_i == 0:
                    handles.append(h)
                y_end = series[xs[-1]][idx]
                if (kind == "full" and config in LABELLED) or (kind == "zoom" and y_end < ylim):
                    ends.append((y_end, xs[-1], label))
            _label_ends(ax, ends, ylim)
            ax.set_xticks([1, 4, 8, 16])
            ax.set_xlim(0, 17.5)
            ax.set_ylim(0, ylim)
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _, f=fmt: f(v) if v else "0"))
            if kind == "zoom":
                ax.set_title(f"{subtitle} — same data, 0 to {fmt(ylim)}", fontsize=10, loc="left", color=INK_2)
            elif idx == 0:
                ax.set_title(subtitle, fontsize=10, loc="left", color=INK)
            ax.grid(True, axis="y", color="#e6e5df", lw=0.6)
            ax.spines[["top", "right"]].set_visible(False)
            ax.spines[["left", "bottom"]].set_color("#c9c8c0")
            ax.tick_params(colors=INK_2, labelsize=9)
            if side_by_side or cell_i % len(panels) == 0:
                if not side_by_side or cell_i % 2 == 0:
                    ax.set_ylabel(ylabel, color=INK_2)
    for ax in grid[-1]:
        ax.set_xlabel("concurrent flows in the batch")
    wide = len(panels) == 2 or side_by_side
    fig.legend(handles=handles, loc="lower center", ncol=4 if wide else 2, frameon=False,
               fontsize=8.5, bbox_to_anchor=(0.5, -0.03 if wide else -0.08),
               handlelength=2.2, columnspacing=1.6)
    iters = "5" if task == "login_fx" else "3"
    fig.suptitle(f"{task}: what a batch of N flows costs in memory and CPU (cold runs, median of {iters})",
                 fontsize=11.5, x=0.005, y=1.01, ha="left", color=INK)
    fig.tight_layout(rect=(0, 0.02, 0.93 if wide else 0.86, 1))
    stem = f"{fig_prefix}parallel-{task}-memory" + ("-zoom" if zoom else "")
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{stem}.{ext}", bbox_inches="tight", dpi=160 if ext == "png" else None)
    plt.close(fig)
    print(f"wrote figures/{stem}.svg")


SINGLE_FLOW_TASKS = [("login_fx", "Login (local fixture)"), ("scrape", "Hacker News"),
                     ("retail", "Storefront"), ("news", "News page")]


def draw_single_flow(prefix, overrides, fig_prefix=""):
    """One figure for the post's baseline section: cold median per task, one
    bar per configuration, the parallel charts' palette. The login bars are
    tens of milliseconds next to seconds, so each task panel has its own
    scale and the value is printed on the bar."""
    series = [(c, l, col) for c, l, col, *_ in PARALLEL_SERIES if c != "pandascript-paced"]
    fig, axes = plt.subplots(1, len(SINGLE_FLOW_TASKS), figsize=(12, 3.6))
    for ax, (task, title) in zip(axes, SINGLE_FLOW_TASKS):
        runs = load(task, "cold", overrides.get(task, prefix))
        vals = [(label, color, statistics.median(runs[c])) for c, label, color in series if c in runs]
        ys = range(len(vals))
        ax.barh(list(ys), [v for _, _, v in vals], color=[c for _, c, _ in vals], height=0.68)
        for y, (_, _, v) in zip(ys, vals):
            ax.text(v, y, f"  {v * 1000:.0f} ms" if v < 1 else f"  {v:.2f} s", va="center", fontsize=8, color=INK_2)
        ax.set_yticks(list(ys))
        ax.set_yticklabels([l for l, _, _ in vals] if ax is axes[0] else [""] * len(vals), fontsize=8.5)
        ax.invert_yaxis()
        ax.set_xlim(0, max(v for _, _, v in vals) * 1.32)
        ax.set_xticks([])
        ax.set_title(title, fontsize=10, loc="left", color=INK)
        ax.spines[["top", "right", "bottom"]].set_visible(False)
        ax.spines["left"].set_color("#c9c8c0")
        ax.tick_params(axis="y", length=0)
    fig.suptitle("One flow at a time: cold, from nothing running to JSON on stdout (median)",
                 fontsize=11.5, x=0.005, y=1.02, ha="left", color=INK)
    fig.tight_layout()
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{fig_prefix}single-flow-cold.{ext}", bbox_inches="tight", dpi=160 if ext == "png" else None)
    plt.close(fig)
    print(f"wrote figures/{fig_prefix}single-flow-cold.svg")


def main():
    # `plot.py <prefix> [figure-prefix] [task=prefix ...]`: a task=prefix
    # argument takes that task's runs from another dataset (v13 re-ran only
    # retail on the v11 binary: `plot.py v11 retail=v13`). The memory figure
    # uses the override too.
    args = [a for a in sys.argv[1:] if "=" not in a]
    overrides = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    prefix = args[0] if args else "stock"
    fig_prefix = args[1] if len(args) > 1 else ""
    if prefix == "par":
        for mode in ("cold", "warm"):
            draw_parallel(prefix, mode=mode, fig_prefix=fig_prefix)
            draw_parallel(prefix, mode=mode, fig_prefix=fig_prefix, zoom_s=1.6)
            draw_parallel(prefix, task="scrape", mode=mode, fig_prefix=fig_prefix)
        for task in ("login_fx", "scrape"):
            draw_parallel_memory(prefix, task=task, fig_prefix=fig_prefix)
            draw_parallel_memory(prefix, task=task, fig_prefix=fig_prefix, zoom=True)
        return
    for task, title in TASKS.items():
        draw(task, title, overrides.get(task, prefix), fig_prefix)
    draw_memory(prefix, fig_prefix, overrides)
    draw_single_flow(prefix, overrides, fig_prefix)


if __name__ == "__main__":
    main()
