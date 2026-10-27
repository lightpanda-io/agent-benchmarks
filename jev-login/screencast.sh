#!/usr/bin/env bash
# Two lightpanda REPLs side by side in tmux, on the HN-shaped fixture, with
# keystrokes mirrored to both panes: whatever is typed runs in both at once.
#
#   ./screencast.sh        start the fixture and attach to the session
#   ./screencast.sh stop   tear both down
#
# screencast.tape drives this under VHS; it works the same by hand.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
BIN=${BIN:-$HERE/../../browser/zig-out/bin/lightpanda}
PORT=${PORT:-9280}
URL="http://127.0.0.1:$PORT/login"
# Its own server, so the user's tmux sessions and config stay out of it.
TMUX_CMD=(tmux -L jev-screencast -f /dev/null)

stop() {
  "${TMUX_CMD[@]}" kill-server 2>/dev/null || true
  # By port: the fixture runs under uv, so its own pid is not the one we started.
  fuser -k "$PORT/tcp" >/dev/null 2>&1 || true
}

if [[ ${1:-} == stop ]]; then
  stop
  exit 0
fi

stop
uv run --no-project python "$HERE/../pandascript-vs-cdp/harness/login_fixture.py" "$PORT" >/dev/null 2>&1 &
for _ in $(seq 50); do
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
  sleep 0.1
done

# Throwaway credentials the fixture accepts; safe to show on screen.
export LP_USERNAME=bench_user LP_PASSWORD=bench_pass

# Explicit flags win over any .lp-agent.zon remembered in this directory.
flags="--verbosity medium --log-level err --log-filter note --url $URL"
# Each pane traces itself; screencast_timers.py reads the durations back.
mkdir -p "$HERE/results"
jev="$BIN agent --no-llm $flags --trace $HERE/results/screencast-jev.jsonl"
chat="env -u TYPESAFE_API_KEY $BIN agent --provider anthropic --model claude-haiku-5-5 $flags --trace $HERE/results/screencast-chat.jsonl"

cols=$(tput cols 2>/dev/null || echo 200)
lines=$(tput lines 2>/dev/null || echo 50)
"${TMUX_CMD[@]}" new-session -d -s race -x "$cols" -y "$lines" "$jev"
"${TMUX_CMD[@]}" split-window -h -t race "$chat"
"${TMUX_CMD[@]}" select-pane -t race.0 -T "Jev"
"${TMUX_CMD[@]}" select-pane -t race.1 -T "Claude Haiku 5.5 (no TypeSafe key)"
"${TMUX_CMD[@]}" set -g status off
"${TMUX_CMD[@]}" set -g default-terminal tmux-256color
"${TMUX_CMD[@]}" set -gq allow-set-title off
"${TMUX_CMD[@]}" set -g pane-border-status top
"${TMUX_CMD[@]}" set -g pane-border-format '#[fg=#f7f7ef,bold] #{pane_title} #[default]'
# Both panes take the keystrokes, so neither border marks one as active.
"${TMUX_CMD[@]}" set -g pane-border-style 'fg=#3a3a3a'
"${TMUX_CMD[@]}" set -g pane-active-border-style 'fg=#3a3a3a'
"${TMUX_CMD[@]}" set -as terminal-features ',*:RGB'
"${TMUX_CMD[@]}" setw -t race synchronize-panes on
exec "${TMUX_CMD[@]}" attach -t race
