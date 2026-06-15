#!/usr/bin/env bash
# cc_close.sh — invoked by the /cc-close Claude Code slash command (macOS port of
# cc_close.ps1). Identifies THIS Claude session by its tmux session name and drops
# close-markers/<session-name>. The cc-discord-remote bot's _close_marker_watcher picks
# it up within ~2s and closes the session + its tmux session cleanly, with no
# auto-resume. This script does NOT kill anything itself, so the agent stays responsive
# until the bot tears it down.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER_DIR="$DIR/close-markers"
mkdir -p "$MARKER_DIR"

SESSION="$(tmux display-message -p '#S' 2>/dev/null || true)"
if [ -z "$SESSION" ]; then
  echo 'cc-close: not running inside a tmux session — cannot locate this terminal.'
  exit 1
fi

: > "$MARKER_DIR/$SESSION"
echo "cc-close: marked session '$SESSION' for close. This tab will close in ~2s."
