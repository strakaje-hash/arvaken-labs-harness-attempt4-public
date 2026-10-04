"""Sign a pre-registration with the gate key, reproducibly (founder ruling 2026-09-22).

    python packages/platform/scripts/sign_preregistration.py <record> <spec> --tag <tag> \\
        --key packages/bundles/keys/bundle-<gate key id>.key [--signed-at <iso>] [--out <path>]

**Why this is in the tree.** Attempt 3's `benchmarks/attempt3-preregistration.signed.json` exists and the tool that
made it does not. A signed object nobody can reproduce is the opposite of what a pre-registration signature is for:
a reader should be able to take the record, the spec and the key's public half and confirm the signature covers
exactly what it claims to. So the signer lives here, with the schema, and **produces the same bytes from the same
inputs every time** -- `signed_at` is an input rather than a clock reading, and Ed25519 is deterministic.

**It reads the TAG's committed content, never the working copy.** The first version compared the working copy's
bytes with HEAD's and refused on a difference. On a Windows checkout with `core.autocrlf` those bytes always differ
-- the spec carried 93 carriage returns on disk and none in the commit -- while git itself reported no change, so
it refused a correct tree (2026-09-22). Comparing more cleverly would have been a second rule about line endings.
Reading `git show <tag>:<path>` removes the comparison: what is signed cannot differ from what is committed,
because it IS what is committed. The commit is resolved from the tag, so the object's `tag` and `commit` agree by
construction, and signing needs no detached checkout.

**The circularity it resolves.** A document cannot contain its own hash, so the hash lives in the spec and the
document cites the tag. This object names both and asserts they agree: `spec_cites_record` is true only when the
spec text actually contains the record's path *and* its sha256, checked here rather than asserted by the author.

Reads the key, never prints it. Signing is the founder's act: this script does not decide when to run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "packages" / "ledger")]

from mark_ledger.keys import iso, now_utc, sign_object  # noqa: E402

SCHEMA = "mark.preregistration-signature/1"
POLICY = "Arvaken Labs Publication and Independence Policy 2.0, §24"
PURPOSE = "gate"


class SignRefused(ValueError):
    """A precondition failed; the message says which."""


def commit_of(tag: str) -> str:
    """The commit a tag names, or a refusal naming the tag. Never HEAD: HEAD is wherever the shell happens to be."""
    proc = subprocess.run(["git", "rev-parse", "--verify", f"{tag}^{{commit}}"], capture_output=True, text=True,
                          check=False, cwd=REPO)
    if proc.returncode != 0:
        raise SignRefused(f"no such tag {tag!r} in this repository: sign against a tag that exists")
    return proc.stdout.strip()


def committed(rev: str, rel: str) -> bytes:
    """The bytes a revision stores for a path -- the same on every machine, whatever its line-ending settings."""
    proc = subprocess.run(["git", "show", f"{rev}:{rel}"], capture_output=True, check=False, cwd=REPO)
    if proc.returncode != 0:
        raise SignRefused(f"{rel} is not in {rev[:12]}: it must be committed at the tag being signed")
    return proc.stdout


def build(record_rel: str, record_bytes: bytes, spec_rel: str, spec_text: str, tag: str, commit: str,
          signed_at: str) -> dict:
    """The signed body, from bytes rather than from a filesystem, so a verifier can rebuild it from `git show`."""
    record_sha = hashlib.sha256(record_bytes).hexdigest()
    # checked, not claimed: the spec must name the record AND carry its hash, since the record cannot carry it
    cites = record_rel in spec_text and record_sha in spec_text
    return {"schema": SCHEMA, "record": record_rel, "record_sha256": record_sha, "tag": tag, "commit": commit,
            "spec": spec_rel, "spec_cites_record": cites, "signed_under": POLICY, "signed_at": signed_at}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("record", help="repo-relative path to the pre-registration markdown")
    ap.add_argument("spec", help="repo-relative path to the benchmark spec that carries its hash")
    ap.add_argument("--tag", required=True, help="the freeze tag; the record and spec are read from it, not from disk")
    ap.add_argument("--key", required=True, help="the gate key; read, never printed")
    ap.add_argument("--cert", default=None, help="defaults to packages/bundles/keys/certs/<key id>.json")
    ap.add_argument("--signed-at", default=None, help="ISO time; defaults to now. An INPUT, so the bytes reproduce")
    ap.add_argument("--out", default=None, help="defaults to <record without .md>.signed.json")
    a = ap.parse_args()

    try:
        commit = commit_of(a.tag)
        record_bytes = committed(commit, a.record)
        spec_text = committed(commit, a.spec).decode("utf-8")
    except SignRefused as e:
        print(f"refused: {e}")
        return 1

    key_path = REPO / a.key
    key_id = key_path.stem.replace("bundle-", "")
    cert_path = REPO / a.cert if a.cert else REPO / "packages" / "bundles" / "keys" / "certs" / f"{key_id}.json"
    cert = json.loads(cert_path.read_text(encoding="utf-8"))
    if cert["cert"]["purpose"] != PURPOSE:
        print(f"refused: {cert_path.name} certifies purpose {cert['cert']['purpose']!r}, not {PURPOSE!r}")
        return 1

    body = build(a.record, record_bytes, a.spec, spec_text, a.tag, commit, a.signed_at or iso(now_utc()))
    if not body["spec_cites_record"]:
        print(f"refused: {a.spec} at {a.tag} does not carry both {a.record} and its sha256 {body['record_sha256']}.\n"
              f"         A document cannot contain its own hash, so the spec is what makes it checkable.")
        return 1

    signed = sign_object(body, key_path.read_text(encoding="utf-8").strip(), cert)
    out = REPO / (a.out or a.record.replace(".md", ".signed.json"))
    # LF, explicitly: on a Windows checkout the default text mode writes CRLF, so the file on disk would not be the
    # bytes git stores -- and the anchor script, which hashes the disk and compares it with HEAD, would refuse it
    out.write_text(json.dumps(signed.to_json(), indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"record     : {body['record']}")
    print(f"sha256     : {body['record_sha256']}")
    print(f"tag        : {body['tag']}   commit: {body['commit'][:12]}")
    print(f"spec cites : {body['spec']}  -> {body['spec_cites_record']}")
    print(f"signed_at  : {body['signed_at']}   key: {signed.key_id}")
    print(f"written    : {out.relative_to(REPO).as_posix()}")
    print(f"\nnext: commit it, then anchor it.\n  uv run python packages/platform/scripts/anchor_file.py "
          f"{out.relative_to(REPO).as_posix()} benchmarks/anchors/attempt4 --what \"the signed attempt 4 pre-registration\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
