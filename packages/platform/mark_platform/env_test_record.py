"""Every env-test attempt inside the run record (fix A7, attempt 3 fixes v1.1, 2026-09-14).

`pod/env-test.sh` applies the calibration rule (founder ruling 2026-09-13): when calibration is the only failure, the
calibration test runs alone three times, and 3/3 clean earns one more full env-test; any repeat replaces the pod. On
attempt 2b the attempts were logs under $RUNS/env-test-*, outside the run directory, and none reached the exported bundle.
The script now lists each attempt in `<prefix>.attempts` (kind, exit code, log file, tab-separated) and names the latest
prefix in $RUNS/env-test-latest. `bench run --env-test <prefix>` refuses a failed env-test before the run opens, copies the
listing and every log into the run directory, and records every attempt in the ledger, results and manifest.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

ATTEMPTS_SUFFIX = ".attempts"
COPIED_TO = "env-test"
_KIND = re.compile(r"full|rerun|calibration-alone-[123]")
_SUMMARY = re.compile(r"\b\d+ (passed|failed|errors?)\b")


def _listing(prefix: str | Path) -> Path:
    p = Path(prefix)
    return p.with_name(p.name + ATTEMPTS_SUFFIX)


def _summary(text: str) -> str:
    lines = [t.strip() for t in text.splitlines() if t.strip()]
    return next((t for t in reversed(lines) if _SUMMARY.search(t)), lines[-1] if lines else "")


def collect(prefix: str | Path) -> dict[str, Any]:
    """Read the attempts env-test.sh listed; refuse a listing that cannot be read as the calibration rule's attempts."""
    listing = _listing(prefix)
    if not listing.exists():
        raise ValueError(f"no env-test attempts listing at {listing}")
    attempts: list[dict[str, Any]] = []
    for n, line in enumerate(listing.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"{listing} line {n}: not kind<TAB>exit<TAB>log: {line!r}")
        kind, code, log = parts
        if not _KIND.fullmatch(kind):
            raise ValueError(f"{listing} line {n}: unknown attempt kind {kind!r}")
        if not code.isdigit():
            raise ValueError(f"{listing} line {n}: exit code {code!r} is not a number")
        if Path(log).name != log:
            raise ValueError(f"{listing} line {n}: the log must be a file beside the listing, got {log!r}")
        logp = listing.parent / log
        if not logp.exists():
            raise ValueError(f"{listing} line {n}: log {log} is missing")
        data = logp.read_bytes()
        attempts.append({"kind": kind, "exit_code": int(code), "log": log, "log_sha256": hashlib.sha256(data).hexdigest(), "summary": _summary(data.decode("utf-8", "replace"))})
    if not attempts or attempts[0]["kind"] != "full":
        raise ValueError(f"{listing}: the first attempt must be the full env-test")
    final = attempts[-1]
    return {"recorded": True, "prefix": Path(prefix).name, "attempts": attempts,
            "calibration_rule_applied": any(a["kind"].startswith("calibration-alone") for a in attempts), "final_attempt": final["kind"],
            "outcome": "passed" if final["exit_code"] == 0 and final["kind"] in ("full", "rerun") else "failed"}


def record_env_test(ctx: Any, prefix: str | Path | None) -> dict[str, Any]:
    """Copy the attempts into the run directory and record them. With no prefix the absence is recorded, never left silent."""
    from .runner import ENGINE_VERSION, Provenance, object_hash

    if not prefix:
        rec: dict[str, Any] = {"recorded": False, "reason": "no env-test record was passed to this run (bench run --env-test)"}
    else:
        rec = collect(prefix)
        dest = ctx.run_dir / COPIED_TO
        dest.mkdir(parents=True, exist_ok=True)
        listing = _listing(prefix)
        shutil.copy2(listing, dest / listing.name)
        for a in rec["attempts"]:
            shutil.copy2(listing.parent / a["log"], dest / a["log"])
        rec["copied_to"] = COPIED_TO
    ctx.env_test = rec
    ctx.ledger.append(ctx.chain_id, "env_test", rec, Provenance(ENGINE_VERSION, object_hash(ctx.env), "platform-runner"))
    return rec
