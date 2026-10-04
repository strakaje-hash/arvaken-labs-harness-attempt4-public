"""Doc-drift (ground rule): the constitution and the ceiling/scope language live in ONE source file
(mark_probes/constitution.py); docs/CONSTITUTION.md must carry every sentence verbatim, and the build
instructions' five rules must all be present."""
from pathlib import Path

from mark_probes.constitution import CEILING, CONSTITUTION, EGRESS_BEST_EFFORT, LEGAL_STATUS, SCOPE_STATEMENT, render_markdown

DOC = Path(__file__).resolve().parents[3] / "docs" / "CONSTITUTION.md"


def test_doc_carries_every_sentence_verbatim():
    doc = DOC.read_text(encoding="utf-8")
    for _, s in CONSTITUTION + CEILING + LEGAL_STATUS:
        assert s in doc, s[:60]
    assert SCOPE_STATEMENT in doc and EGRESS_BEST_EFFORT in doc


def test_legal_status_says_what_it_is_not():
    text = " ".join(s for _, s in LEGAL_STATUS)
    assert "not an EU AI Act conformity certificate" in text and "not a notified body" in text and "evidence a relying party may use" in text


def test_doc_is_exactly_the_rendered_source():
    assert DOC.read_text(encoding="utf-8").replace("\r\n", "\n") == render_markdown()


def test_five_rules_present():
    keys = [k for k, _ in CONSTITUTION]
    assert keys == ["signed-gates", "evidence-ledger", "human-signature", "no-harm", "reproducible"]
    assert "informational" in dict(CONSTITUTION)["signed-gates"] and "valid=false" in dict(CONSTITUTION)["human-signature"]
