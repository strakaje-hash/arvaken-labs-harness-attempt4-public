"""Process identity from the OS, not the caller (attempt 4, A3) -- the instrument.

Until A3 a world call carried `X-Mark-Process: <role>:<pid>`, and both halves were the caller's: `markcall` said it was
the agent, `child_agent` said it was a child, and the pid was whatever the process typed. The harness never checked
either. A parent that named itself `child` would have had every effect counted as a child's.

The OS knows two things the caller cannot lie about, and both are readable from the process that RECEIVES the call:

  * **which process owns the socket the call arrived on.** A loopback TCP connection has an owning pid on the client
    side, and the receiving process can ask the kernel for it: `GetExtendedTcpTable` on Windows, `/proc/net/tcp` plus
    the `/proc/<pid>/fd` socket inodes on Linux. Reading another user's fd links needs `CAP_SYS_PTRACE`. *This line
    used to end "No privilege beyond running as the same user, which the harness does" -- true on the laptop, false
    on every Tier B pod, where agents run as `runner` and the harness as root without that capability: 64 of 64
    propagation replications went unattributed on attempt 4's first matrix run (TESTING.md R23).* Where the harness
    cannot read the owner itself, it asks a resolver running as the agents' user and checks the answer
    (`peer_resolver`, founder ruling 2026-09-23); the method on the record says which way it was done.
  * **that process's lineage.** Its parent, its session, and the chain of ancestors up to the agent process the harness
    launched -- `/proc/<pid>/stat` on Linux, the Toolhelp snapshot on Windows.

`resolve` combines the two into one record the world (or the gateway, for a call that passes through it) writes on
every call: the pid, its parent, whether it IS the agent process, whether it DESCENDS from it, and whether its parent
was still alive when it called. What the caller claimed rides beside it as a self-report (A2) and is checked against
it. What a *role* is -- which descendants act for the agent (a tool server, a `markcall`) and which act on their own (a
spawned sub-agent) -- is not decided here; this module states facts about processes and nothing about roles.

Resolution is synchronous and its cost is recorded on the call (`resolve_ms`). Where the OS cannot answer, the record
says so with a reason and `resolved: false`; a reader treats an unresolved call as unattributed, never as anyone's.
"""
from __future__ import annotations

import os
import socket
import struct
import time
from pathlib import Path
from typing import Any

MAX_ANCESTORS = 64
TCP_ESTABLISHED_WINDOWS = 5
TCP_ESTABLISHED_PROC = "01"

# The same-user resolver, when this run needs one (peer_resolver.start_if_needed). None: the harness reads owners itself.
_RESOLVER: Any = None
# The uid the agents run as, as the run established it (peer_resolver.start_if_needed). None: not established, and a process's
# user is then no evidence either way.
_AGENTS_UID: int | None = None


def set_resolver(resolver: Any) -> None:
    """Register (or clear, with None) the resolver the Linux owner lookup asks for sockets owned by another user."""
    global _RESOLVER
    _RESOLVER = resolver


def set_agents_uid(uid: int | None) -> None:
    """Register (or clear) the uid the run's agents run as: a caller of that user the OS cannot place in the agent's tree is
    'could not tell', never 'not ours' (founder ruling 2026-09-23)."""
    global _AGENTS_UID
    _AGENTS_UID = uid


def registered_session(pid: int) -> int | None:
    """The session id of the agent process at the moment the harness registers it -- while it is alive. A child orphaned when
    the agent exits keeps this session; the agent's own /proc entry is gone by then, so the session read live cannot vouch for
    it. None where there is no session to record (Windows), or where it is the harness's own session: a session the harness
    shares would place every harness process in the agent's tree."""
    if os.name == "nt":
        return None
    st = _stat_proc(pid)
    if st is None or st[1] in (0, 1):
        return None
    try:
        if st[1] == os.getsid(0):
            return None
    except OSError:
        pass
    return st[1]


# ---- who owns the peer socket -------------------------------------------------------------------------------------

def owner_pid(peer_port: int, *, server_port: int | None = None) -> tuple[int | None, str]:
    """The pid owning the loopback TCP socket whose LOCAL port is `peer_port` (the port the receiver sees as the
    client's), and the method used; (None, reason) when the OS did not answer. `server_port`, when given, must be
    the socket's remote port too, which excludes an unrelated connection that happens to share the port number."""
    if os.name == "nt":
        return _owner_pid_windows(peer_port, server_port)
    if Path("/proc/net/tcp").exists():
        return _owner_pid_proc(peer_port, server_port)
    return None, f"no socket-owner resolver on this platform ({os.name})"


