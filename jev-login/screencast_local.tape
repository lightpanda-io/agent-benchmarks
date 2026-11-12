# The /login race with a local decider: StartLux-Decision on llama.cpp on the
# left, Claude Haiku 5.5 on the right, llama-server's log and btop below.
#
#   vhs screencast_local.tape && ./screencast_local.sh stop && uv run python screencast_timers.py --layout local
#
# Run on the machine with the GPU. Needs ANTHROPIC_API_KEY in the environment.

Output results/jev-login-local-race.mp4

Require tmux
Require uv
Require btop

Set Shell "bash"
Set FontFamily "Fira Code"
Set FontSize 15
Set Width 1920
Set Height 1080
Set Padding 28
Set TypingSpeed 45ms
Set Framerate 20
Set Theme { "name": "Lightpanda", "background": "#1c1c1c", "foreground": "#f7f7ef", "selection": "#3a3a3a", "cursor": "#6cc7e2", "black": "#1c1c1c", "red": "#e06666", "green": "#5fb872", "yellow": "#e3b341", "blue": "#00a4dd", "magenta": "#c678dd", "cyan": "#6cc7e2", "white": "#f7f7ef", "brightBlack": "#8a8a8a", "brightRed": "#e06666", "brightGreen": "#5fb872", "brightYellow": "#e3b341", "brightBlue": "#00a4dd", "brightMagenta": "#c678dd", "brightCyan": "#6cc7e2", "brightWhite": "#ffffff" }

Hide
Type "./screencast_local.sh"
Enter
Wait+Screen@30s /stream: on/
Show

Sleep 3s
Type "/login password=$LP_PASSWORD username=$LP_USERNAME"
Sleep 700ms
Enter
Wait+Screen@120s /worked for/
Sleep 3s
