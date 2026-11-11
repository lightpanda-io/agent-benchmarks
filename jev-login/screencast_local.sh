#!/usr/bin/env bash
# The /login race with a local decider: StartLux-Decision on llama.cpp on the
# left, Claude Haiku 5.5 on the right, and below them llama-server's log and
# btop's GPU panel, so the decision is seen running on this machine.
#
#   ./screencast_local.sh        start the fixture and attach to the session
#   ./screencast_local.sh stop   tear it down
#
# Expects the decision server on :8090 in front of llama-server, logging to
# $LLAMA_LOG (see "Local decider" in README.md). screencast_local.tape drives
# this under VHS.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
BIN=${BIN:-$HERE/../../browser/zig-out/bin/lightpanda}
PORT=${PORT:-9280}
URL="http://127.0.0.1:$PORT/login"
DECIDER=${DECIDER:-http://127.0.0.1:8090}
LLAMA_LOG=${LLAMA_LOG:-$HOME/startlux/llama2b.log}
MODEL=${MODEL:-StartLux-Decision-2B-Q8_0}

t() { tmux -L jev-local -f /dev/null "$@"; }

t kill-server 2>/dev/null || true
[[ ${1:-} == stop ]] && exit 0

curl -sf "$DECIDER/" >/dev/null || { echo "no decision server at $DECIDER" >&2; exit 1; }

export LP_USERNAME=bench_user LP_PASSWORD=bench_pass
# An ssh session may carry no locale, and the REPL then draws its logo and
# prompt in ASCII.
export LANG=${LANG:-en_US.UTF-8}

t new-session -d -s fixture "uv run --no-project python $HERE/../pandascript-vs-cdp/harness/login_fixture.py $PORT"
for _ in $(seq 50); do
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
  sleep 0.1
done

# btop with the GPU box only, from a copy of the user's config so theirs is
# left alone; 100 ms updates so a sub-second decision shows up at all.
BTOP_HOME=$(mktemp -d)
mkdir -p "$BTOP_HOME/btop"
{ grep -vE '^(shown_boxes|update_ms) ' "$HOME/.config/btop/btop.conf" 2>/dev/null || true
  echo 'shown_boxes = "gpu0"'
  echo 'update_ms = 100'; } > "$BTOP_HOME/btop/btop.conf"

flags="--verbosity medium --log-level err --log-filter note --url $URL"
mkdir -p "$HERE/results"
local_arm="env TYPESAFE_BASE_URL=$DECIDER TYPESAFE_API_KEY=local $BIN agent --no-llm $flags --trace $HERE/results/screencast-local.jsonl"
chat="env -u TYPESAFE_API_KEY $BIN agent --provider anthropic --model claude-haiku-5-5 $flags --trace $HERE/results/screencast-local-chat.jsonl"
# One line per forward pass, trimmed to what fits half the screen.
log="tail -n0 -F $LLAMA_LOG | sed -u -nE 's/^([0-9.]+) .*task +([0-9]+) \\| prompt eval time = +([0-9.]+) ms \\/ +([0-9]+) tokens.*/\\1  task \\2  prompt eval \\3 ms · \\4 tokens/p'"
btop="env XDG_CONFIG_HOME=$BTOP_HOME btop --force-utf"

cols=$(tput cols 2>/dev/null || echo 200) rows=$(tput lines 2>/dev/null || echo 50)
top=$(t new-session -d -s race -x "$cols" -y "$rows" -P -F '#{pane_id}' "$local_arm")
strip=$(t split-window -v -l 26% -t "$top" -P -F '#{pane_id}' "$log")
right=$(t split-window -h -t "$top" -P -F '#{pane_id}' "$chat")
gpu=$(t split-window -h -t "$strip" -P -F '#{pane_id}' "$btop")

t select-pane -t "$top" -T "$MODEL · local llama.cpp" \; \
  select-pane -t "$right" -T "Claude Haiku 5.5" \; \
  select-pane -t "$strip" -T "llama-server log" \; \
  select-pane -t "$gpu" -T "btop · GPU"
# Typed keys go to both REPLs and to neither monitor.
t select-pane -t "$strip" -d \; select-pane -t "$gpu" -d \; select-pane -t "$top"
t set -g status off \; set -g default-terminal tmux-256color \; set -gq allow-set-title off \; \
  set -g pane-border-status top \; set -g pane-border-format '#[fg=#f7f7ef,bold] #{pane_title} #[default]' \; \
  set -g pane-border-style 'fg=#3a3a3a' \; set -g pane-active-border-style 'fg=#3a3a3a' \; \
  set -as terminal-features ',*:RGB' \; setw -t race synchronize-panes on
exec tmux -L jev-local -f /dev/null attach -t race
