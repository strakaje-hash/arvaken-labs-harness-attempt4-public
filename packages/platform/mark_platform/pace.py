"""The measured pace of a target on a workload (founder rulings 2026-09-12 and 2026-09-13, ks.resume v3).

A pace is the interval at which the uninterrupted stream lands effects. It differs by target (about 200 ms scripted,
255 ms LangGraph and 386 ms OpenHands, against a declared 150 ms sleep) because it is a property of this model on
this host on this day, so it is measured, never declared.

The pace for a target and workload is the median inter-effect interval of the `none` baseline, taken from the first
`none` cell on that target and workload in the run. `none` never halts, so its stream is the uninterrupted one. The
source is recorded as the cell id, the replications used and the median. On the single-call arm the interval includes
the harness continuation's latency, recorded beside the continuation count (founder ruling 2026-09-13): the hold is
derived from the interval the agent actually exhibits.

**Floors, pre-registered in the ks.resume gate beside min_replications** (founder ruling 2026-09-13). A pace from one
interval in one replication is a number with no distribution under it (the v3 smoke read `ok` at 14,936 ms that way):
- a replication contributes only with at least `pace_min_intervals_per_replication` intervals (5);
- at least `pace_min_contributing_fraction` of the measured replications must contribute (0.5: ten of twenty);
- the pace is the median over the contributing replications' intervals.
A gate that does not carry the floors pre-registers no pace, and the pace is unavailable.

Outcomes that are not a pace:
- `unavailable`: no floors pre-registered, a single effect in every stream, or too few contributing replications;
- `invalid`: a measured pace below the declared sleep, which means the mock or the timing shim is wrong.
A pace-dependent cell never runs on a guess: it is not_run `pace_unavailable` or `pace_invalid`."""
from __future__ import annotations

import statistics
from typing import Any

HOLD_PACES_RULE = "the hold is a whole number of measured paces, the same for none and every control on the cell"
FLOOR_KEYS = ("pace_min_intervals_per_replication", "pace_min_contributing_fraction")


def declared_sleep_ms(workload: dict[str, Any]) -> float | None:
    prm = workload.get("params") or {}
    v = prm.get("pace_ms") if prm.get("pace_ms") is not None else prm.get("spacing_ms")
    return float(v) if v is not None else None


def effect_stream(mock_calls: list[dict[str, Any]] | None, service: str = "payment") -> list[int]:
    """Receipt-of-record stamps of the executed effects (never a refused attempt), in order. The pace sets ks.resume's
    hold, so it is a result-deciding quantity, and until attempt 4 (A2) it was read from the agent's dispatch stamps."""
    from mark_probes.killswitch import receipt_stamp

    return sorted(receipt_stamp(c) for c in (mock_calls or [])
                  if c.get("service") == service and not c.get("refused") and (c.get("received_mono_ns") is not None or c.get("hop_arrived_mono_ns") is not None)
                  and not str(c.get("path", "")).startswith("/calibration"))


def floors_from_gate(gate: Any) -> dict[str, Any]:
    """The pre-registered floors from a loaded gate, with where they came from; `missing` when the gate carries none."""
    source = f"{getattr(gate, 'gate_id', '?')} v{getattr(gate, 'version', '?')} ({'signed' if getattr(gate, 'signed', False) else 'unsigned'})"
    pre = getattr(gate, "preconditions", None) or {}
    if any(pre.get(k) is None for k in FLOOR_KEYS):
        return {"source": source, "missing": True}
    return {"source": source, "missing": False, "min_intervals_per_replication": int(pre["pace_min_intervals_per_replication"]),
            "min_contributing_fraction": float(pre["pace_min_contributing_fraction"])}


def pace_from_streams(streams: list[list[int]], *, declared_ms: float | None, source_cell: str, floors: dict[str, Any] | None) -> dict[str, Any]:
    per_rep = [max(0, len(s) - 1) for s in streams]
    base: dict[str, Any] = {"source_cell": source_cell, "replications": len(streams), "intervals_per_replication": per_rep, "declared_sleep_ms": declared_ms, "floors": floors}
    if not floors or floors.get("missing"):
        src = (floors or {}).get("source", "no ks.resume gate")
        return {**base, "intervals": sum(per_rep), "status": "unavailable", "pace_ms": None, "contributing_replications": None,
                "reason": f"pace floors not pre-registered in the ks.resume gate this run loaded ({src})"}
    k, f = floors["min_intervals_per_replication"], floors["min_contributing_fraction"]
    contributing = [s for s in streams if len(s) - 1 >= k]
    base["contributing_replications"] = len(contributing)
    if not contributing or len(contributing) < f * len(streams):
        reason = ("single effect in the uninterrupted stream" if sum(per_rep) == 0
                  else f"{len(contributing)} of {len(streams)} measured replications have at least {k} intervals; at least {f:.0%} must")
        return {**base, "intervals": sum(per_rep), "status": "unavailable", "pace_ms": None, "reason": reason}
    intervals = [(b - a) / 1e6 for s in contributing for a, b in zip(s, s[1:])]
    med = statistics.median(intervals)
    base["intervals"] = len(intervals)
    if declared_ms is not None and med < declared_ms:
        return {**base, "status": "invalid", "pace_ms": med,
                "reason": f"measured pace {med:.1f} ms is below the declared sleep {declared_ms:g} ms: the mock or the timing shim is wrong"}
    return {**base, "status": "ok", "pace_ms": med, "reason": None}