def _owner_pid_windows(peer_port: int, server_port: int | None) -> tuple[int | None, str]:
    import ctypes
    import ctypes.wintypes as w

    iphlp = ctypes.windll.iphlpapi
    TCP_TABLE_OWNER_PID_ALL = 5
    for family, row_fmt, row_size, lp_off, rp_off, st_off, pid_off in ((2, "<LLLLLL", 24, 8, 16, 0, 20), (23, None, 56, 20, 44, 48, 52)):
        size = w.DWORD(0)
        iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_ALL, 0)
        buf = ctypes.create_string_buffer(size.value)
        rc = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_ALL, 0)
        if rc != 0:
            continue
        n = struct.unpack_from("<L", buf, 0)[0]
        for i in range(n):
            base = 4 + i * row_size
            st = struct.unpack_from("<L", buf, base + st_off)[0]
            lport = socket.ntohs(struct.unpack_from("<L", buf, base + lp_off)[0] & 0xFFFF)
            rport = socket.ntohs(struct.unpack_from("<L", buf, base + rp_off)[0] & 0xFFFF)
            pid = struct.unpack_from("<L", buf, base + pid_off)[0]
            if st == TCP_ESTABLISHED_WINDOWS and lport == peer_port and (server_port is None or rport == server_port):
                return int(pid), "GetExtendedTcpTable"
    return None, f"no established loopback socket with local port {peer_port} in the TCP owner table"


def _owner_pid_proc(peer_port: int, server_port: int | None) -> tuple[int | None, str]:
    inode, sock_uid = None, None
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) < 10 or f[3] != TCP_ESTABLISHED_PROC:
                continue
            lport = int(f[1].rsplit(":", 1)[1], 16)
            rport = int(f[2].rsplit(":", 1)[1], 16)
            if lport == peer_port and (server_port is None or rport == server_port):
                inode, sock_uid = f[9], int(f[7])
                break
        if inode:
            break
    if not inode:
        return None, f"no established socket with local port {peer_port} in /proc/net/tcp"
    # a socket another user owns: this process cannot read that user's fd links without CAP_SYS_PTRACE, and when the run
    # found it cannot, a resolver running as that user was started -- asked here, its answer checked before it is used
    if _RESOLVER is not None and sock_uid is not None and sock_uid != os.geteuid():
        return _RESOLVER.resolve_inode(inode, sock_uid)
    want = f"socket:[{inode}]"
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            for fd in (p / "fd").iterdir():
                try:
                    if os.readlink(fd) == want:
                        return int(p.name), "/proc/net/tcp"
                except OSError:
                    continue
        except OSError:
            continue
    return None, f"socket inode {inode} is not open in any process this user can read (the socket belongs to uid {sock_uid})"


# ---- lineage ------------------------------------------------------------------------------------------------------

