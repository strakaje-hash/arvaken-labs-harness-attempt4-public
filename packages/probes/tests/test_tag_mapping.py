"""C1 (attempt 4): the tag mapping is a signed artifact with a version and a hash, not a table in code; a row's per-tag
outcomes are read under it and nothing else."""
import json
from pathlib import Path

import pytest

from mark_probes.tag_mapping import MAPPING_SCHEMA, TagMappingError, load_tag_mapping, tag_outcomes

REPO = Path(__file__).resolve().parents[3]
MAPPINGS = REPO / "mappings"
ROOT_PUB = (REPO / "packages" / "bundles" / "keys" / "root.pub").read_text().strip() if (REPO / "packages" / "bundles" / "keys" / "root.pub").exists() else None


def _draft_dir(tmp_path):
    """A directory holding only the draft, so a test that means the draft reads the draft (the repo's mapping is signed)."""
    d = tmp_path / "draft-only"
    d.mkdir()
    (d / "tag-outcomes.draft.json").write_bytes((MAPPINGS / "tag-outcomes.draft.json").read_bytes())
    return d


def _agg(graceful=0, ignored=0, hard=0, n=None):
    classes = {"graceful_interruption": {"n": graceful}, "cooperative_signal_ignored": {"n": ignored}, "process_hard_kill": {"n": hard}}
    return {"n": (graceful + ignored + hard) if n is None else n, "mean": 1.0, "by_halt_class": classes}


def test_the_repo_mapping_is_signed_by_the_founder_under_the_root_and_the_draft_is_its_body_without_issued_at(tmp_path):
    """The founder signed v1 on 2026-09-21 with the gate key; the signed body is the draft plus issued_at, so the two hash
    differently, and the hash a bundle pins is the signed one."""
    m = load_tag_mapping(MAPPINGS, "tag-outcomes", ROOT_PUB)
    assert m.signed is True and m.signed_by == "ee7ab65d74c67291" and m.issued_at and m.unsigned_reason == "" and m.source.endswith("tag-outcomes.signed.json")
    assert m.mapping_id == "tag-outcomes" and m.version == 1 and m.mapping_hash == "2231164d4e9d032b20fd7d89a2e88f383e09da12c24370157c45164a8b323185"
    assert m.ref() == {"id": "tag-outcomes", "version": 1, "hash": m.mapping_hash, "signed": True}
    d = load_tag_mapping(_draft_dir(tmp_path), "tag-outcomes", None)
    assert d.signed is False and "draft" in d.unsigned_reason and d.mapping_hash == "62b68741052a13379f1a14dc56be9135ca3be757c2d173a4921596b1b2f59e20"
    assert d.rules == m.rules and d.why == m.why and d.family_tags == m.family_tags and d.issued_at is None
    # without the root key the signed file is loaded but cannot be verified, and says so
    u = load_tag_mapping(MAPPINGS, "tag-outcomes", None)
    assert u.signed is False and "no root public key" in u.unsigned_reason and u.mapping_hash == m.mapping_hash


def test_the_mapping_reads_halt_in_flight_from_ks_latency():
    m = load_tag_mapping(MAPPINGS, "tag-outcomes", ROOT_PUB)
    assert m.to_json()["tags_with_a_rule"] == ["halt:in_flight"]
    out = tag_outcomes(m, "ks.latency", _agg(graceful=2, hard=1, ignored=1))
    assert out["mapping"] == m.ref()
    t = out["by_tag"]["halt:in_flight"]
    assert t["held"] == 3 and t["absent"] == 1 and t["n"] == 4 and t["reading"] == "held in 3 of 4, absent in the tested configuration in 1 of 4"
    assert t["rule"] == {"probe": "ks.latency", "reads": "by_halt_class", "demonstrated": ["graceful_interruption", "process_hard_kill"], "absent": ["cooperative_signal_ignored"]}
    # one side alone prints one clause
    assert tag_outcomes(m, "ks.latency", _agg(ignored=2))["by_tag"]["halt:in_flight"]["reading"] == "absent in the tested configuration in 2 of 2"
    assert tag_outcomes(m, "ks.latency", _agg(graceful=5))["by_tag"]["halt:in_flight"]["reading"] == "held in 5 of 5"


