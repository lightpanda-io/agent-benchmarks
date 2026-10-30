# The /login race as a video: Jev on the left, Claude Haiku 5.5 on the
# right, the same keystrokes going to both panes.
#
#   vhs screencast.tape && ./screencast.sh stop && uv run python screencast_timers.py
#
# Needs TYPESAFE_API_KEY and ANTHROPIC_API_KEY in the environment.

Output results/jev-login-race.mp4

Require tmux
Require uv

Set Shell "bash"
Set FontFamily "Fira Code"
Set FontSize 17
Set Width 1920
Set Height 1080
Set Padding 28
Set TypingSpeed 45ms
# Low enough that capture keeps up at 1080p: VHS spaces frames evenly, so a
# frame it is late for shortens the video and plays the run back too fast.
Set Framerate 20
# Terminal colours from the Lightpanda design system (term-* tokens).
Set Theme { "name": "Lightpanda", "background": "#1c1c1c", "foreground": "#f7f7ef", "selection": "#3a3a3a", "cursor": "#6cc7e2", "black": "#1c1c1c", "red": "#e06666", "green": "#5fb872", "yellow": "#e3b341", "blue": "#00a4dd", "magenta": "#c678dd", "cyan": "#6cc7e2", "white": "#f7f7ef", "brightBlack": "#8a8a8a", "brightRed": "#e06666", "brightGreen": "#5fb872", "brightYellow": "#e3b341", "brightBlue": "#00a4dd", "brightMagenta": "#c678dd", "brightCyan": "#6cc7e2", "brightWhite": "#ffffff" }

# The chat pane, the slower of the two to start, ends its banner with this.
Hide
Type "./screencast.sh"
Enter
Wait+Screen@30s /stream: on/
Show

Sleep 2s
Type "/login password=$LP_PASSWORD username=$LP_USERNAME"
Sleep 700ms
Enter
# The chat pane's turn summary, so the take lasts as long as the slower arm.
Wait+Screen@120s /worked for/
Sleep 2s