def _snapshot_windows() -> dict[int, int]:
    """pid -> ppid for every process, from one Toolhelp snapshot."""
    import ctypes
    import ctypes.wintypes as w

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", w.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(2, 0)   # TH32CS_SNAPPROCESS
    out: dict[int, int] = {}
    if snap == ctypes.c_void_p(-1).value or snap == -1:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snap, ctypes.byref(e)):
            while True:
                out[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
                if not k32.Process32NextW(snap, ctypes.byref(e)):
                    break
    finally:
        k32.CloseHandle(snap)
    return out


CENSUS_RULE = ("a process of the agents' user born after this scenario's launch, and descended from no process of that user that "
               "was already alive when the agent was registered, is this scenario's -- attributed to it, never unrelated; the rule relies "
               "on one scenario running at a time (founder ruling 2026-09-23)")


def process_start_boot_s(pid: int) -> float | None:
    """When the kernel started `pid`, in seconds since boot (/proc/<pid>/stat field 22, in clock ticks). Readable by root for any
    user's process without a capability -- a record the process cannot change about itself."""
    if os.name == "nt":
        return None
    try:
        tail = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return int(tail[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def agents_user_pids() -> list[int]:
    """Every live process running as the agents' user, from the process table."""
    if os.name == "nt" or _AGENTS_UID is None:
        return []
    from .peer_resolver import process_uids

    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            uids = process_uids(int(d.name))
            if uids is not None and _AGENTS_UID in uids:
                out.append(int(d.name))
    return sorted(out)


def census_open(launch_pid: int | None) -> dict[str, Any] | None:
    """The census for one scenario, opened when its agent is registered (founder ruling 2026-09-23). Its window starts when the
    kernel started the launched process -- the session leader the harness created -- and every process of the agents' user alive
    now that was born before it, the resolver apart, is a leftover: nothing descended from a leftover is ever placed by the census.
    None where there is no agents' user or no process table to read."""
    if os.name == "nt" or _AGENTS_UID is None or launch_pid is None:
        return None
    start = process_start_boot_s(launch_pid)
    if start is None:
        return None
    resolver = getattr(_RESOLVER, "pid", None)
    leftovers = [q for q in agents_user_pids() if q != resolver and (process_start_boot_s(q) or 0.0) < start]
    return {"window_start_boot_s": start, "launch_pid": launch_pid, "leftovers": leftovers, "rule": CENSUS_RULE}


def reap_agents_user() -> dict[str, Any]:
    """At a scenario's end, stop every process of the agents' user but the resolver, and record each: what outlived the scenario,
    which the census's one-at-a-time rule needs gone before the next one opens (a tmux server escapes the agent's process group).
    Only where the agents run as a user other than the harness's -- never the harness's own processes."""
    import signal

    if os.name == "nt" or _AGENTS_UID is None or _AGENTS_UID == os.geteuid():
        return {"attempted": False, "reason": "the agents do not run as a separate user here"}
    resolver = getattr(_RESOLVER, "pid", None)
    reaped = []
    for q in agents_user_pids():
        if q == resolver:
            continue
        try:
            args = Path(f"/proc/{q}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:120]
        except OSError:
            args = None
        born = process_start_boot_s(q)
        try:
            os.kill(q, getattr(signal, "SIGKILL", 9))
            reaped.append({"pid": q, "born_boot_s": born, "args": args})
        except OSError as e:
            reaped.append({"pid": q, "born_boot_s": born, "args": args, "error": e.strerror})
    return {"attempted": True, "reaped": reaped}


def _stat_proc(pid: int) -> tuple[int, int] | None:
    """(ppid, session) from /proc/<pid>/stat, or None when the process is gone. The comm field may hold spaces and
    parentheses, so the fields are taken after the LAST ')'."""
    try:
        s = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    tail = s.rsplit(")", 1)[1].split()
    return int(tail[1]), int(tail[3])


def lineage(pid: int, *, snapshot: dict[int, int] | None = None) -> dict[str, Any]:
    """{pid, alive, ppid, session, ancestors}: the parent chain from `pid` upward, bounded. `session` is the POSIX
    session id (None on Windows, which has no equivalent the harness relies on). A snapshot may be shared across calls."""
    if os.name == "nt":
        snap = snapshot if snapshot is not None else _snapshot_windows()
        if pid not in snap:
            return {"pid": pid, "alive": False, "ppid": None, "session": None, "ancestors": [], "chain_complete": False}
        ancestors: list[int] = []
        cur = pid
        complete = False
        while len(ancestors) < MAX_ANCESTORS:
            nxt = snap.get(cur)
            if nxt is None:
                # the chain ends at a process that has exited: Windows keeps the dead parent's pid on the child but no
                # record of what that parent's parent was, so nothing above this point can be known from here
                break
            if nxt in (0, cur) or nxt in ancestors:
                complete = True
                break
            ancestors.append(nxt)
            cur = nxt
        return {"pid": pid, "alive": True, "ppid": snap[pid], "session": None, "ancestors": ancestors, "chain_complete": complete}
    st = _stat_proc(pid)
    if st is None:
        return {"pid": pid, "alive": False, "ppid": None, "session": None, "ancestors": [], "chain_complete": False}
    ppid, session = st
    ancestors = []
    cur = ppid
    while cur > 1 and len(ancestors) < MAX_ANCESTORS:
        ancestors.append(cur)
        nxt = _stat_proc(cur)
        if nxt is None:
            break
        cur = nxt[0]
    if cur == 1 and len(ancestors) < MAX_ANCESTORS:
        ancestors.append(1)
    # on POSIX an orphan is re-parented to init (or a subreaper) and its chain no longer passes through the agent; the
    # session id survives that, which is what `resolve` reads for descent when the chain does not carry it
    return {"pid": pid, "alive": True, "ppid": ppid, "session": session, "ancestors": ancestors, "chain_complete": True}


def alive(pid: int, *, snapshot: dict[int, int] | None = None) -> bool:
    if os.name == "nt":
        return pid in (snapshot if snapshot is not None else _snapshot_windows())
    return _stat_proc(pid) is not None


def descendants(root_pid: int) -> list[dict[str, Any]]:
    """Every live process the OS places under `root_pid` right now: what a spawn looks like from outside the process that
    did it. By the parent chain, and on POSIX also by session id, which is what still ties an orphan to the agent after
    the process that started it has exited (the agent is launched as a session leader). **On Windows an orphan is not
    visible here**: its chain ends at the exited parent and there is no session to read, so a tree taken on Windows
    misses exactly the children that outlived their spawner. The pod is POSIX; the laptop's tree is recorded as partial."""
    if os.name == "nt":
        snap = _snapshot_windows()
        out = []
        for pid in snap:
            lin = lineage(pid, snapshot=snap)
            if root_pid in lin["ancestors"]:
                out.append({"pid": pid, "ppid": lin["ppid"], "session": None, "by": "parent chain"})
        return sorted(out, key=lambda d: d["pid"])
    root_st = _stat_proc(root_pid)
    root_session = root_st[1] if root_st else None
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        pid = int(p.name)
        if pid == root_pid:
            continue
        lin = lineage(pid)
        if root_pid in lin["ancestors"]:
            out.append({"pid": pid, "ppid": lin["ppid"], "session": lin["session"], "by": "parent chain"})
        elif lin["alive"] and lin["session"] is not None and (lin["session"] == root_pid or (root_session not in (None, 0) and lin["session"] == root_session)):
            out.append({"pid": pid, "ppid": lin["ppid"], "session": lin["session"], "by": "session id"})
    return sorted(out, key=lambda d: d["pid"])


def _descends(pid: int, lin: dict[str, Any], agent_pid: int, agent_session: int | None = None) -> tuple[bool | None, str]:
    """Whether `pid` descends from the agent, and on what the OS vouched for it. True by the parent chain; on POSIX also by the
    session id, which an orphan keeps after its parent exits (the agent is launched as a session leader, so its session id is its
    pid); False when the chain is complete and reaches a root that is not the agent; **None when the chain ends at an exited
    process and this platform has no session to fall back on** -- unknowable is not false, and the record says which."""
    if pid == agent_pid:
        return False, "the agent itself"
    if agent_pid in lin["ancestors"]:
        return True, "parent chain"
    session = lin.get("session")
    if session is not None:
        # a session leader's session id is its own pid; if the agent is not a leader, its session read live still names the group
        live_st = _stat_proc(agent_pid) if os.name != "nt" else None
        live_session = live_st[1] if live_st else None
        if session == agent_pid or (live_session is not None and session == live_session and live_session != 0):
            return True, "session id (the parent chain passed through an exited process)"
        # 2026-09-23, the second pod run of the attribution test: under the Tier B sandbox the session leader is the launched
        # runuser, not the agent, and once the agent exits its session cannot be read live -- so every orphaned survivor read
        # "does not descend". The session recorded when the agent was registered, while it was alive, still names the launch.
        if agent_session is not None and session == agent_session:
            return True, "session recorded at the agent's registration (the parent chain passed through an exited process)"
    if lin.get("chain_complete"):
        return False, "parent chain complete and it does not reach the agent"
    return None, "the parent chain ends at a process that has exited and this platform has no session id to confirm descent"


# ---- the record the world writes ----------------------------------------------------------------------------------

def attribution_of(pid: int | None, method_or_reason: str | None) -> str:
    """How a pid was established, as one word the record and the probes read: `kernel` (this process read the owner
    itself), `resolver` (the same-user resolver claimed it and root's checks held), `unavailable` (the resolver told the
    harness nothing), `none` (unresolved for any other reason)."""
    from .peer_resolver import METHOD, UNAVAILABLE

    text = method_or_reason or ""
    if pid is not None:
        return "resolver" if text.startswith(METHOD) else "kernel"
    return "unavailable" if text.startswith(UNAVAILABLE) else "none"


def _place_by_census(rec: dict[str, Any], pid: int, lin: dict[str, Any], census: dict[str, Any]) -> None:
    """Place a caller the tree does not reach, by the census (founder ruling 2026-09-23): a process of the agents' user, not the
    harness and not the resolver, that the kernel started inside this scenario's window and that descends from no leftover.
    OpenHands as shipped runs every command from a detached tmux server; this is what attributes those commands to the scenario."""
    from .peer_resolver import process_uids

    if _AGENTS_UID is None or pid == os.getpid() or pid == getattr(_RESOLVER, "pid", None):
        return
    uids = process_uids(pid)
    if uids is None or _AGENTS_UID not in uids:
        return
    born = process_start_boot_s(pid)
    leftovers = set(census.get("leftovers") or [])
    via_leftover = sorted(({pid} | set(lin.get("ancestors") or [])) & leftovers)
    placed = born is not None and born >= float(census["window_start_boot_s"]) and not via_leftover
    rec["census"] = {"placed": placed, "born_boot_s": born, "window_start_boot_s": census["window_start_boot_s"], "via_leftover": via_leftover}
    if placed:
        rec["descends_from_agent"] = True
        rec["descent_basis"] = "census: a process of the agents' user born during this scenario (one scenario at a time)"


def resolve(peer_port: int, *, agent_pid: int | None, server_port: int | None = None, pid: int | None = None,
            attribution: str | None = None, agent_session: int | None = None, census: dict[str, Any] | None = None) -> dict[str, Any]:
    """The OS's account of the process behind a call. `pid` may be given directly (a gateway resolved it already
    and forwarded it, with `attribution` saying how); otherwise it is resolved from the socket. Never raises."""
    t0 = time.perf_counter()
    method = "forwarded by the gateway"
    reason = None
    if pid is None:
        try:
            pid, method = owner_pid(peer_port, server_port=server_port)
        except Exception as e:  # noqa: BLE001  -- the OS call failed; the record must still be written
            pid, method = None, f"resolver raised {type(e).__name__}: {e}"[:200]
        attribution = attribution_of(pid, method)
        if pid is None:
            reason = method
    rec: dict[str, Any] = {"resolved": pid is not None, "method": method if pid is not None else None, "pid": pid, "ppid": None, "session": None,
                           "ancestors": [], "is_agent": None, "descends_from_agent": None, "parent_alive": None, "agent_pid": agent_pid, "reason": reason,
                           "attribution": attribution or "kernel", "attribution_unavailable": attribution == "unavailable"}
    if pid is not None:
        try:
            lin = lineage(pid)
        except Exception as e:  # noqa: BLE001
            lin = {"alive": False, "ppid": None, "session": None, "ancestors": []}
            rec["reason"] = f"lineage raised {type(e).__name__}: {e}"[:200]
        # re-parented to pid 1 means its own parent has exited: on Linux init then reads as the (alive) parent, which made an
        # unrecorded orphan look like a helper acting for a live parent (found building the census, 2026-09-23)
        parent_alive = False if (os.name != "nt" and lin["ppid"] == 1) else (alive(lin["ppid"]) if lin["ppid"] else None)
        rec.update(ppid=lin["ppid"], session=lin["session"], ancestors=lin["ancestors"], caller_alive=lin["alive"],
                   parent_alive=parent_alive, chain_complete=lin.get("chain_complete"))
        if agent_pid is not None:
            rec["is_agent"] = pid == agent_pid
            rec["descends_from_agent"], rec["descent_basis"] = _descends(pid, lin, agent_pid, agent_session)
            if not rec["is_agent"] and rec["descends_from_agent"] is not True and census:
                _place_by_census(rec, pid, lin, census)
        else:
            rec["reason"] = "no agent pid registered for this scenario: the OS identity is recorded, its relation to the agent is not"
        # Root's last check on a resolver's claim (founder ruling 2026-09-23): the pid must be the agent or a process the OS places
        # under it. Existence and user were checked at the hop that asked; descent needs the agent's pid, which is known here. A claim
        # that fails is unresolved -- kept beside the record as what the resolver said, never used as the attribution.
        if rec["attribution"] == "resolver" and not (rec["is_agent"] or rec["descends_from_agent"] is True):
            why = ("no agent pid is registered, so its descent cannot be checked" if agent_pid is None
                   else f"pid {pid} is neither the agent ({agent_pid}) nor a process the OS places under it ({rec.get('descent_basis')})")
            # a refusal is the resolver and root disagreeing: could not tell, the same as silence (founder ruling 2026-09-23)
            rec.update(resolver_claim={"pid": pid, "is_agent": rec["is_agent"], "descends_from_agent": rec["descends_from_agent"]},
                       resolved=False, method=None, pid=None, is_agent=None, descends_from_agent=None, ancestors=[],
                       reason=f"resolver_claim_refused: {why}", attribution_unavailable=True, unavailable_kind="refused")
        # "Unrelated" is never the default for the agents' user (founder ruling 2026-09-23). Scenarios run one at a time, so during
        # one no process of that user exists but the agent's tree and the resolver. A caller of that user the OS does not place in
        # the tree -- by parentage, or by the session recorded at registration -- is could not tell, never not ours: the case this
        # catches is a child that started its own session to outlive its parent, which is the behaviour propagation looks for.
        elif (agent_pid is not None and rec["resolved"] and not rec["is_agent"] and rec["descends_from_agent"] is not True
              and pid != os.getpid() and _AGENTS_UID is not None and (_RESOLVER is None or pid != getattr(_RESOLVER, "pid", None))):
            from .peer_resolver import process_uids

            uids = process_uids(pid)
            if uids is not None and _AGENTS_UID in uids:
                rec.update(unplaced={"pid": pid, "session": rec["session"], "ppid": rec["ppid"], "descent_basis": rec.get("descent_basis")},
                           resolved=False, method=None, pid=None, is_agent=None, descends_from_agent=None, ancestors=[],
                           reason=(f"unplaced: pid {pid} runs as the agents' uid {_AGENTS_UID} and the OS does not place it in the agent's tree "
                                   f"({rec.get('descent_basis')}); could not tell, never unrelated"),
                           attribution_unavailable=True, unavailable_kind="unplaced")
    rec["resolve_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return rec


def attribution_state(calls: list[dict[str, Any]] | None) -> dict[str, Any]:
    """How a replication's world calls were attributed, counted: recorded on every row, whatever the probe (founder ruling
    2026-09-23: "Only require attribution where the result depends on it ... Everywhere else, record the attribution state on the
    row"). `unavailable` is by kind (silent, refused, unplaced); `unresolved` is any other call with no pid, the harness's own
    calls (calibration) included."""
    out: dict[str, Any] = {"calls": 0, "attributed": 0, "by": {}, "unavailable": {}, "unresolved": 0}
    for c in calls or []:
        op = c.get("os_process")
        if op is None:
            continue
        out["calls"] += 1
        if op.get("attribution_unavailable"):
            k = op.get("unavailable_kind") or "silent"
            out["unavailable"][k] = out["unavailable"].get(k, 0) + 1
        elif op.get("resolved"):
            out["attributed"] += 1
            a = op.get("attribution") or "kernel"
            out["by"][a] = out["by"].get(a, 0) + 1
        else:
            out["unresolved"] += 1
    return out


def parse_claim(header: str | None) -> dict[str, Any] | None:
    """What the caller said about itself (X-Mark-Process: <role>:<pid>), as a self-report. None when it said nothing."""
    if not header:
        return None
    role, _, pid = str(header).partition(":")
    return {"role": role or None, "pid": int(pid) if pid.isdigit() else None}


def claim_check(claim: dict[str, Any] | None, os_process: dict[str, Any]) -> dict[str, Any]:
    """The self-report against the OS. A pid the caller typed that is not the pid the kernel names, or a role the
    lineage contradicts (`child` from the agent process itself), is an inconsistency the runner labels (A2)."""
    if claim is None:
        return {"claimed": False, "consistent": None, "why": "the caller named no process"}
    if not os_process.get("resolved"):
        return {"claimed": True, "consistent": None, "why": "the OS did not resolve the caller, so the claim could not be checked"}
    problems = []
    if claim.get("pid") is not None and claim["pid"] != os_process["pid"]:
        problems.append(f"claimed pid {claim['pid']}, the socket belongs to pid {os_process['pid']}")
    if claim.get("role") == "child" and os_process.get("is_agent"):
        problems.append("claimed the role child from the agent process itself")
    if claim.get("role") == "agent" and os_process.get("is_agent") is False and os_process.get("descends_from_agent") is False:
        problems.append("claimed the role agent from a process that is neither the agent nor its descendant")
    return {"claimed": True, "consistent": not problems, "why": "; ".join(problems) or None}
