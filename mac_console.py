"""macOS console layer — the pty+pyte replacement for the Windows console_helper.py.

The Windows original attaches to an already-running console with Win32 APIs
(AttachConsole / WriteConsoleInput / ReadConsoleOutputCharacter). macOS has no such
"attach to a foreign console" primitive, so instead the bot *owns* each Claude Code
process: it spawns `claude` inside a pseudo-terminal (pty) it controls. Writing bytes
to the pty master = injecting keystrokes (WriteConsoleInput); a background reader feeds
all output into a pyte terminal emulator whose rendered screen we read on demand
(ReadConsoleOutputCharacter).

Because `claude` is a single native binary, `pty.fork()` execs it directly — so the
child PID is exactly the Claude PID written into ~/.claude/sessions/<pid>.json, which
keeps this layer in lockstep with the cross-platform session registry / JSONL tail.

Key tokens match the Windows helper's vocabulary (enter/esc/up/down/left/right/tab/
backspace/space/single chars). On a real pty arrows are just VT escape sequences, so
no synthetic key-event machinery is needed.
"""

import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import termios
import threading
import time
from typing import Dict, List, Optional

import pyte

DEFAULT_COLS = 120
DEFAULT_ROWS = 42

# Named keys → the bytes a real terminal sends. Arrows are VT escape sequences
# (the Ink/React TUI reads navigation from these, exactly like the Windows path).
KEY_BYTES = {
    "enter": "\r", "return": "\r",
    "esc": "\x1b", "escape": "\x1b",
    "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D",
    "tab": "\t",
    "backspace": "\x7f", "bksp": "\x7f",
    "space": " ",
}


class PtySession:
    """One Claude Code process running in a pty the bot owns."""

    def __init__(self, pid: int, master_fd: int, cwd: str, name: Optional[str],
                 cols: int, rows: int):
        self.pid = pid
        self.fd = master_fd
        self.cwd = cwd
        self.name = name
        self.started_at = time.time()
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self._lock = threading.Lock()
        self._stop = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        while not self._stop:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not r:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            with self._lock:
                self.stream.feed(data.decode("utf-8", "replace"))

    def snapshot(self) -> str:
        """Current rendered screen as text, trailing blank lines trimmed."""
        with self._lock:
            lines = [ln.rstrip() for ln in self.screen.display]
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def _write(self, data: str):
        try:
            os.write(self.fd, data.encode("utf-8"))
        except OSError:
            pass

    def send_keys(self, sequence: str):
        """Comma-separated key tokens, no trailing Enter. e.g. `down,down,enter`, `1`."""
        for raw in sequence.split(","):
            tok = raw.strip()
            if not tok:
                continue
            low = tok.lower()
            if low in KEY_BYTES:
                self._write(KEY_BYTES[low])
            elif len(tok) == 1:
                self._write(tok)
            else:
                raise ValueError(f"Unknown key token: {tok!r}")
            time.sleep(0.06)

    def send_esc(self):
        self._write("\x1b")

    def send_enter(self):
        self._write("\r")

    def type_text(self, text: str, submit: bool = True):
        """Type text into the TUI input, then Enter to submit (matches helper 'type')."""
        # Chunk so a long paste doesn't overflow the line discipline buffer.
        for i in range(0, len(text), 80):
            self._write(text[i:i + 80])
            time.sleep(0.02)
        time.sleep(0.15)
        if submit:
            self._write("\r")

    def alive(self) -> bool:
        return pid_alive(self.pid)

    def close(self):
        self._stop = True
        try:
            os.close(self.fd)
        except OSError:
            pass


# ---------- module-level manager -------------------------------------------

_sessions: Dict[int, PtySession] = {}


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def claude_path() -> str:
    return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def spawn(argv: List[str], cwd: Optional[str], name: Optional[str] = None,
          cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> PtySession:
    """Fork a pty and exec argv inside it. Returns a PtySession keyed by the child PID
    (which, for the single-binary `claude`, equals the session-registry PID)."""
    exe = argv[0]
    if exe == "claude":
        exe = claude_path()
        argv = [exe] + argv[1:]

    pid, fd = pty.fork()
    if pid == 0:
        # ---- child ----
        try:
            if cwd and os.path.isdir(cwd):
                os.chdir(cwd)
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLUMNS"] = str(cols)
            os.environ["LINES"] = str(rows)
            # Ensure ~/.local/bin is reachable for any helper the binary calls.
            local_bin = os.path.expanduser("~/.local/bin")
            os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")
            os.execv(exe, argv)
        except Exception:
            os._exit(127)
    # ---- parent ----
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except OSError:
        pass
    sess = PtySession(pid, fd, cwd or os.getcwd(), name, cols, rows)
    _sessions[pid] = sess
    return sess


def get(pid: int) -> Optional[PtySession]:
    return _sessions.get(pid)


def all_sessions() -> List[PtySession]:
    return [s for s in _sessions.values()]


def reap_dead() -> List[int]:
    """Remove sessions whose process has exited. Returns the dead PIDs."""
    dead = []
    for pid, sess in list(_sessions.items()):
        # Reap zombies so liveness reflects reality.
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                dead.append(pid)
        except ChildProcessError:
            if not pid_alive(pid):
                dead.append(pid)
        except OSError:
            pass
        if pid not in dead and not pid_alive(pid):
            dead.append(pid)
    for pid in dead:
        s = _sessions.pop(pid, None)
        if s:
            s.close()
    return dead


def kill(pid: int) -> bool:
    """Terminate a session's process tree. Returns True if it's gone afterward."""
    sess = _sessions.pop(pid, None)
    if sess:
        sess.close()
    if not pid_alive(pid):
        return True
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except OSError:
            break
        for _ in range(15):
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        if not pid_alive(pid):
            break
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:
        pass
    return not pid_alive(pid)