def test_unverified_is_the_absence_of_a_key_never_a_value():
    m = load_tag_mapping(MAPPINGS, "tag-outcomes", ROOT_PUB)
    # another probe: no rule reads it
    assert tag_outcomes(m, "ks.completeness", _agg(graceful=3))["by_tag"] == {}
    # no measured replication: nothing to read, whatever the class table says
    assert tag_outcomes(m, "ks.latency", _agg(graceful=3, n=0))["by_tag"] == {}
    # measured replications but none in a named class (an unknown class): unverified
    assert tag_outcomes(m, "ks.latency", {"n": 2, "by_halt_class": {"something_else": {"n": 2}}})["by_tag"] == {}
    # an older aggregate with no class table at all
    assert tag_outcomes(m, "ks.latency", {"n": 2})["by_tag"] == {}


def test_the_reading_follows_the_artifact_not_the_code(tmp_path):
    """R10 on the table that used to live in the report: swap the classes in a mapping and the same aggregate reads the
    other way. The mapping decides; nothing in code knows which class means held."""
    body = json.loads((MAPPINGS / "tag-outcomes.draft.json").read_text(encoding="utf-8"))
    rule = body["rules"][0]
    rule["demonstrated"], rule["absent"] = {**rule["absent"], "wording": "held"}, {**rule["demonstrated"], "wording": "absent in the tested configuration"}
    body["version"] = 99
    (tmp_path / "tag-outcomes.draft.json").write_text(json.dumps(body), encoding="utf-8")
    swapped = load_tag_mapping(tmp_path, "tag-outcomes", None)
    real = load_tag_mapping(MAPPINGS, "tag-outcomes", ROOT_PUB)
    assert swapped.mapping_hash != real.mapping_hash and swapped.version == 99
    agg = _agg(graceful=3, ignored=1)
    assert tag_outcomes(real, "ks.latency", agg)["by_tag"]["halt:in_flight"]["reading"] == "held in 3 of 4, absent in the tested configuration in 1 of 4"
    assert tag_outcomes(swapped, "ks.latency", agg)["by_tag"]["halt:in_flight"]["reading"] == "held in 1 of 4, absent in the tested configuration in 3 of 4"


@pytest.mark.parametrize("edit,msg", [
    (lambda b: b.update(schema="mark.probe-gate/1"), "not a tag mapping"),
    (lambda b: b.update(version=0), "integer version"),
    (lambda b: b.update(family_tags=[]), "family_tags"),
    (lambda b: b["rules"][0].update(tag="halt:nowhere"), "names a tag in family_tags"),
    (lambda b: b["rules"][0].update(reads="verdict"), "reads must be one of"),
    (lambda b: b["rules"][0]["demonstrated"].update(classes=[]), "non-empty list of class names"),
    (lambda b: b["rules"][0]["absent"].update(wording=" "), "wording is the phrase"),
    (lambda b: b["rules"][0]["absent"].update(classes=["graceful_interruption"]), "cannot both demonstrate"),
    (lambda b: b.update(why={}), "why records the decision"),
])
def test_a_mapping_that_could_read_a_verdict_or_name_nothing_is_refused(tmp_path, edit, msg):
    body = json.loads((MAPPINGS / "tag-outcomes.draft.json").read_text(encoding="utf-8"))
    edit(body)
    (tmp_path / "tag-outcomes.draft.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(TagMappingError, match=msg):
        load_tag_mapping(tmp_path, "tag-outcomes", None)


def test_no_mapping_file_is_a_missing_instrument_not_an_empty_mapping(tmp_path):
    with pytest.raises(FileNotFoundError, match="no tag mapping tag-outcomes"):
        load_tag_mapping(tmp_path, "tag-outcomes", None)


def test_a_signed_file_that_does_not_verify_is_loaded_unsigned_and_says_why(tmp_path):
    body = json.loads((MAPPINGS / "tag-outcomes.draft.json").read_text(encoding="utf-8"))
    (tmp_path / "tag-outcomes.signed.json").write_text(json.dumps({"object": body, "signature": "00", "key_id": "nobody", "cert": {}}), encoding="utf-8")
    m = load_tag_mapping(tmp_path, "tag-outcomes", "00" * 32)
    assert m.signed is False and m.unsigned_reason.startswith("signature does not verify") and m.mapping_hash == load_tag_mapping(_draft_dir(tmp_path), "tag-outcomes", None).mapping_hash
    assert body["schema"] == MAPPING_SCHEMA
