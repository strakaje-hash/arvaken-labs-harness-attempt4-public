"""Anchoring glue: a run on the pod is offline, so the chain root is recorded local-only at close; `platform
run export` re-anchors with Rekor/TSA when the network allows (or the laptop does it after the bundle lands)."""
from __future__ import annotations

from mark_ledger.anchor import LocalOnlyAnchor
from mark_ledger.store import Ledger


def local_anchor(ledger: Ledger, chain_id: str) -> None:
    root = ledger.chain_root(chain_id)
    if root:
        ledger.record_anchor(LocalOnlyAnchor().anchor(chain_id, root).to_json())
