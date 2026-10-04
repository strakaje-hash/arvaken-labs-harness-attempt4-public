"""Founder ruling 2026-09-16: Paper 0's counts of differing vendor documents come from each register row's
`documents_differ` field, never from prose, so the two numbers cannot drift from the rows as rows are added."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("render_register", REPO / "docs/papers/paper0/tools/render_register.py")
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def test_register_rows_pass_the_documents_differ_and_one_quote_checks():
    assert rr.problems(rr.load_rows()) == []


def test_paper0_blocks_are_exactly_what_the_rows_render():
    text = rr.PAPER.read_text(encoding="utf-8")
    assert rr.render_all(text, rr.load_rows()) == text


def test_the_register_table_lists_every_row_once_with_all_six_families():
    rows = rr.load_rows()
    table = rr.render_table(rows)
    body = [l for l in table.splitlines() if l[:2] == "| " and l.split("|")[1].strip().isdigit()]
    assert len(body) == len(rows)
    assert all(len(l.strip("|").split("|")) == 9 for l in body)  # number, product, Gate 1 and six families
    assert "unverified" not in "".join(body)  # the demonstrated state is stated once, above the table
    assert f"**Demonstrated: unverified for all {len(rows)} products; no probe has run.**" in table
    assert "| not yet read |" not in "".join(body)  # the register is complete
    import pytest
    demo = dict(rows)
    demo[next(iter(demo))] = dict(demo[next(iter(demo))], demonstrated={"guardrail": "held"})
    with pytest.raises(SystemExit):
        rr.render_table(demo)


def test_difference_sentences_quote_both_documents_without_emphasis():
    bad = {"x": {"origin": "found-in-review", "gate1": "passed", "product": "X", "cells": {},
                 "documents": {"a": {"file": "f"}, "b": {"file": "g"}},
                 "documents_differ": [{"family": "guardrail", "kind": "conflict",
                                       "says_more": {"document": "a", "section": "s", "quote": ""},
                                       "says_less": {"document": "b", "section": "s", "quote": ""},
                                       "difference": "The first page **overstates** “blocks”."}]}}
    msgs = " ".join(rr.problems(bad))
    assert "must quote words from both documents" in msgs and "no added emphasis" in msgs


_fspec = importlib.util.spec_from_file_location("paper0_fetch", REPO / "docs/papers/paper0/tools/paper0_fetch.py")
pf = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(pf)


def test_fetch_helper_refuses_to_save_a_challenge_or_an_empty_response_as_a_page():
    assert pf.challenge_reason(200, b'<html>Request unsuccessful. Incapsula incident ID: 1-2</html>')
    assert pf.challenge_reason(202, b"")
    assert pf.challenge_reason(200, b"<html><title>Just a moment...</title></html>")
    assert pf.challenge_reason(200, b"<html><h1>AI Security</h1><p>real page</p></html>") is None
    # real pages behind Imperva and Cloudflare carry these injected scripts and must not read as challenges
    assert pf.challenge_reason(200, b'<html><script src="/_Incapsula_Resource?SWJIYLWA=1"></script><h1>Real</h1></html>') is None
    assert pf.challenge_reason(200, b'<html><script src="/cdn-cgi/challenge-platform/scripts/x.js"></script><p>Real</p></html>') is None


def test_fetch_records_every_save_survives_a_failed_url_and_never_skips_another_hosts_page(tmp_path, monkeypatch):
    pages = {"https://www.x.com/a/b": b"<html>marketing</html>", "https://docs.x.com/a/b": b"<html>docs</html>"}

    def get(u, timeout=60):
        if u.endswith("/missing"):
            raise OSError("HTTP Error 404")
        return 200, u, {}, pages[u]

    monkeypatch.setattr(pf, "ROOT", tmp_path)
    monkeypatch.setattr(pf, "get", get)
    pf.fetch("v", ["https://www.x.com/a/b", "https://www.x.com/missing", "https://docs.x.com/a/b"])
    import json
    rec = json.loads((tmp_path / "v" / "retrieved.json").read_text(encoding="utf-8"))
    assert [(r["url"], r.get("file"), r.get("error")) for r in rec] == [
        ("https://www.x.com/a/b", "a_b.html", None),
        ("https://www.x.com/missing", None, "HTTP Error 404"),
        ("https://docs.x.com/a/b", "docs.x.com_a_b.html", None),
    ]
    assert (tmp_path / "v" / "docs.x.com_a_b.html").read_bytes() == pages["https://docs.x.com/a/b"]


def test_no_preserved_page_behind_a_register_cell_reads_as_a_challenge():
    # the positive control for the markers: real pages (some carry Cloudflare's injected challenge-platform script) pass
    flagged = []
    for slug, row in rr.load_rows().items():
        for key, doc in (row.get("documents") or {}).items():
            body = (REPO / row["evidence_dir"] / doc["file"]).read_bytes()
            if pf.challenge_reason(200, body):
                flagged.append(f"{slug}/{key}")
    assert flagged == []


def _row(more_rev, less_rev):
    return {"product": "P", "documents": {"a": {"revision": more_rev, "url": "u"}, "b": {"revision": less_rev, "url": "v"}},
            "documents_differ": [{"family": "guardrail", "kind": "conflict", "difference": "d",
                                  "says_more": {"document": "a", "section": "s", "quote": "q"},
                                  "says_less": {"document": "b", "section": "s", "quote": "r"}}]}


def test_the_sharper_count_needs_an_undated_broader_side_and_a_dated_narrower_side():
    rows = {
        "undated-more": _row("Undetermined: no page-specific revision date published", "2026-01-01T00:00:00Z"),
        "both-dated": _row("2026-01-01", "2026-02-01"),
        "both-undated": _row("Undetermined: x", "Undetermined: y"),
        "dated-more": _row("2026-01-01", "Undetermined: y"),
    }
    flagged = {d["slug"] for d in rr.differences(rows) if d["undated_says_more"]}
    assert flagged == {"undated-more"}
    block = rr.render(rows)
    assert "For 4 products in the register" in block
    assert "In 1 of those 4," in block
    # these synthetic rows have no cells, so the block must say the figures are working figures
    assert "Working figures, not for quotation" in block


def test_counts_are_called_final_only_when_every_family_is_read_and_no_review_is_pending():
    full = {f: {"state": "no claim found", "documents_read": []} for f in rr.FAMILIES}
    rows = {"a": {"product": "A", "documents": {}, "cells": dict(full)}}
    assert rr.incomplete(rows) == []
    assert "complete register" in rr.render(rows) and "Working figures" not in rr.render(rows)
    rows["a"]["founder_review"] = "PENDING: founder reads this row"
    assert rr.incomplete(rows) == ["a: waiting for the founder's review"]
    rows["b"] = {"product": "B", "documents": {}, "cells": {}}
    assert "b: 6 of 6 families not yet read" in rr.incomplete(rows)
    assert "Working figures, not for quotation" in rr.render(rows)


def test_a_hand_edited_count_is_caught():
    text = rr.PAPER.read_text(encoding="utf-8")
    block = rr.render(rr.load_rows())
    tampered = text.replace(block, block.replace("For ", "For 99 ", 1))
    assert tampered != text
    assert rr.updated_paper(tampered, block) != tampered


def test_headline_and_risk_numbers_come_from_the_rows_and_the_headline_refuses_a_demonstrated_row():
    import pytest
    full = {f: {"state": "no claim found", "documents_read": []} for f in rr.FAMILIES}
    rows = {"a": {"cells": dict(full, **{"halt:pre_execution": {"state": "claimed"}, "guardrail": {"state": "claimed"}})},
            "b": {"cells": dict(full, **{"halt:in_flight": {"state": "Undetermined"}})}}
    head = rr.render_headline(rows)
    assert "Across 2 products, 1 claims the ability to refuse" in head and "and 0 claim the ability to stop" in head
    risk = rr.render_risk(rows)
    assert "1 product claims a guardrail capability and 0 claim an evidence capability" in risk
    rows["a"]["demonstrated"] = {"halt:pre_execution": "held"}
    with pytest.raises(SystemExit):
        rr.render_headline(rows)


def test_no_replacement_character_in_the_register_or_the_paper():
    # found 2026-09-16: a mis-decoded console display put U+FFFD into a recorded section name
    files = sorted(rr.REGISTER.glob("*.json")) + [rr.PAPER]
    assert [f.name for f in files if "\ufffd" in f.read_text(encoding="utf-8")] == []


def test_population_and_method_blocks_count_from_the_rows():
    rows = rr.load_rows()
    pop = rr.render_population(rows)
    assert f"The register has {len(rows)} rows" in pop
    assert sum(int(l[2:].split()[0]) for l in pop.splitlines() if l.startswith("- ")) == len(rows)
    assert "[REGISTER" not in rr.PAPER.read_text(encoding="utf-8").split("-->", 1)[1]  # no placeholder left below the header
    docs = sum(len(r.get("documents") or {}) for r in rows.values())
    assert f"rests on {docs} documents" in rr.render_method(rows)
    moved = {"a": {"origin": "ledger-deferred", "gate1": "passed", "product": "A", "vendor": "A", "cells": {}},
             "b": {"origin": "ledger-register", "gate1": "passed", "product": "B", "vendor": "B", "cells": {}}}
    text = rr.render_population(moved)
    assert "placed 1 of these rows in a different group: A." in text
    assert "Not yet screened" in text and "a: 6 of 6 families not yet read" in text


def test_the_paper_does_not_name_the_unpublished_study():
    # founder ruling 2026-09-16: "attempt 3" is internal vocabulary and the study is not public when Paper 0 is released
    import re
    text = rr.PAPER.read_text(encoding="utf-8")
    assert re.search(r"attempt[ -]?3", text, re.I) is None
    for slug, row in rr.load_rows().items():
        for fam, c in row["cells"].items():
            if c["state"] == "excluded":
                assert re.search(r"attempt[ -]?3", c["excluded_because"], re.I) is None, (slug, fam)
