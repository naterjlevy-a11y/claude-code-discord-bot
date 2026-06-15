#!/usr/bin/env bash
# Launch the macOS Discord bot with auto-restart. Keeps ~/.local/bin (the `claude` CLI for
# the agent SDK) and Homebrew's bin (the `tmux` transport) on PATH, even when auto-started
# by launchd / a login item where the default PATH is minimal. If the bot exits (e.g. a
# transient DNS / network blip that kills the gateway loop), it's relaunched after a backoff.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

# `caffeinate -s` prevents IDLE system sleep while the bot runs (so it stays reachable on
# AC power). NOTE: closing the lid still sleeps a laptop unless an external display is
# attached OR sleep is disabled — see README/run notes (`sudo pmset -a disablesleep 1`).
CAFFEINATE=""
command -v caffeinate >/dev/null 2>&1 && CAFFEINATE="caffeinate -s"

while true; do
  $CAFFEINATE .venv/bin/python bot_mac.py
  code=$?
  # Exit 0 = clean stop (e.g. SystemExit from a config error) — don't loop forever on that.
  if [ "$code" -eq 0 ]; then
    echo "[run-mac] bot exited cleanly (code 0); stopping supervisor." >&2
    break
  fi
  echo "[run-mac] bot exited (code $code) — restarting in 5s…" >&2
  sleep 5
done
