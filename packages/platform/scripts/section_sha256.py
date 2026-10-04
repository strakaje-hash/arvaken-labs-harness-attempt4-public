"""The fingerprint of one section of a markdown record (founder rulings 5 and 6, 2026-09-23).

    python section_sha256.py <file> "<heading text>" [--commit <rev>]

A section is the bytes from its heading line up to, not including, the next line that begins with "## ", or the end of
the file; the heading is matched by its exact text after the "## ". Its SHA-256 is taken over those bytes as committed:
UTF-8, LF line endings (a Windows working copy's CRLF is normalised to LF, which is what git stores). With --commit, the
file is read from that commit instead of the working copy.

The freeze-4 record carries the SHA-256 of two freeze-notes sections it does not disclose -- the sealed readings and the
void note -- so it commits to them before the run; when the freeze notes are published, anyone can recompute both.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


class SectionNotFound(ValueError):
    pass


def section(text: str, heading: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    starts = [i for i, line in enumerate(lines) if line == f"## {heading}"]
    if len(starts) != 1:
        raise SectionNotFound(f"{len(starts)} sections headed {heading!r}; exactly one is required")
    a = starts[0]
    b = next((i for i in range(a + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[a:b])
    return body + "\n" if b < len(lines) else body


def section_sha256(text: str, heading: str) -> str:
    return hashlib.sha256(section(text, heading).encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("heading")
    ap.add_argument("--commit", default=None)
    a = ap.parse_args(argv)
    if a.commit:
        rel = Path(a.file).resolve().relative_to(REPO).as_posix()
        text = subprocess.run(["git", "-C", str(REPO), "show", f"{a.commit}:{rel}"], capture_output=True, check=True).stdout.decode("utf-8")
    else:
        text = Path(a.file).read_bytes().decode("utf-8")
    try:
        print(section_sha256(text, a.heading))
    except SectionNotFound as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
