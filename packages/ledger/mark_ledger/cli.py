"""mark-ledger: the third-party verification path.

  mark-ledger verify <ledger-dir> [chain_id ...]          re-hash chains, check payloads and anchor receipts
  mark-ledger verify-manifest <manifest.json> --root <root.pub> [--revocations keys/revocations.json]
  mark-ledger anchor <ledger-dir> <chain_id> --authority local-only|rekor|rfc3161 [--tsa-url ...] [--key ... --cert ...]
Exit status 0 only when everything verified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .anchor import LocalOnlyAnchor, RekorAnchor, Rfc3161Anchor
from .keys import KeyCertError
from .manifest import read_signed, verify_manifest
from .store import Ledger


def cmd_verify(a: argparse.Namespace) -> int:
    led = Ledger(a.ledger)
    chains = a.chain or led.list_chains()
    ok = True
    for c in chains:
        v = led.verify(c, check_receipts=not a.no_receipts)
        ok = ok and v.ok
        print(json.dumps(v.to_json(), indent=2 if a.pretty else None, sort_keys=True))
    if not chains:
        print("no chains", file=sys.stderr)
        return 2
    return 0 if ok else 1


def cmd_verify_manifest(a: argparse.Namespace) -> int:
    root_pub = Path(a.root).read_text(encoding="utf-8").strip()
    rev = json.loads(Path(a.revocations).read_text(encoding="utf-8")) if a.revocations else None
    try:
        m = verify_manifest(read_signed(a.manifest), root_pub, revocations=rev)
    except (KeyCertError, ValueError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    out = {"ok": True, "run_id": m["run_id"], "chain_root": m["evidence"]["chain_root"], "pins": m["pins"]}
    if a.ledger:
        v = Ledger(a.ledger).verify(m["evidence"]["chain_id"], check_receipts=True)
        out["ledger"] = v.to_json()
        out["chain_root_matches"] = v.chain_root == m["evidence"]["chain_root"]
        out["ok"] = v.ok and out["chain_root_matches"]
    print(json.dumps(out, indent=2 if a.pretty else None, sort_keys=True))
    return 0 if out["ok"] else 1


def cmd_anchor(a: argparse.Namespace) -> int:
    led = Ledger(a.ledger)
    root = led.chain_root(a.chain)
    if not root:
        print(f"chain {a.chain} is empty", file=sys.stderr)
        return 2
    if a.authority == "local-only":
        client = LocalOnlyAnchor()
    elif a.authority == "rekor":
        if not a.key:
            print("rekor needs --key <private hex file> (the PEM public half is derived; --pem overrides)", file=sys.stderr)
            return 2
        from .anchor import ed25519_public_pem

        priv = Path(a.key).read_text().strip()
        client = RekorAnchor(priv, Path(a.pem).read_text() if a.pem else ed25519_public_pem(priv), base_url=a.rekor_url)
    elif a.authority == "rfc3161":
        if not a.tsa_url:
            print("rfc3161 needs --tsa-url", file=sys.stderr)
            return 2
        client = Rfc3161Anchor(a.tsa_url)
    else:
        return 2
    anc = client.anchor(a.chain, root)
    led.record_anchor(anc.to_json())
    print(json.dumps(anc.to_json(), sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mark-ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("ledger")
    v.add_argument("chain", nargs="*")
    v.add_argument("--no-receipts", action="store_true")
    v.add_argument("--pretty", action="store_true")
    v.set_defaults(fn=cmd_verify)
    vm = sub.add_parser("verify-manifest")
    vm.add_argument("manifest")
    vm.add_argument("--root", required=True)
    vm.add_argument("--revocations")
    vm.add_argument("--ledger")
    vm.add_argument("--pretty", action="store_true")
    vm.set_defaults(fn=cmd_verify_manifest)
    an = sub.add_parser("anchor")
    an.add_argument("ledger")
    an.add_argument("chain")
    an.add_argument("--authority", choices=["local-only", "rekor", "rfc3161"], default="local-only")
    an.add_argument("--rekor-url", default="https://rekor.sigstore.dev")
    an.add_argument("--tsa-url")
    an.add_argument("--key")
    an.add_argument("--pem")
    an.set_defaults(fn=cmd_anchor)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
