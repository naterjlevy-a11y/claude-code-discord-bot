"""macOS port of live_processes.py — list running Claude Code sessions.

Reads the same ~/.claude/sessions/*.json registry the Windows version does (Claude
writes it identically on macOS), but checks liveness with os.kill(pid, 0) instead of
the Win32 OpenProcess handle. On macOS the *drivable* sessions are the ones the bot
spawned in a pty (see mac_console); a registry entry with no owned pty can be listed
but not have keystrokes injected into it.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import mac_console

REGISTRY = Path.home() / ".claude" / "sessions"


@dataclass
class LiveClaude:
    pid: int
    session_id: str
    cwd: str
    status: str
    name: Optional[str] = None
    started_at_ms: Optional[int] = None
    owned: bool = False  # True if the bot owns this session's pty (so it's drivable)


def pid_alive(pid: int) -> bool:
    return mac_console.pid_alive(pid)


def list_running() -> List[LiveClaude]:
    """Live Claude sessions from the registry. Bot-owned (pty) sessions are marked
    owned=True and sorted first so attach/spawn prefer drivable ones."""
    owned_pids = {s.pid for s in mac_console.all_sessions()}
    out: List[LiveClaude] = []
    if REGISTRY.is_dir():
        for f in REGISTRY.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            pid = data.get("pid")
            if not pid or not pid_alive(pid):
                continue
            out.append(
                LiveClaude(
                    pid=pid,
                    session_id=data.get("sessionId", "?"),
                    cwd=data.get("cwd", "?"),
                    status=data.get("status", "?"),
                    name=data.get("name"),
                    started_at_ms=data.get("startedAt"),
                    owned=pid in owned_pids,
                )
            )
    # Include owned pty sessions that haven't written a registry json yet (just spawned).
    seen = {c.pid for c in out}
    for s in mac_console.all_sessions():
        if s.pid in seen or not s.alive():
            continue
        out.append(LiveClaude(
            pid=s.pid, session_id="?", cwd=s.cwd, status="starting",
            name=s.name, started_at_ms=int(s.started_at * 1000), owned=True,
        ))
    out.sort(key=lambda c: (not c.owned, -(c.started_at_ms or 0)))
    return out


def find_by_pid(pid: int) -> Optional[LiveClaude]:
    for c in list_running():
        if c.pid == pid:
            return c
    return None


def cwd_to_encoded(cwd: str) -> str:
    """Claude Code's ~/.claude/projects/<encoded>/ encoding — non-alnum becomes '-'."""
    return "".join(ch if ch.isalnum() else "-" for ch in cwd)


def session_jsonl_path(cwd: str, session_id: str) -> Path:
    return Path.home() / ".claude" / "projects" / cwd_to_encoded(cwd) / f"{session_id}.jsonl"
