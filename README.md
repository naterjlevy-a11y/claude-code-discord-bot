# claude-code-discord-bot

![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)
![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Windows-lightgrey.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)

Drive [Claude Code](https://claude.com/claude-code) running on your laptop from a Discord channel on your phone. Type a task in Discord → it runs on your machine → the response streams back. Tool actions (Edit / Write / Bash) surface as tappable **Approve / Deny** buttons. Cross-platform: a **macOS** implementation (pseudo-terminal + screen emulation) and the original **Windows** implementation (Win32 console APIs).

```
┌─────────┐   Discord    ┌──────────┐   pty / Win32 console    ┌──────────────┐
│ phone   │ ───────────► │   bot    │ ───── inject keys ─────► │ Claude Code  │
│ (you)   │ ◄─────────── │ (Python) │ ◄──── read output ────── │ on laptop    │
└─────────┘              └──────────┘                          └──────────────┘
```

## Why this exists

Claude Code ships a built-in `/remote-control`, but it requires the phone's Claude account to match the laptop's. If your phone uses a different account, that feature is unusable. This bot sidesteps the problem: the phone authenticates to *Discord*, the bot runs locally and talks to Claude Code using the laptop's existing credentials.

## Two ways to run

### 1. SDK mode (recommended — clean & reliable)
In a channel, just type a task. The bot runs a real Claude Code turn via `claude-agent-sdk` in the channel's working directory, streams the response back, and pops **Approve / Deny** buttons before any Edit / Write / Bash. Read-only tools auto-run.

```
!cc <prompt>          run a task (or just type in the channel)
!cc cd <path>         set the working directory
!cc new               start a fresh session
!cc sessions          list past sessions
!cc resume <id>       continue a past session
!cc status | cancel   inspect / stop the current turn
```

### 2. Live terminal mode (drive the real TUI)
Spawn a real `claude` TUI the bot owns, in its own `#channel`. Type → keystrokes are injected → the bot posts the rendered screen back. A keypad (arrows / Enter / Esc / 1–5) handles pickers and approval popups.

```
!cc launch <name> [cwd]   spawn a TUI session in a new channel
!cc attach <name|pid>     attach to a running session
!cc look | pad            print the screen / show the keypad
!cc keys down,down,enter  send a sequence of keys
!cc get <path>            upload a file from the session folder
!cc close                 end the session
```

## Platform implementations

| Concern | Windows (original) | macOS (port) |
|---|---|---|
| Inject keystrokes | Win32 `AttachConsole` / `WriteConsoleInput` | `pty.fork()` — bot owns the pseudo-terminal |
| Read output | `ReadConsoleOutputCharacter` + JSONL tail | [`pyte`](https://pypi.org/project/pyte/) terminal emulator renders the screen |
| Process liveness | `tasklist` / `taskkill` / `OpenProcess` | `os.kill` + the `~/.claude/sessions` registry |
| Entry point | `bot.py` | `bot_mac.py` |

**macOS runtime note:** recent Claude Code builds don't always write the per-session JSONL transcript that the Windows path tails, so live-terminal mode on macOS is **screen-based** (it scrapes the emulated screen after each message). That works, but very long responses can scroll off-screen. **For substantial tasks, prefer SDK mode** — it returns full structured output and proper approval buttons.

## Quick start (macOS)

```bash
# Python 3.12+ recommended (the SDK needs >= 3.10). uv makes this painless:
uv venv && uv pip install -r requirements.txt

cp .env.example .env        # then fill in your Discord token + IDs
./run-mac.sh                # foreground; pkill -f bot_mac.py to stop
```

For start-on-login, add a `launchd` LaunchAgent that runs `run-mac.sh`.

## Quick start (Windows)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env       # fill in token + IDs
python bot.py
```

## Configuration

All config lives in `.env` (see `.env.example`):

- `DISCORD_TOKEN` — your bot token (Developer Portal → Bot → Reset Token; enable **Message Content Intent**).
- `ALLOWED_USER_IDS` — comma-separated Discord user IDs allowed to drive the bot.
- `ALLOWED_CHANNEL_IDS` — channels the bot responds in (empty = any).
- `DEFAULT_CWD` — default working directory for new sessions.

## Security

- The bot only obeys `ALLOWED_USER_IDS` in `ALLOWED_CHANNEL_IDS`. Set these.
- Approving a tool runs real commands and edits real files on the host machine. Approve deliberately.
- `DISCORD_TOKEN` is a full credential. `.env` is git-ignored — never commit it. If a token is ever exposed, reset it in the Developer Portal.

## Credits

The Windows implementation and original design are by **[Reuben Lavin](https://github.com/reubenlavin08/cc-discord-remote)**. This repository adds a full **macOS port** (`bot_mac.py`, `mac_console.py`, `mac_live.py`, `mac_terminal.py`) built on `pty` + `pyte`. MIT licensed — see [LICENSE](LICENSE).
