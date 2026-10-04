"""mark_timing: the single timing shim (Task 4.1 clock rule).

`mono_ns()` reads CLOCK_MONOTONIC_RAW (Linux: `time.clock_gettime_ns(CLOCK_MONOTONIC_RAW)`, unaffected by NTP
slewing, host-wide, sub-microsecond). Every participant on the pod (harness, agents, mock services, the tool
server) takes its timestamps here, so any two readings on one host are directly comparable. On platforms
without CLOCK_MONOTONIC_RAW (the Windows authoring laptop) it falls back to `perf_counter_ns` and SAYS so:
`clock_source()` is recorded in every run manifest, and a pod run whose source is not `CLOCK_MONOTONIC_RAW`
is refused by the runner unless MARK_ALLOW_FALLBACK_CLOCK=1 (laptop CI only).

Wall-clock readings (`wall_ns`) exist for humans and never enter a difference.
The Node shim (`shim.mjs`) is the same contract for TypeScript participants: `process.hrtime.bigint()` is
CLOCK_MONOTONIC (not RAW) on Linux, which is what Node exposes; the difference (NTP slew, < 500 ppm) is
recorded as `clock_source: "node:hrtime(CLOCK_MONOTONIC)"` so a mixed trace says which clocks it holds.
"""
from __future__ import annotations

import time

_RAW = getattr(time, "CLOCK_MONOTONIC_RAW", None)
_SOURCE = "CLOCK_MONOTONIC_RAW" if _RAW is not None else "perf_counter_ns (fallback; not CLOCK_MONOTONIC_RAW)"


def mono_ns() -> int:
    if _RAW is not None:
        return time.clock_gettime_ns(_RAW)
    return time.perf_counter_ns()


def wall_ns() -> int:
    return time.time_ns()


def clock_source() -> str:
    return _SOURCE


def is_raw() -> bool:
    return _RAW is not None


def stamp() -> dict[str, int | str]:
    return {"mono_ns": mono_ns(), "wall_ns": wall_ns(), "clock": _SOURCE}


def resolution_ns(samples: int = 2000) -> int:
    """Smallest positive difference between consecutive readings."""
    best = None
    last = mono_ns()
    for _ in range(samples):
        now = mono_ns()
        d = now - last
        if d > 0 and (best is None or d < best):
            best = d
        last = now
    return best or 0
