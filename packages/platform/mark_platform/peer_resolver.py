"""Who owns a socket, asked of a process running as the agents' own user (attempt 4, founder ruling 2026-09-23).

**Why this exists.** The first harness hop attributes a call by the process that owns the client end of its socket:
the socket's inode from `/proc/net/tcp`, then the process whose `/proc/<pid>/fd` holds that inode. Reading another
user's fd links needs `CAP_SYS_PTRACE`. On the measured pod the agents run as `runner`, the harness runs as root, and
the container does not grant root that capability, so every `readlink` fails with EACCES and every call went
unattributed -- 64 of 64 `ks.propagation` replications on attempt 4's first matrix run. A process running as the
agents' user needs no capability to read their fd links. This module is that process, and the harness's side of it.

**What root still checks** (the founder's first protection). The resolver only *claims* "this socket belongs to this
pid". Root reads everything it can without the capability -- which processes exist, who owns them, who their parent
is, and the socket's own uid in `/proc/net/tcp` -- and accepts a claim only when the pid exists, is not the resolver,
and runs as the user that owns the socket, the resolver's user. Descent from the agent is checked where the agent's pid
is known, by the world (`process_identity.resolve`). A claim that fails any check is unresolved, never accepted.

**What silence means** (the second). A resolver that is gone, or does not answer within its timeout, or says a socket
that is still established has no owner, has not told the harness anything: the call is marked `attribution_unavailable`,
and the runner samples the resolver's health around every replication. `ks.propagation` reads either as
`not_run: attribution_unavailable`. "Could not tell" is never "no survivors".

**The limit, stated where it is used.** The resolver shares a user with the agents it attributes, so an agent could stop,
pause or deceive it. Sound only while the agents are not hostile to the harness -- the standing limit of the paper. The
way to remove it is a host that grants the harness `CAP_SYS_PTRACE` (docs/attempt5-list.md); on such a host this
process is not started and the harness reads the owners itself.

The resolver runs through the same sandbox command as the agents, so it is their user by construction, not by
configuration. Root learns its pid and uid from the kernel (`SO_PEERCRED` on the Unix socket), never from what it says.
It holds one TCP connection to root open for its whole life: root knows that socket's owner (the resolver itself), so
every health sample asks the question whose answer root already has -- a known-answer check through the same code path.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

UNAVAILABLE = "attribution_unavailable"
METHOD = "same-user resolver, verified by root"
LIMIT = ("on this host, attribution relies on a resolver running as the same user as the agents, which is sound only while "
         "the agents are not hostile to the harness")
TIMEOUT_S = 2.0
START_TIMEOUT_S = 60.0
CAP_SYS_PTRACE = 19


# ---- the resolver process (runs as the agents' user) --------------------------------------------------------------

def owner_of_inode(inode: str) -> tuple[int | None, int]:
    """The pid whose fd table holds `socket:[inode]`, scanning every process this user can read; and how many fd
    tables could not be read (a count, so a resolver that can read nothing says so rather than answering 'no owner')."""
    want = f"socket:[{inode}]"
    unreadable = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            fds = list((p / "fd").iterdir())
        except OSError:
            unreadable += 1
            continue
        for fd in fds:
            try:
                if os.readlink(fd) == want:
                    return int(p.name), unreadable
            except OSError:
                continue
    return None, unreadable


def _peer_uid(conn: socket.socket) -> int | None:
    try:
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return uid
    except OSError:
        return None


def _answer(req: dict[str, Any]) -> dict[str, Any]:
    op = req.get("op")
    if op == "ping":
        return {"ok": True, "pid": os.getpid(), "uid": os.getuid()}
    if op == "owner":
        inode = str(req.get("inode") or "")
        if not inode.isdigit():
            return {"ok": False, "why": f"not an inode: {inode!r}"}
        pid, unreadable = owner_of_inode(inode)
        return {"ok": True, "pid": pid, "unreadable_fd_tables": unreadable}
    return {"ok": False, "why": f"unknown op {op!r}"}


def _serve_conn(conn: socket.socket, harness_uid: int) -> None:
    with conn:
        # only the harness may ask: the kernel names the caller's uid, and anyone else is closed on without a word
        if _peer_uid(conn) != harness_uid:
            return
        f = conn.makefile("rwb")
        for line in f:
            try:
                out = _answer(json.loads(line))
            except (ValueError, TypeError) as e:
                out = {"ok": False, "why": f"bad request: {type(e).__name__}"}
            f.write((json.dumps(out) + "\n").encode())
            f.flush()


_HELD: list[socket.socket] = []   # the sentinel connection, held for the process's life


def serve(name: str, harness_uid: int, sentinel_port: int) -> int:
    # the known answer: this process holds one connection to root for as long as it lives
    _HELD.append(socket.create_connection(("127.0.0.1", sentinel_port), timeout=10))
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind("\0" + name)
    srv.listen(64)
    # the harness holds our stdin open; when it goes, so do we -- a resolver never outlives the run that started it
    threading.Thread(target=lambda: (sys.stdin.buffer.read(), os._exit(0)), daemon=True).start()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_serve_conn, args=(conn, harness_uid), daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mark_platform.peer_resolver")
    p.add_argument("--name", required=True)
    p.add_argument("--harness-uid", type=int, required=True)
    p.add_argument("--sentinel-port", type=int, required=True)
    a = p.parse_args(argv)
    return serve(a.name, a.harness_uid, a.sentinel_port)


# ---- the harness's side (runs as root) ----------------------------------------------------------------------------

def has_cap_sys_ptrace() -> bool:
    """Whether this process may read other users' fd links itself. Read from the kernel's own record of it."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("CapEff:"):
                return bool(int(line.split()[1], 16) >> CAP_SYS_PTRACE & 1)
    except (OSError, ValueError, IndexError):
        pass
    return False


