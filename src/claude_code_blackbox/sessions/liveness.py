"""Decide whether the process behind a session record still runs, the way Claude Code decides it."""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path


def _proc_stat_start(pid: int) -> str | None:
    """Return field 22 (starttime, in jiffies) of ``/proc/<pid>/stat`` as Claude Code reads it, or ``None``."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # comm (field 2) is in parentheses and may contain spaces, so count fields from the last ")".
    fields = stat[stat.rfind(")") + 2 :].split(" ")
    return fields[19] if len(fields) > 19 else None


@cache
def own_pid_domain() -> str | None:
    """Return the ``pidDomain`` value Claude Code would write from this machine, or ``None`` on Windows.

    On Linux it is ``linux:<machine-id>:<pid namespace>``. On macOS Claude Code writes the constant ``linux``.
    """
    if sys.platform == "win32":
        return None
    if not sys.platform.startswith("linux"):
        return "linux"
    try:
        machine_id = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
        namespace = os.readlink("/proc/self/ns/pid")
    except OSError:
        return None
    return f"linux:{machine_id}:{namespace}"


def is_session_process_alive(pid: int, proc_start: str | None, pid_domain: str | None) -> bool:
    """Return whether ``pid`` runs and is still the process that wrote the record.

    A record from another pid domain (another container or pid namespace) returns ``False``, because its pid means
    nothing here. On Linux the record's ``procStart`` must equal the current start time of ``pid``, which catches a
    reused pid. On macOS only the pid is checked. On Windows nothing is checked and the result is ``True``:
    ``os.kill`` there terminates the process instead of probing it.
    """
    if sys.platform == "win32":
        return True
    own_domain = own_pid_domain()
    if pid_domain is not None and own_domain is not None and pid_domain != own_domain:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # the process exists but belongs to another user
    except OSError:
        return False
    if proc_start is not None and sys.platform.startswith("linux"):
        return _proc_stat_start(pid) == proc_start
    return True
