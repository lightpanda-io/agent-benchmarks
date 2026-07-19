"""Strip plots of per-run benchmark timings, one SVG per live task.

Usage: uv run --with matplotlib python harness/plot.py [results-prefix]
Reads results/<prefix>-<task>-{cold,warm}/raw.jsonl (default prefix: stock).
"""

import json
import pathlib
import statistics
import sys

import matplotlib
import matplotlib.pyplot as plt

ROOT = pathlib.Path(__file__).parent.parent
OUT = ROOT / "figures"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
ACCENT = "#2a78d6"   # PandaScript rows
NEUTRAL = "#8a897f"  # other configurations (identity lives in the row labels)

ROWS = [
    ("pandascript", "PandaScript replay"),
    ("puppeteer-lightpanda", "Puppeteer → lightpanda serve"),
    ("playwright-lightpanda", "Playwright → lightpanda serve"),
    ("puppeteer-chrome", "Puppeteer → Chrome"),
    ("playwright-chrome", "Playwright → Chrome"),
]

TASKS = {
    "scrape": "Hacker News scrape — 6 page loads, live site",
    "retail": "Retail price monitoring (gymshark.com) — 4 page loads, live site",
    "news": "News monitoring (apnews.com) — 4 page loads, live site",
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
        for line in path.read_text().splitlines():
            r = json.loads(line)
            if r.get("warmup") or not r.get("ok"):
                continue
            runs.setdefault(r["config"], []).append(r["ms"] / 1000.0)
        return runs

    runs = read(ROOT / "results" / f"{prefix}-{task}-{mode}" / "raw.jsonl")
    # Warm-mode pandascript rows come from the -agentcache supplement when it
    # exists (persistent per-session cache dir — the warm-state analogue of a
    # held browser; same source as the published tables).
    supplement = ROOT / "results" / f"{prefix}-{task}-{mode}-agentcache" / "raw.jsonl"
    if mode == "warm" and supplement.exists():
        runs["pandascript"] = read(supplement)["pandascript"]
    return runs


def draw(task, title, prefix):
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 2.9), sharex=True, sharey=True)
    fig.patch.set_facecolor(SURFACE)

    xmax = 0.0
    for ax, mode in zip(axes, ("cold", "warm")):
        runs = load(task, mode, prefix)
        ax.set_facecolor(SURFACE)
        for y, (config, label) in enumerate(reversed(ROWS)):
            values = runs.get(config, [])
            if not values:
                continue
            xmax = max(xmax, max(values))
            color = ACCENT if config == "pandascript" else NEUTRAL
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

    axes[0].set_yticks(range(len(ROWS)))
    axes[0].set_yticklabels([label for _, label in reversed(ROWS)], fontsize=9.5)
    for tick, (config, _) in zip(axes[0].get_yticklabels(), reversed(ROWS)):
        tick.set_color(INK if config == "pandascript" else INK_2)
        if config == "pandascript":
            tick.set_fontweight("bold")
    axes[0].set_xlim(0, xmax * 1.06)
    for ax in axes:
        ax.set_xlabel("seconds per run", fontsize=9, color=INK_2)

    fig.suptitle(title, fontsize=11.5, x=0.005, y=1.02, ha="left", color=INK)
    fig.tight_layout()

    OUT.mkdir(exist_ok=True)
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{task}.{ext}", bbox_inches="tight",
                    facecolor=SURFACE, dpi=160)
    plt.close(fig)
    print(f"wrote figures/{task}.svg")


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "stock"
    for task, title in TASKS.items():
        draw(task, title, prefix)


if __name__ == "__main__":
    main()
