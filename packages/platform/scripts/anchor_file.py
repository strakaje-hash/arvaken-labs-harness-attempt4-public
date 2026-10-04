"""Anchor one repository file two ways, as the attempt 3 read-under files were anchored (docs/ANCHORS.md, 2026-09-16):

  * RFC 3161: a DigiCert time-stamp token over the FILE's bytes (`<name>.tsq` / `<name>.tsr`, made and verified with openssl);
  * Rekor:    an entry over the file's SHA-256 LINE (`<sha256>\\n`), signed with the run-manifest key -- the ledger's own
              `RekorAnchor` artifact form -- recorded as `<name>.rekor.json` and checked offline with `check_receipt`.

    python packages/platform/scripts/anchor_file.py <repo-relative file> <receipts dir> --what "<one-line description>"

Refuses to anchor a file whose working copy differs from HEAD, so what is anchored is what is committed. Appends to
`<receipts dir>/anchored.json` ({mark_commit, anchors: [...]}) and prints the docs/ANCHORS.md row. Never prints a key.
Anchoring publishes to a public log: run it only on the founder's word.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "packages" / "ledger")]

from mark_ledger.anchor import RekorAnchor, check_receipt, ed25519_public_pem  # noqa: E402

TSA_URL = "http://timestamp.digicert.com"
RUN_MANIFEST_KEY = REPO / "packages" / "bundles" / "keys" / "bundle-a151ce6f95f5793b.key"


def sh(*cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("receipts_dir")
    ap.add_argument("--what", required=True)
    ap.add_argument("--label", default=None, help="chain_id prefix (default: the receipts dir name)")
    a = ap.parse_args()
    rel = a.file.replace("\\", "/")
    path = REPO / rel
    out = REPO / a.receipts_dir
    out.mkdir(parents=True, exist_ok=True)
    label = a.label or out.name

    # 0. what is anchored is what is committed
    head = sh("git", "rev-parse", "HEAD", cwd=REPO).stdout.strip()
    committed = sh("git", "show", f"HEAD:{rel}", cwd=REPO)
    if committed.returncode != 0:
        print(f"refused: {rel} is not in HEAD ({committed.stderr.strip()})")
        return 1
    on_disk = path.read_bytes()
    if hashlib.sha256(committed.stdout.encode("utf-8") if isinstance(committed.stdout, str) else committed.stdout).hexdigest() != hashlib.sha256(on_disk).hexdigest():
        # git show returns text with the platform's newline translation off (we compare bytes through a second, binary read)
        raw = subprocess.run(["git", "show", f"HEAD:{rel}"], capture_output=True, check=False, cwd=REPO).stdout
        if hashlib.sha256(raw).hexdigest() != hashlib.sha256(on_disk).hexdigest():
            print(f"refused: the working copy of {rel} differs from HEAD {head[:12]}; commit first")
            return 1
    sha = hashlib.sha256(on_disk).hexdigest()
    line = RekorAnchor.artifact(sha)
    line_sha = hashlib.sha256(line).hexdigest()
    base = out / path.name
    print(f"file       : {rel}\nsha256     : {sha}\nline sha256: {line_sha}\ncommit     : {head}")

    # 1. RFC 3161 over the file bytes, via openssl (the token includes the TSA certificate chain: -cert)
    tsq, tsr = base.with_name(base.name + ".tsq"), base.with_name(base.name + ".tsr")
    q = sh("openssl", "ts", "-query", "-data", str(path), "-sha256", "-cert", "-out", str(tsq))
    if q.returncode != 0:
        print("openssl ts -query failed:", q.stderr.strip())
        return 1
    r = httpx.post(TSA_URL, content=tsq.read_bytes(), headers={"Content-Type": "application/timestamp-query"}, timeout=60)
    if r.status_code != 200:
        print(f"TSA returned {r.status_code}")
        return 1
    tsr.write_bytes(r.content)
    text = sh("openssl", "ts", "-reply", "-in", str(tsr), "-text").stdout
    serial = re.search(r"Serial number:\s*(0x[0-9A-Fa-f]+)", text)
    gen = re.search(r"Time stamp:\s*(.+)", text)
    import certifi

    v = sh("openssl", "ts", "-verify", "-data", str(path), "-in", str(tsr), "-CAfile", certifi.where())
    if "Verification: OK" not in v.stdout + v.stderr:
        print("openssl ts -verify did not say OK:", (v.stdout + v.stderr).strip()[:300])
        return 1
    print(f"rfc3161    : serial {serial.group(1) if serial else '?'}  time {gen.group(1).strip() if gen else '?'}  (openssl ts -verify: OK)")

    # 2. Rekor over the SHA-256 line, signed with the run-manifest key (read here, never printed)
    priv = RUN_MANIFEST_KEY.read_text(encoding="utf-8").strip()
    anchor = RekorAnchor(priv, ed25519_public_pem(priv)).anchor(f"{label}:{rel}", sha)
    rec = anchor.to_json()
    ok, what = check_receipt(rec)
    if not ok:
        print("rekor receipt did not check:", what)
        return 1
    rekor_p = base.with_name(base.name + ".rekor.json")
    rekor_p.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    rc = rec["receipt"]
    print(f"rekor      : logIndex {rc['logIndex']}  uuid {rc['uuid']}  integrated {rc['integratedTime']}  pre_existing {rc['pre_existing_entry']}  ({what})")

    # 3. the record beside the receipts
    rec_p = out / "anchored.json"
    book = json.loads(rec_p.read_text(encoding="utf-8")) if rec_p.exists() else {"mark_commit": head, "anchors": []}
    book["anchors"].append({"file": rel, "what": a.what, "sha256": sha, "line_sha256": line_sha, "tsa_serial": serial.group(1) if serial else None,
                            "tsa_time": gen.group(1).strip() if gen else None, "rekor_logIndex": rc["logIndex"], "rekor_uuid": rc["uuid"],
                            "rekor_integrated": rc["integratedTime"], "mark_commit": head})
    rec_p.write_text(json.dumps(book, indent=1) + "\n", encoding="utf-8")
    print("\ndocs/ANCHORS.md row:")
    print(f"| `{rel}` | {a.what} | `{sha}` | [{rc['logIndex']}]({rc['url']}) | `{serial.group(1) if serial else '?'}` {gen.group(1).strip() if gen else '?'} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
