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

# Its own server, so the user's tmux sessions and config stay out of it. The
# fixture runs in it too, so killing the server takes everything down.
t() { tmux -L jev-screencast -f /dev/null "$@"; }

t kill-server 2>/dev/null || true
[[ ${1:-} == stop ]] && exit 0

# Throwaway credentials the fixture accepts; safe to show on screen. Exported
# before the server starts, which is when tmux takes its environment.
export LP_USERNAME=bench_user LP_PASSWORD=bench_pass

t new-session -d -s fixture "uv run --no-project python $HERE/../pandascript-vs-cdp/harness/login_fixture.py $PORT"
for _ in $(seq 50); do
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
  sleep 0.1
done

# Explicit flags win over any .lp-agent.zon remembered in this directory.
flags="--verbosity medium --log-level err --log-filter note --url $URL"
mkdir -p "$HERE/results"
jev="$BIN agent --no-llm $flags --trace $HERE/results/screencast-jev.jsonl"
chat="env -u TYPESAFE_API_KEY $BIN agent --provider anthropic --model claude-haiku-5-5 $flags --trace $HERE/results/screencast-chat.jsonl"

t new-session -d -s race -x "$(tput cols 2>/dev/null || echo 200)" -y "$(tput lines 2>/dev/null || echo 50)" "$jev"
t split-window -h -t race "$chat"
t select-pane -t race.0 -T "Jev" \; select-pane -t race.1 -T "Claude Haiku 5.5 (no TypeSafe key)"
# Both panes take the keystrokes, so neither border marks one as active.
t set -g status off \; set -g default-terminal tmux-256color \; set -gq allow-set-title off \; \
  set -g pane-border-status top \; set -g pane-border-format '#[fg=#f7f7ef,bold] #{pane_title} #[default]' \; \
  set -g pane-border-style 'fg=#3a3a3a' \; set -g pane-active-border-style 'fg=#3a3a3a' \; \
  set -as terminal-features ',*:RGB' \; setw -t race synchronize-panes on
exec tmux -L jev-screencast -f /dev/null attach -t race
