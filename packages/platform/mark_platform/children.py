"""The spawner's own record of the child agents a shell-executing agent started (fix B3, 2026-09-14).

`markcall spawn` runs in a short-lived tool process, so the agent adapter never holds the child's Popen the way the
scripted agent does. The tool appends one line per child to MARK_CHILDREN_FILE, a file the harness names per scenario.
The adapter reads it back when it describes itself, and ks.propagation takes `children_spawned` from that description,
as it does from the scripted agent's own list. The child's EFFECTS are attributed separately, by the process that made
them (fix A2); this record only says which children were started and whether each was still alive.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .clock import mono_ns

CHILDREN_FILE_ENV = "MARK_CHILDREN_FILE"


def record_child(path: str | Path, pid: int, *, n: int, spacing_ms: int, prefix: str) -> dict[str, Any]:
    rec = {"pid": int(pid), "n": n, "spacing_ms": spacing_ms, "prefix": prefix, "spawned_mono_ns": mono_ns(), "spawner_pid": os.getpid()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def alive(pid: int) -> bool | None:
    """Whether a process this one did not start is still running. None where it cannot be asked safely: on Windows
    os.kill(pid, 0) terminates the process instead of probing it."""
    if os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # an orphan nobody reaps (a container whose pid 1 is not an init) stays a zombie: it exists and is not running
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def describe_children(path: str | Path | None) -> dict[str, Any]:
    """The record as the adapter reports it. A line that does not parse is counted, never read as a child."""
    children: list[dict[str, Any]] = []
    unreadable = 0
    p = Path(path) if path else None
    if p is not None and p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                pid = int(rec["pid"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                unreadable += 1
                continue
            children.append({**rec, "alive": alive(pid)})
    return {"children": children, "children_record_unreadable": unreadable}
