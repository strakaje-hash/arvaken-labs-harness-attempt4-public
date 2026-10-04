"""Calibration taken DURING a run, under the run's own load (founder ruling 2026-09-22).

**Why the gate before a run is not enough.** Calibration ran before each smoke and passed; it failed twice, both
times while a smoke was working the GPU. A calibration taken while the machine is quiet certifies a quieter
machine than the one the run happens on, and `ks.latency` judges some controls on a 250 ms threshold -- a
scheduler that can delay the harness by 30 ms under load can move a reading across that line. So the clock being
trustworthy *during* the run is what the latency verdicts rest on.

**What this does.** A sample is a few known sleeps measured on the raw monotonic clock, judged by
`integrity.calibration_verdict` -- the same function the pre-run gate uses, so an in-run sample can never be held
to a softer rule than the gate that opened the run. Samples bracket every replication: one before the first, one
after each. A replication both of whose bracketing samples pass is vouched for; one where either fails is not
counted, with the reason, the same way a stalled replication is.

**And throttling beside it.** The container's CPU quota is shared no matter which cores each process is pinned to,
so the model server exhausting it can still pause the harness on its own cores. That is rare today (47 throttled
periods in 461,843 on the first pod) but it is a different cause with the same symptom, so every sample carries
the cgroup's throttle counters across its own window. If throttling ever becomes the cause, the record shows it
rather than hiding it inside a failed calibration (founder ruling 2026-09-22).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from mark_timing import mono_ns

from .integrity import calibration_verdict

CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")
DEFAULT_SAMPLES = 3
EXPECTED_MS = 250.0
TOLERANCE_MS = 5.0


def cpu_throttle_counters(path: Path = CPU_STAT) -> dict[str, int] | None:
    """`nr_throttled` and `throttled_usec` from the cgroup, or None where there is no cgroup v2 (a laptop)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("nr_periods", "nr_throttled", "throttled_usec"):
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                return None
    return out or None


def sample(*, samples: int = DEFAULT_SAMPLES, expected_ms: float = EXPECTED_MS, tolerance_ms: float = TOLERANCE_MS,
           stat_path: Path = CPU_STAT) -> dict[str, Any]:
    """One clock sample under whatever load is present now, with the throttle counters across its own window."""
    before = cpu_throttle_counters(stat_path)
    started = mono_ns()
    durs: list[float] = []
    for _ in range(max(1, samples)):
        t0 = mono_ns()
        time.sleep(expected_ms / 1000.0)
        durs.append((mono_ns() - t0) / 1e6)
    ended = mono_ns()
    after = cpu_throttle_counters(stat_path)
    rec = calibration_verdict(durs, expected_ms, tolerance_ms, min_samples=max(1, samples))
    rec["started_mono_ns"] = started
    rec["ended_mono_ns"] = ended
    if before is not None and after is not None:
        rec["throttling"] = {k: after.get(k, 0) - before.get(k, 0) for k in ("nr_periods", "nr_throttled", "throttled_usec")}
        # stated separately from `ok`: throttling is a DIFFERENT cause with the same symptom, and a sample that
        # passed while the cgroup throttled is a fact a reader needs, not one to fold into a boolean
        rec["throttled_during_sample"] = rec["throttling"]["nr_throttled"] > 0
    else:
        rec["throttling"] = None
        rec["throttled_during_sample"] = None
        rec["throttling_reason"] = f"no cgroup v2 cpu.stat at {stat_path}"
    return rec


UNVERIFIED = "clock_unverified"
NO_GATE = ("no calibration gate ran for this run, so no tolerance was agreed; the samples are recorded and no "
           "replication is excluded on a threshold the run never agreed to")


def unverified_reason(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str | None:
    """The reason a replication bracketed by these two samples is not counted, or None if the clock is vouched for.

    Both sides must vouch. A replication is excluded when EITHER bracketing sample failed, because the harness
    cannot say which side of the sample the delay fell on -- and an exclusion that guesses is worse than one that
    is honest about the bracket."""
    bad = [(name, s) for name, s in (("before", before), ("after", after)) if s is not None and not s.get("ok")]
    if not bad:
        return None
    parts = []
    for name, s in bad:
        thr = s.get("throttling") or {}
        extra = f", cgroup throttled {thr.get('nr_throttled')}x for {thr.get('throttled_usec')} us during the sample" if s.get("throttled_during_sample") else ""
        parts.append(f"the {name} sample read max {s.get('max_ms')} ms against {s.get('expected_ms')} +{s.get('tolerance_ms')}{extra}")
    return f"{UNVERIFIED}: the clock was not vouched for across this replication -- " + "; ".join(parts)
