"""The stop rule, applied to a FULL run cell by cell as its ledger records each cell (founder ruling 2026-09-23: five machines;
"a machine stops on the stop rule -- do the others carry on? Yes").

    python full_run_stop_watch.py <run dir> [--poll-s 20] [--once]

A full run is one bench run, and it writes results.json only when it closes, so the practice checker -- which reads a closed
smoke -- cannot see a full run's cells in time. The ledger records every cell as it closes (`probe_result`, whose object is
the row exactly as results.json will carry it), and the run's calibration before the first cell. This follows the chain and
hands each row, with the run's calibration and the world's call record, to the SAME checker the practice runs used
(`practice_stop_check.check` and `review`), so the full run is judged by the table written before it, not by a copy.

Prints one line per cell (`GO ON`, `GO ON | REVIEW: ...`, or `STOP: ...`) and exits:
  0 -- results.json appeared and the whole run passed the checker (the run-level checks -- dropped spans, a second
       instrument, the model cache -- are read there, where the run records them);
  1 -- a stop: the first cell (or the calibration, or the closed run) the checker sends back; the caller ends the run;
  2 -- the run dir or its ledger cannot be read.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _checker():
    spec = importlib.util.spec_from_file_location("practice_stop_check", HERE / "practice_stop_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _object(run: Path, content_hash: str) -> dict[str, Any]:
    from mark_ledger.store import Ledger

    o = Ledger(run / "ledger").get_object(content_hash)
    return json.loads(o) if isinstance(o, (str, bytes)) else o


def _calls(run: Path) -> list[dict[str, Any]] | None:
    try:
        return [json.loads(line) for line in (run / "mock-calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return None


def cell_verdict(psc: Any, row: dict[str, Any], calibration_ok: bool | None, calls: list[dict[str, Any]] | None) -> tuple[list[str], list[str]]:
    """(stops, review flags) for one closed cell, by the committed checker. The run-level fields a closed run carries are not
    known yet; they are read from results.json at close, so here they are the values that stop nothing."""
    results = {"run_failed": False, "calibration": {"ok": calibration_ok}, "dropped_spans": 0, "baseline_invariants": {"violations": []},
               "single_instrument": {"foreign_spans": 0}, "model_cache_integrity": {"status": "verified"}, "results": [row]}
    sids = {r.get("scenario_id") for r in row.get("per_replication") or []}
    own = None if calls is None else [c for c in calls if c.get("scenario_id") in sids]
    stops = psc.check(results, own)
    if calibration_ok is not True:
        stops = [f"the run's calibration did not pass (ok={calibration_ok})"] + [s for s in stops if s != "calibration failed"]
    return stops, psc.review(results)


def watch(run: Path, poll_s: float = 20.0, once: bool = False) -> int:
    psc = _checker()
    seen = 0
    calibration_ok: bool | None = None
    while True:
        chains = sorted((run / "ledger" / "chains").glob("*.jsonl")) if (run / "ledger" / "chains").is_dir() else []
        if len(chains) > 1:
            print(f"STOP: {len(chains)} chains in one run's ledger; which one is the run's cannot be told")
            return 1
        recs = [json.loads(line) for line in chains[0].read_text(encoding="utf-8").splitlines() if line.strip()] if chains else []
        for r in recs[seen:]:
            if r["kind"] == "calibration":
                calibration_ok = bool(_object(run, r["content_hash"]).get("ok"))
                if not calibration_ok:
                    print("STOP: the run's calibration did not pass on this machine")
                    return 1
            elif r["kind"] == "probe_result":
                row = _object(run, r["content_hash"])
                where = f"{(row.get('probe') or {}).get('id')}/{(row.get('workload') or {}).get('id')}/{(row.get('target') or {}).get('id')}/{(row.get('control') or {}).get('id')}"
                stops, flags = cell_verdict(psc, row, calibration_ok, _calls(run))
                if stops:
                    print(f"STOP: {where}: " + " || ".join(stops))
                    return 1
                print(f"{where}: " + ("GO ON | REVIEW: " + " || ".join(flags) if flags else "GO ON"), flush=True)
        seen = len(recs)
        if (run / "results.json").exists():
            # the closed run, every run-level check included, by the same checker's own entry point
            return 0 if psc.main([str(run)]) == 0 else 1
        if once:
            return 0
        time.sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--poll-s", type=float, default=20.0)
    ap.add_argument("--once", action="store_true", help="read what is there now and exit (0 unless a stop was found)")
    a = ap.parse_args(argv)
    run = Path(a.run_dir)
    if not run.is_dir():
        print(f"STOP: no run dir {run}")
        return 2
    return watch(run, a.poll_s, a.once)


if __name__ == "__main__":
    raise SystemExit(main())
