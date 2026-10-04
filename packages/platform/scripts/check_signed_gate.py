"""After the founder signs a gate: check the signed file before it is committed or anchored.

    python packages/platform/scripts/check_signed_gate.py ks.completeness

Four things, each printed with its value so the check is readable, not just green:
  1. the signed file verifies under the repository root for purpose `gate`, and names the key that signed it;
  2. the signed body is the committed draft plus `issued_at` and nothing else -- byte-for-byte on the canonical form, so a
     draft edited between review and signing would show here;
  3. the gate hash the rows will cite (over the signed body, `issued_at` included), which differs from the draft's hash
     by exactly that stamp;
  4. `load_gate` -- the runtime's own reader -- returns this version, signed, with the thresholds the probe needs.
Exit 1 on any failure. Never writes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "packages" / "ledger"), str(REPO / "packages" / "probes")]

from mark_ledger.canonical import canonical_json, object_hash  # noqa: E402
from mark_ledger.keys import KeyCertError, SignedObject, verify_signed_object  # noqa: E402
from mark_probes.gate import GATE_KEY_PURPOSE, load_gate  # noqa: E402


def main(gate_id: str) -> int:
    gates = REPO / "gates"
    keys = REPO / "packages" / "bundles" / "keys"
    draft_p, signed_p = gates / f"{gate_id}.draft.json", gates / f"{gate_id}.signed.json"
    root_pub = (keys / "root.pub").read_text(encoding="utf-8").strip()
    rev_p = keys / "revocations.json"
    revocations = json.loads(rev_p.read_text(encoding="utf-8")) if rev_p.exists() else None
    draft = json.loads(draft_p.read_text(encoding="utf-8"))
    raw = json.loads(signed_p.read_text(encoding="utf-8"))
    ok = True

    # 1. verifies under the root, purpose gate
    try:
        so = SignedObject.from_json(raw)
        body = verify_signed_object(so, root_pub, GATE_KEY_PURPOSE, revocations=revocations, issued_at_field="issued_at")
        print(f"1. signature      : verifies under the root for purpose '{GATE_KEY_PURPOSE}'; key {so.key_id}; issued_at {body.get('issued_at')}")
    except (KeyCertError, KeyError, ValueError) as e:
        print(f"1. signature      : DOES NOT VERIFY: {e}")
        return 1

    # 2. the body is the draft plus issued_at and nothing else
    stripped = {k: v for k, v in body.items() if k != "issued_at"}
    same = canonical_json(stripped) == canonical_json(draft)
    print(f"2. body vs draft  : {'identical apart from issued_at' if same else 'DIFFERS FROM THE DRAFT'}  (draft file sha256 {hashlib.sha256(draft_p.read_bytes()).hexdigest()})")
    ok &= same

    # 3. the hash the rows will cite
    print(f"3. gate_hash      : {object_hash(body)}  (signed body; draft body was {object_hash(draft)})")
    print(f"   signed file    : sha256 {hashlib.sha256(signed_p.read_bytes()).hexdigest()}  ({signed_p.stat().st_size} bytes)")

    # 4. the runtime's own reader
    g = load_gate(gates, gate_id, root_pub, revocations)
    print(f"4. load_gate      : v{g.version} signed={g.signed} by {g.signed_by} source={Path(g.source).name} thresholds={g.thresholds}")
    ok &= g.signed and g.version == int(draft["version"]) and g.gate_hash == object_hash(body)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "ks.completeness"))
