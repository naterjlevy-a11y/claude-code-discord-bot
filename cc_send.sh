#!/usr/bin/env bash
# cc_send.sh <path> — invoked by the /cc-send slash command, or directly by the agent
# (macOS port of cc_send.ps1). Copies <path> into outbox/<session>__<filename>; the
# cc-discord-remote bot's _outbox_watcher uploads it to THIS session's Discord channel
# within ~3s, then deletes it. This is the push side (terminal → Discord); `!cc get` is
# the pull side (Discord asks).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTBOX="$DIR/outbox"
mkdir -p "$OUTBOX"

SRC="${1:-}"
if [ -z "$SRC" ]; then
  echo 'cc-send: usage: cc-send <path>'
  exit 1
fi
if [ ! -f "$SRC" ]; then
  echo "cc-send: file not found: $SRC"
  exit 1
fi

SESSION="$(tmux display-message -p '#S' 2>/dev/null || true)"
if [ -z "$SESSION" ]; then
  echo 'cc-send: not running inside a tmux session — cannot locate this terminal.'
  exit 1
fi

BASE="$(basename "$SRC")"
cp -f "$SRC" "$OUTBOX/${SESSION}__${BASE}"
echo "cc-send: queued '$BASE' for Discord (session '$SESSION'). It will appear in ~3s."