def process_uids(pid: int) -> tuple[int, int] | None:
    """(real, effective) uid of `pid` from /proc/<pid>/status, which root can read without any capability."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Uid:"):
                f = line.split()
                return int(f[1]), int(f[2])
    except (OSError, ValueError, IndexError):
        return None
    return None


def socket_row(inode: str) -> tuple[str, int] | None:
    """(state, uid) of the TCP socket with this inode, from /proc/net/tcp{,6}; None when it is no longer listed."""
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) >= 10 and f[9] == inode:
                return f[3], int(f[7])
    return None


class SameUserResolver:
    """The harness's handle on the resolver process: start it through the agents' sandbox, ask it, check what it says,
    and sample its health. Every method returns a record and never raises past the caller -- an unanswered question is a
    fact the record carries."""

    def __init__(self, launch_prefix: list[str], *, python: str | None = None, timeout_s: float = TIMEOUT_S, log_path: Path | None = None) -> None:
        self.launch_prefix = list(launch_prefix)
        self.python = python or sys.executable
        self.timeout_s = timeout_s
        self.log_path = log_path
        self.name = f"mark-peer-resolver-{os.getpid()}-{secrets.token_hex(6)}"
        self.proc: subprocess.Popen | None = None
        self.pid: int | None = None
        self.uid: int | None = None
        self.sentinel_inode: str | None = None
        self._sentinel_conn: socket.socket | None = None
        self._log: Any = None

    # -- lifecycle

    def start(self) -> "SameUserResolver":
        lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lst.bind(("127.0.0.1", 0))
        lst.listen(1)
        lst.settimeout(START_TIMEOUT_S)
        env = {k: v for k, v in os.environ.items() if k != "MARK_SANDBOX_REPORT"}
        self._log = open(self.log_path, "ab") if self.log_path else subprocess.DEVNULL
        cmd = [*self.launch_prefix, self.python, "-m", "mark_platform.peer_resolver", "--name", self.name,
               "--harness-uid", str(os.geteuid()), "--sentinel-port", str(lst.getsockname()[1])]
        # it starts where agents work ($MARK_RUNS, which run.sh makes traversable), never in a folder of its own choosing
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=self._log, stderr=self._log, env=env, cwd=os.environ.get("MARK_RUNS") or "/tmp")
        try:
            conn, (_, client_port) = lst.accept()
        except OSError as e:
            self.stop()
            raise RuntimeError(f"the resolver never connected back ({type(e).__name__}); see {self.log_path}") from e
        finally:
            lst.close()
        self._sentinel_conn = conn
        # the resolver's end of the sentinel connection: root reads its inode from /proc/net/tcp (no capability needed)
        self.sentinel_inode = _inode_for_local_port(client_port)
        deadline = time.monotonic() + START_TIMEOUT_S
        while True:
            try:
                s = self._connect()
                break
            except OSError:
                if time.monotonic() > deadline:
                    self.stop()
                    raise RuntimeError("the resolver's socket never came up")
                time.sleep(0.05)
        # who the resolver IS, from the kernel -- never from its own reply
        pid, uid, _gid = struct.unpack("3i", s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        s.close()
        self.pid, self.uid = pid, uid
        # The pids it names must be pids in root's numbering. A sandbox that gives the resolver its own pid namespace (Tier A's
        # bwrap --unshare-pid) would have it name processes by numbers that mean other processes out here -- a wrong answer
        # root's checks might not catch. Its own pid, as it sees it, must be the one the kernel gave root.
        seen = self._ask({"op": "ping"}).get("pid")
        if seen != pid:
            self.stop()
            raise RuntimeError(f"the resolver sees itself as pid {seen} and the kernel names it {pid}: it runs in another pid namespace, "
                               "so every pid it named would be in the wrong numbering")
        return self

    def stop(self) -> None:
        if self._sentinel_conn is not None:
            self._sentinel_conn.close()
            self._sentinel_conn = None
        if self.proc is not None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
            self.proc = None
        if self._log not in (None, subprocess.DEVNULL):
            self._log.close()
            self._log = None

    # -- asking

    def _connect(self) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout_s)
        s.connect("\0" + self.name)
        return s

    def _ask(self, req: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as s:
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    raise ConnectionError("the resolver closed the connection without answering")
                buf += chunk
        return json.loads(buf)

    def resolve_inode(self, inode: str, socket_uid: int) -> tuple[int | None, str]:
        """(pid, method) on a claim root has verified; (None, reason) otherwise. A reason starting with UNAVAILABLE means
        the resolver told the harness nothing -- gone, silent, or 'no owner' for a socket that is still established."""
        t0 = time.perf_counter()
        try:
            out = self._ask({"op": "owner", "inode": inode})
        except (OSError, ValueError, ConnectionError) as e:
            return None, f"{UNAVAILABLE}: the resolver did not answer ({type(e).__name__}: {e})"[:300]
        ms = round((time.perf_counter() - t0) * 1000, 3)
        if not out.get("ok"):
            return None, f"{UNAVAILABLE}: the resolver refused the question ({out.get('why')})"
        pid = out.get("pid")
        if pid is None:
            row = socket_row(inode)
            if row is not None and row[0] == "01":
                # an established socket is held by some process; a working resolver running as its owner's user finds it
                return None, (f"{UNAVAILABLE}: the resolver found no owner for socket inode {inode}, which is still established "
                              f"(it could not read {out.get('unreadable_fd_tables')} fd table(s))")
            return None, f"the caller's socket (inode {inode}) closed before its owner could be read"
        why = verify_claim(int(pid), socket_uid=socket_uid, resolver_pid=self.pid, resolver_uid=self.uid)
        if why:
            return None, f"resolver_claim_refused: {why}"
        return int(pid), f"{METHOD} ({ms} ms)"

    def health(self) -> dict[str, Any]:
        """One sample: is the process there, under the uid the kernel gave at start, and does it answer the question whose
        answer root already knows (the owner of its own sentinel socket is itself)?"""
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"pid": self.pid, "alive": False, "answering": False, "known_answer": False, "available": False, "why": None}
        uids = process_uids(self.pid) if self.pid else None
        rec["alive"] = uids is not None and self.uid in uids
        if not rec["alive"]:
            rec["why"] = f"{UNAVAILABLE}: the resolver process (pid {self.pid}) is gone or no longer runs as uid {self.uid}"
        else:
            try:
                out = self._ask({"op": "owner", "inode": self.sentinel_inode})
                rec["answering"] = bool(out.get("ok"))
                rec["known_answer"] = out.get("pid") == self.pid
                if not rec["known_answer"]:
                    rec["why"] = f"{UNAVAILABLE}: the resolver named pid {out.get('pid')} as the owner of its own socket (it is {self.pid})"
            except (OSError, ValueError, ConnectionError) as e:
                rec["why"] = f"{UNAVAILABLE}: the resolver did not answer within {self.timeout_s} s ({type(e).__name__})"
        rec["available"] = rec["alive"] and rec["answering"] and rec["known_answer"]
        rec["ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return rec


def verify_claim(pid: int, *, socket_uid: int, resolver_pid: int | None, resolver_uid: int | None) -> str | None:
    """Root's check of a resolver's claim, from what root can read without any capability. None when it holds; else why."""
    if resolver_pid is not None and pid == resolver_pid:
        return f"it named itself (pid {pid}) as the owner of an agent's socket"
    uids = process_uids(pid)
    if uids is None:
        return f"pid {pid} does not exist"
    if socket_uid != resolver_uid:
        return f"the socket belongs to uid {socket_uid}, not the agents' uid {resolver_uid}"
    if resolver_uid not in uids:
        return f"pid {pid} runs as uid {uids[1]}, not the agents' uid {resolver_uid}"
    return None


def _inode_for_local_port(port: int) -> str | None:
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) >= 10 and int(f[1].rsplit(":", 1)[1], 16) == port and f[3] == "01":
                return f[9]
    return None


def start_if_needed(launch_prefix: list[str], *, log_path: Path | None = None) -> tuple["SameUserResolver | None", dict[str, Any]]:
    """Decide how this run attributes calls, and start the resolver only where it is the only way. Returns (resolver or
    None, the record the manifest carries). The decision is the kernel's: the resolver's uid from SO_PEERCRED, the
    harness's capability from CapEff."""
    # `agents_uid`: the user the agents run as, where the run can establish it -- the resolver's uid from SO_PEERCRED, or the
    # harness's own when there is no sandbox. A caller of that user the OS cannot place in the agent's tree is could not tell.
    if os.name != "posix" or not Path("/proc/net/tcp").exists():
        return None, {"mode": "kernel", "basis": "not Linux: the platform's own owner table (GetExtendedTcpTable on Windows)", "limit": None, "agents_uid": None}
    if not launch_prefix:
        return None, {"mode": "kernel", "basis": "no sandbox: the agents run as the harness's own user, whose fd links it reads", "limit": None,
                      "agents_uid": os.geteuid()}
    r = SameUserResolver(launch_prefix, log_path=log_path).start()
    if r.uid == os.geteuid():
        r.stop()
        return None, {"mode": "kernel", "basis": f"the sandbox runs agents as the harness's own uid {os.geteuid()}", "limit": None, "agents_uid": r.uid}
    if has_cap_sys_ptrace():
        r.stop()
        return None, {"mode": "kernel", "basis": "the harness holds CAP_SYS_PTRACE and reads the agents' fd links itself", "limit": None, "agents_uid": r.uid}
    first = r.health()
    if not first["available"]:
        r.stop()
        raise RuntimeError(f"the same-user resolver started but failed its first known-answer check: {first}")
    return r, {"mode": "same-user resolver", "basis": (f"agents run as uid {r.uid}; the harness runs as uid {os.geteuid()} without CAP_SYS_PTRACE, "
                                                      "so it cannot read their fd links"),
               "resolver_pid": r.pid, "resolver_uid": r.uid, "agents_uid": r.uid, "first_health": first,
               "limit": LIMIT + "; a child that changes its own session is caught by the rule that a process of the agents' user the OS cannot "
                                "place in the agent's tree is could not tell, not by the session check"}


if __name__ == "__main__":
    sys.exit(main())
