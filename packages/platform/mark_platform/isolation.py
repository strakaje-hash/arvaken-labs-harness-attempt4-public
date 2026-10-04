"""Task 1.4: what the kernel under this container allows. Measured, never assumed. The record goes into
docs/POD.md, every run manifest (`isolation_capabilities`) and the sandbox tier decision (Task 3.2):

  Tier A  user namespaces (CLONE_NEWUSER) + CAP_NET_ADMIN: bubblewrap/firejail per agent + iptables egress allowlist
  Tier B  neither: `runuser` as the runner uid, prlimit, ephemeral working directory, no secrets in the environment,
          every service on localhost, HTTP(S)_PROXY to a local allowlist proxy. egress_control = best_effort.

Measured on pod t4x9i3ysc580ak (Runpod, EU-RO-1) 2026-09-11: Seccomp mode 2 with one filter, CapEff a80425fb
(no cap_sys_admin, no cap_net_admin), unshare -U / -n / -p all EPERM, bwrap fails to create any namespace,
iptables "Permission denied", runuser works. Tier B.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any


def _run(cmd: list[str], timeout: int = 20) -> tuple[int | None, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return None, "not installed"
    except subprocess.TimeoutExpired:
        return None, "timeout"


def _proc_status() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in open("/proc/self/status", encoding="utf-8"):
            k, _, v = line.partition(":")
            if k in ("Seccomp", "Seccomp_filters", "CapEff", "CapBnd", "NoNewPrivs"):
                out[k] = v.strip()
    except OSError:
        pass
    return out


def probe() -> dict[str, Any]:
    """Run every check and return the record. Cheap (< 2 s); safe to run at every manifest write."""
    if os.name != "posix":
        return {"platform": os.name, "tier": "none", "reason": "not a Linux host", "egress_control": "none"}
    st = _proc_status()
    rc_capsh, capsh = _run(["capsh", "--print"])
    caps_line = next((l for l in capsh.splitlines() if l.startswith("Current:")), "") if rc_capsh == 0 else ""
    rec: dict[str, Any] = {
        "platform": "linux",
        "uid": os.getuid(),
        "capsh_current": caps_line,
        "cap_sys_admin": "cap_sys_admin" in caps_line,
        "cap_net_admin": "cap_net_admin" in caps_line,
        "seccomp_mode": st.get("Seccomp"),
        "seccomp_filters": st.get("Seccomp_filters"),
        "cap_eff": st.get("CapEff"),
        "no_new_privs": st.get("NoNewPrivs"),
    }
    for name, cmd in (("unshare_user", ["unshare", "-U", "true"]), ("unshare_net", ["unshare", "-n", "true"]), ("unshare_pid", ["unshare", "-p", "-f", "true"])):
        rc, out = _run(cmd)
        rec[name] = {"ok": rc == 0, "detail": out[:120]}
    rc, out = _run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-user", "--unshare-pid", "true"])
    rec["bwrap"] = {"installed": shutil.which("bwrap") is not None, "ok": rc == 0, "detail": out[:160]}
    rc, out = _run(["iptables", "-L", "OUTPUT", "-n"])
    rec["iptables"] = {"installed": shutil.which("iptables") is not None, "ok": rc == 0, "detail": out[:160]}
    rc, out = _run(["runuser", "-u", "runner", "--", "id", "-u"])
    rec["runuser_runner"] = {"ok": rc == 0 and out.strip().isdigit() and out.strip() != "0", "detail": out[:80]}
    rec["firejail_installed"] = shutil.which("firejail") is not None
    # Measured, not assumed: can the runner uid write into the model cache? On Runpod's network volume (a FUSE
    # mount without POSIX permission enforcement) the answer was YES on 2026-09-11, so the cache is protected by
    # post-run checksum verification (manifest.model_cache_integrity), not by file modes.
    hf = os.environ.get("HF_HOME", "/workspace/hf")
    probe_path = os.path.join(hf, ".runner-write-probe")
    rc, out = _run(["runuser", "-u", "runner", "--", "sh", "-c", f"echo x > {probe_path} && echo WROTE"])
    writable = rc == 0 and "WROTE" in out
    try:
        os.remove(probe_path)
    except OSError:
        pass
    rec["hf_cache_writable_by_runner"] = {"writable": writable, "path": hf, "detail": out[:120]}
    rc, out = _run(["runuser", "-u", "runner", "--", "cat", "/etc/platform/secrets/canary.txt"])
    rec["secrets_readable_by_runner"] = rc == 0 and "canary-" in out
    tier_a = rec["unshare_user"]["ok"] and rec["bwrap"]["ok"] and rec["cap_net_admin"] and rec["iptables"]["ok"]
    if tier_a:
        rec.update({"tier": "A", "egress_control": "enforced", "reason": "user namespaces + CAP_NET_ADMIN available"})
    elif rec["runuser_runner"]["ok"]:
        rec.update({"tier": "B", "egress_control": "best_effort", "reason": "no CLONE_NEWUSER / CAP_NET_ADMIN: runuser + prlimit + allowlist proxy; an agent with shell access can unset the proxy environment"})
    else:
        rec.update({"tier": "none", "egress_control": "none", "reason": "no runner user available"})
    return rec


def summary_line(rec: dict[str, Any]) -> str:
    return (f"tier {rec.get('tier')} (egress_control={rec.get('egress_control')}); seccomp mode {rec.get('seccomp_mode')}; cap_sys_admin={rec.get('cap_sys_admin')}, "
            f"cap_net_admin={rec.get('cap_net_admin')}; unshare -U {'ok' if rec.get('unshare_user', {}).get('ok') else 'EPERM'}; bwrap {'ok' if rec.get('bwrap', {}).get('ok') else 'fails'}; "
            f"iptables {'ok' if rec.get('iptables', {}).get('ok') else 'denied'}; runuser {'ok' if rec.get('runuser_runner', {}).get('ok') else 'fails'}")


if __name__ == "__main__":
    import json

    r = probe()
    print(json.dumps(r, indent=1))
    print(summary_line(r))
