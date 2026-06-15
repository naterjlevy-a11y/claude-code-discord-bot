"""tmux transport layer — the macOS replacement for the Windows console_helper.py.

The Windows original attaches to an already-running console with Win32 APIs
(AttachConsole / WriteConsoleInput / ReadConsoleOutputCharacter). macOS has no
"attach to a foreign console" primitive, so instead every Claude Code session runs
inside its own **tmux session** that lives independently of the bot. Driving a session
is then just:

  * inject keystrokes   → `tmux send-keys`        (WriteConsoleInput)
  * read the screen     → `tmux capture-pane -p`  (ReadConsoleOutputCharacter)

Because the tmux server outlives the bot, terminals survive a bot restart / crash —
which is exactly what the reboot-restore + auto-resume machinery in the bot depends on
(the Win32 design relied on standalone consoles / Windows-Terminal tabs surviving the
same way).

Sessions are named `cc-<sanitized>` (optionally with a short random suffix to avoid
collisions). The bot still tracks everything by the **claude PID** (so the shared
session registry / JSONL tail in live_processes.py + session_files.py work unchanged);
`target_for_pid()` bridges a claude PID back to the tmux session that hosts it.

Every function here is blocking (it shells out to `tmux` / `ps`); the bot wraps the
calls in `asyncio.to_thread` so the event loop never stalls.
"""

import os
import secrets
import shutil
import subprocess
import time
from typing import Dict, List, Optional

SESSION_PREFIX = "cc-"
PANE_WIDTH = 200
PANE_HEIGHT = 50

# Named key tokens → the tmux key name `send-keys` understands. Single printable
# characters are sent literally with `-l` instead (see send_key_sequence). Arrows map
# to tmux's Up/Down/Left/Right, which tmux emits to the app as the proper VT escape
# sequences — exactly what Claude Code's Ink/React TUI reads navigation from.
_KEY_NAMES = {
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "tab": "Tab",
    "space": "Space",
    "backspace": "BSpace",
    "bksp": "BSpace",
}


# ---------- low-level tmux helpers ------------------------------------------

