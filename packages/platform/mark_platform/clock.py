"""Timestamps for the platform: a thin re-export of the shared timing shim (packages/timing, Task 4.1).
Every span, mock call and halt command carries BOTH a monotonic reading (CLOCK_MONOTONIC_RAW on the pod) and a
wall-clock reading (for humans). Differences are only ever taken between monotonic readings."""
from __future__ import annotations

from mark_timing import clock_source, is_raw, mono_ns, resolution_ns, stamp, wall_ns  # noqa: F401

__all__ = ["clock_source", "is_raw", "mono_ns", "resolution_ns", "stamp", "wall_ns"]