def _run(args: List[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _ok(args: List[str], timeout: float = 10.0) -> bool:
    try:
        return _run(args, timeout=timeout).returncode == 0
    except Exception:
        return False


def tmux_available() -> bool:
    """True iff a `tmux` binary is on PATH (and answers a trivial command)."""
    if shutil.which("tmux") is None:
        return False
    try:
        return _run(["tmux", "-V"]).returncode == 0
    except Exception:
        return False


# ---------- session naming --------------------------------------------------

def sanitize(raw: str) -> str:
    out = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (raw or "").lower())
    return out.strip("-")[:40] or "claude"


def session_name(raw: str) -> str:
    """Deterministic `cc-<sanitized>` name for a raw label."""
    return f"{SESSION_PREFIX}{sanitize(raw)}"


def unique_session_name(raw: str) -> str:
    """A `cc-<sanitized>` name guaranteed not to clash with a live tmux session."""
    base = session_name(raw)
    if not has_session(base):
        return base
    for _ in range(1000):
        cand = f"{base}-{secrets.token_hex(2)}"
        if not has_session(cand):
            return cand
    return f"{base}-{secrets.token_hex(8)}"


# ---------- session lifecycle -----------------------------------------------

def has_session(name: str) -> bool:
    return _ok(["tmux", "has-session", "-t", name])


def new_session(name: str, cwd: Optional[str], command: str = "claude") -> bool:
    """Create a detached tmux session `name` running `command` (default `claude`).

    Mirrors `tmux new-session -d -s NAME -x 200 -y 50 -c CWD claude`. The wide
    (-x/-y) geometry matters because a detached session otherwise defaults to 80x24,
    which would wrap Claude's TUI and corrupt the screen captures. `command` is passed
    as a single argument so tmux runs it via `/bin/sh -c` when it carries args (e.g.
    `claude --resume <id>`); the claude PID then lands a level under the pane process,
    which `target_for_pid` resolves by walking the ppid chain.
    """
    args = ["tmux", "new-session", "-d", "-s", name,
            "-x", str(PANE_WIDTH), "-y", str(PANE_HEIGHT)]
    if cwd and os.path.isdir(cwd):
        args += ["-c", cwd]
    args.append(command)
    return _ok(args)


def kill_session(name: str) -> bool:
    """Kill a tmux session by name. Returns True if it's gone afterwards."""
    if not name:
        return False
    _ok(["tmux", "kill-session", "-t", name])
    time.sleep(0.2)
    return not has_session(name)


def list_sessions() -> List[str]:
    try:
        proc = _run(["tmux", "list-sessions", "-F", "#{session_name}"])
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def list_cc_sessions() -> List[str]:
    """Every tmux session this tool manages (cc- prefixed)."""
    return [s for s in list_sessions() if s.startswith(SESSION_PREFIX)]


# ---------- screen capture --------------------------------------------------

def capture(target: str) -> str:
    """Render the visible pane of `target` to text, trailing blank lines trimmed.

    `target` may be a session name (`cc-foo`) or any tmux target spec.
    """
    if not target:
        return ""
    try:
        proc = _run(["tmux", "capture-pane", "-p", "-t", target])
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    lines = [ln.rstrip() for ln in proc.stdout.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# ---------- input injection -------------------------------------------------

def send_text(target: str, text: str, submit: bool = True) -> None:
    """Type `text` into the pane. Newlines become Enter keypresses (matching the
    Windows helper); a trailing Enter is appended when `submit` so the TUI sends it."""
    if not target:
        return
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            # `-l` = literal; `--` so a line starting with `-` isn't read as a flag.
            _ok(["tmux", "send-keys", "-t", target, "-l", "--", line])
        if i < len(lines) - 1:
            _ok(["tmux", "send-keys", "-t", target, "Enter"])
    if submit:
        # Brief beat so the TUI processes the typed chars before the submit Enter.
        time.sleep(0.15)
        _ok(["tmux", "send-keys", "-t", target, "Enter"])


def send_key_sequence(target: str, sequence: str) -> None:
    """Send a comma-separated key sequence like `down,down,enter` or `1`.

    Named tokens (enter/esc/up/down/left/right/tab/space/backspace) map to tmux key
    names; any single printable character is sent literally. Unknown multi-char tokens
    raise ValueError (surfaced by the bot as an error)."""
    if not target:
        return
    for raw in sequence.split(","):
        tok = raw.strip()
        if not tok:
            continue
        low = tok.lower()
        if low in _KEY_NAMES:
            _ok(["tmux", "send-keys", "-t", target, _KEY_NAMES[low]])
        elif len(tok) == 1:
            _ok(["tmux", "send-keys", "-t", target, "-l", "--", tok])
        else:
            raise ValueError(f"Unknown key token: {tok!r}")
        time.sleep(0.06)


def send_esc(target: str) -> None:
    if target:
        _ok(["tmux", "send-keys", "-t", target, "Escape"])


def send_enter(target: str) -> None:
    if target:
        _ok(["tmux", "send-keys", "-t", target, "Enter"])


# ---------- pid → tmux session resolution -----------------------------------

def _pane_pid_to_session() -> Dict[int, str]:
    """Map every tmux pane's process pid → its session name."""
    out: Dict[int, str] = {}
    try:
        proc = _run(["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}"])
    except Exception:
        return out
    if proc.returncode != 0:
        return out
    for line in proc.stdout.splitlines():
        sess, _, pid = line.partition("\t")
        pid = pid.strip()
        if pid.isdigit():
            out[int(pid)] = sess.strip()
    return out


def _ppid_map() -> Dict[int, int]:
    """pid → ppid for every process, via `ps -axo pid=,ppid=`."""
    out: Dict[int, int] = {}
    try:
        proc = _run(["ps", "-axo", "pid=,ppid="])
    except Exception:
        return out
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            out[int(parts[0])] = int(parts[1])
    return out


def target_for_pid(pid: int) -> Optional[str]:
    """Find the tmux session whose pane hosts claude `pid`.

    The claude process is usually the pane's own process (when tmux exec's `claude`
    directly), but with a multi-word command it sits a level under a `/bin/sh -c`
    pane process. Walk the pid's ppid chain upward until it matches a known pane pid,
    and return that pane's session name."""
    if not pid:
        return None
    panes = _pane_pid_to_session()
    if not panes:
        return None
    if pid in panes:
        return panes[pid]
    ppid = _ppid_map()
    cur = pid
    for _ in range(25):
        if cur in panes:
            return panes[cur]
        nxt = ppid.get(cur)
        if not nxt or nxt == cur or nxt <= 1:
            break
        cur = nxt
    return None
