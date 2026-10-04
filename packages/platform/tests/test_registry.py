import hashlib
import html
import importlib
import re
from pathlib import Path

import pytest
import yaml

from mark_platform import registry
from mark_platform.registry import RegistryError, applicable, load

REPO = Path(__file__).resolve().parents[3]
BASE = {"id": "x", "name": "x", "category": "reference", "license": "MIT", "license_checked": "y", "launch": {"module": "m"}, "instrumented": "i", "halt": "h"}
LAB = {"study_set": "labs", "edition": "lab_built", "execution_class": "Permitted", "publication_class": "Unconditional", "tag_states": []}
COMMUNITY = {"id": "y", "name": "y", "category": "agent", "license": "MIT", "license_checked": "y", "launch": {"module": "m"}, "instrumented": "i", "halt": "h",
             "repo": "https://github.com/langchain-ai/langgraph", "sha": "a" * 40, "study_set": "labs", "edition": "community",
             "license_url": "https://github.com/langchain-ai/langgraph/blob/pin/LICENSE", "license_retrieved_at": "2026-09-15T14:30:14Z",
             "enterprise_delta_ref": "langgraph", "section_26": "open_source_edition_of_commercial_product", "execution_class": "Permitted", "publication_class": "Unconditional", "tag_states": [],
             "finish_tool": None, "finish_tool_note": "ends when a reply carries no tool call"}
FIVE = {"url": "https://example.test/doc", "revision": "r", "retrieved_at": "2026-09-15T00:00:00Z", "section": "s", "quote": "a short quote",
        "evidence": "targets/evidence/2026-09-15/langgraph-README.md"}


def test_registry_loads_and_every_launch_module_imports():
    reg = load()
    assert {"scripted", "langgraph-ref", "openhands-sdk", "none", "agt-kill-switch", "langgraph-interrupt"} <= set(reg)
    for t in reg.values():
        mod = importlib.import_module(t.launch_module)  # the adapter file must exist even where its deps are optional
        assert hasattr(mod, "build"), f"{t.launch_module} has no build()"
        assert t.license in registry.OSI_APPROVED


def test_none_control_is_registered_and_is_the_baseline():
    reg = load()
    assert reg["none"].category == "control" and "ceiling" in reg["none"].halt


def test_external_targets_are_pinned():
    reg = load()
    pinned = [t for t in reg.values() if t.repo]
    assert len(pinned) >= 2, f"only {len(pinned)} repo target(s): a registry that lost them would pass this silently"
    for t in pinned:
        assert t.sha and len(t.sha) == 40


@pytest.mark.parametrize("bad,msg", [
    ({"id": "x", "name": "x", "category": "agent", "license": "Proprietary", "license_checked": "y", "launch": {"module": "m"}, "instrumented": "i", "halt": "h", "repo": "r", "sha": "a" * 40}, "not an accepted OSI"),
    ({"id": "x", "name": "x", "category": "agent", "license": "MIT", "license_checked": "y", "launch": {"module": "m"}, "instrumented": "i", "halt": "h", "repo": "r", "sha": "abc"}, "sha pin"),
    ({"id": "x", "name": "x", "category": "agent", "license": "MIT", "license_checked": "y", "launch": {"module": "m"}, "instrumented": "i", "halt": "h"}, "needs repo"),
    ({"id": "x", "name": "x", "category": "tool", "license": "MIT", "license_checked": "y", "launch": {"module": "m"}, "instrumented": "i", "halt": "h"}, "category"),
    ({"id": "x", "name": "x", "category": "agent", "license": "MIT", "license_checked": "y", "launch": {"module": "m"}, "instrumented": "i", "repo": "r", "sha": "a" * 40}, "missing halt"),
])
def test_validator_refuses(bad, msg):
    with pytest.raises(RegistryError, match=msg):
        registry._validate(bad)


def test_framework_native_controls_only_apply_to_their_agent():
    reg = load()
    assert applicable(reg["langgraph-interrupt"], reg["langgraph-ref"])[0]
    ok, reason = applicable(reg["langgraph-interrupt"], reg["openhands-sdk"])
    assert not ok and reason.startswith("control_not_applicable")
    assert applicable(reg["agt-kill-switch"], reg["openhands-sdk"])[0]
    assert applicable(reg["none"], reg["scripted"])[0]


# ---- change-set B3/B4/B5 (founder rulings 2026-09-15): study set, edition, terms state, tag states ----

@pytest.mark.parametrize("bad,msg", [
    ({**BASE, **LAB, "study_set": "demo"}, "study_set must be one of"),
    ({**COMMUNITY, "edition": "commercial"}, "commercial targets are not registered"),
    ({**BASE, **LAB, "enterprise_delta_ref": "langgraph"}, "carries no enterprise delta"),
    ({**BASE, **LAB, "section_26": "open_source_subject"}, "carries no enterprise delta or section_26"),
    ({**COMMUNITY, "section_26": None}, "section_26 must be one of"),
    ({**BASE, **LAB, "license_url": "https://example.test/LICENSE"}, "needs license_retrieved_at"),
    ({**COMMUNITY, "license_url": None}, "needs license_url"),
    ({**COMMUNITY, "license_retrieved_at": "yesterday"}, "ISO 8601"),
    ({**COMMUNITY, "publication_class": "Allowed"}, "publication_class must be one of"),
    ({k: v for k, v in {**BASE, **LAB}.items() if k != "tag_states"}, "tag_states must be recorded"),
    ({**COMMUNITY, "tag_states": [{"tag": "kill"}]}, "family tag"),
    ({**COMMUNITY, "tag_states": [{"tag": "halt", "claimed": "claimed", "source": {k: v for k, v in FIVE.items() if k != "quote"}}]}, r"missing \['quote'\]"),
    ({**COMMUNITY, "tag_states": [{"tag": "halt", "claimed": "claimed", "source": {**FIVE, "quote": "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"}}]}, "under fifteen words"),
    ({**COMMUNITY, "tag_states": [{"tag": "halt", "claimed": "claimed", "source": {**FIVE, "evidence": "targets/evidence/2026-09-15/no-such-file.html"}}]}, "preserved evidence file"),
    ({**COMMUNITY, "tag_states": [{"tag": "halt", "claimed": "claimed", "source": FIVE, "demonstrated": "held"}]}, "never records a demonstrated state"),
    ({**COMMUNITY, "tag_states": [{"tag": "halt", "claimed": "none_found"}]}, "names the documents checked"),
    ({**COMMUNITY, "tag_states": [{"tag": "halt", "claimed": "undetermined"}]}, "names the missing element"),
])
def test_the_terms_and_tag_fields_are_refused_when_they_would_let_an_unstated_or_unsourced_claim_in(bad, msg):
    with pytest.raises(RegistryError, match=msg):
        registry._validate(bad)


def test_study_sets_are_disjoint_by_upstream_subject_not_by_target_id():
    """Two targets on one repository are one subject: a demo target on the Lab's LangGraph repository overlaps even under another id."""
    labs = registry._validate({**COMMUNITY, "id": "lg-agent"})
    demo = registry._validate({**COMMUNITY, "id": "lg-interrupt-demo", "study_set": "platform_demo"})
    other = registry._validate({**COMMUNITY, "id": "oh", "study_set": "platform_demo", "repo": "https://github.com/OpenHands/software-agent-sdk/"})
    rec = registry.study_sets_record([labs, demo, other])
    assert rec["overlap"] == ["https://github.com/langchain-ai/langgraph"] and rec["disjoint"] is False
    assert registry.study_sets_record([labs, other]) == {"rule": rec["rule"], "labs": ["https://github.com/langchain-ai/langgraph"],
                                                         "platform_demo": ["https://github.com/openhands/software-agent-sdk"], "overlap": [], "disjoint": True}


def test_every_attempt3_target_is_labs_and_every_open_source_subject_carries_its_licence_and_delta():
    reg = load()
    assert {t.study_set for t in reg.values()} == {"labs"} and registry.study_sets_record(reg.values())["disjoint"] is True
    deltas = registry.load_enterprise_deltas()
    for t in reg.values():
        if t.edition == "community":
            assert t.repo and t.license_url.startswith(f"{t.repo}/blob/{t.sha}/") and t.enterprise_delta_ref in deltas, t.id
        else:
            assert t.edition == "lab_built" and not t.enterprise_delta_ref and t.section_26 is None and not t.tag_states, t.id
    # Policy 2.0 §26 and Part IX item 4, as signed 2026-09-15:
    # - the three controls are open-source editions of commercial products;
    # - langgraph-ref is a Lab-built agent on the LangGraph library (§3B);
    # - the OpenHands agent is not classified by the policy.
    assert reg["langgraph-ref"].edition == "lab_built" and reg["langgraph-ref"].repo == "https://github.com/langchain-ai/langgraph"
    assert {t.id: t.section_26 for t in reg.values() if t.section_26} == {
        "openhands-sdk": "not_classified_at_signing", "agt-kill-switch": "open_source_edition_of_commercial_product",
        "langgraph-interrupt": "open_source_edition_of_commercial_product", "openhands-pause": "open_source_edition_of_commercial_product"}
    assert {t.id: [s["tag"] for s in t.tag_states] for t in reg.values() if t.tag_states} == {
        "agt-kill-switch": ["halt", "halt:in_flight"], "langgraph-interrupt": ["gate", "halt:pre_execution", "halt:in_flight"], "openhands-pause": ["halt", "halt:in_flight"]}


def _visible_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".html":
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return " ".join(text.replace(chr(0x200B), "").split())


def test_every_cited_quote_is_in_its_preserved_document_and_every_document_is_byte_exact():
    """The owner of a claim is its document: the quote is found in the preserved copy, and the copy is the bytes retrieved."""
    ev = REPO / "targets" / "evidence" / "2026-09-15"
    lines = (ev / "retrieved.sha256").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 16
    for line in lines:
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((ev / name.lstrip("*")).read_bytes()).hexdigest() == digest, name
    sources = [s["source"] for t in load().values() for s in t.tag_states if s["claimed"] == "claimed"]
    for d in registry.load_enterprise_deltas().values():
        sources += [s for s in (d.get("source"), d.get("commercial_edition_named_by")) if s]
    assert len(sources) == 7
    for s in sources:
        assert " ".join(s["quote"].split()) in _visible_text(REPO / s["evidence"]), (s["evidence"], s["quote"])


def test_the_enterprise_deltas_file_is_refused_without_cited_features_or_documents_checked(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text(yaml.safe_dump({"schema": "mark.enterprise-deltas/1", "deltas": {"x": {"subject": "s", "tested_edition": "t", "commercial_edition": "X Cloud", "features": ["a"], "source": {"url": "u"}}}}), encoding="utf-8")
    with pytest.raises(RegistryError, match="five-part standard"):
        registry.load_enterprise_deltas(p)
    p.write_text(yaml.safe_dump({"schema": "mark.enterprise-deltas/1", "deltas": {"x": {"subject": "s", "tested_edition": "t", "commercial_edition": None, "features": []}}}), encoding="utf-8")
    with pytest.raises(RegistryError, match="documents checked"):
        registry.load_enterprise_deltas(p)
    doc = yaml.safe_load((REPO / "targets" / "registry.yaml").read_text(encoding="utf-8"))
    next(t for t in doc["targets"] if t["id"] == "openhands-sdk")["enterprise_delta_ref"] = "no-such-delta"
    reg = tmp_path / "registry.yaml"
    reg.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(RegistryError, match="no-such-delta"):
        load(reg)


def test_a_bench_run_refuses_to_open_on_a_study_set_overlap(tmp_path):
    from mark_platform.cli import main

    doc = yaml.safe_load((REPO / "targets" / "registry.yaml").read_text(encoding="utf-8"))
    doc["targets"].append(dict(next(t for t in doc["targets"] if t["id"] == "langgraph-interrupt"), id="langgraph-demo", study_set="platform_demo"))
    reg = tmp_path / "registry.yaml"
    reg.write_text(yaml.safe_dump(doc), encoding="utf-8")
    run_dir = tmp_path / "refused"
    with pytest.raises(SystemExit, match="change-set B5.*github.com/langchain-ai/langgraph"):
        main(["--tools", "inproc", "--registry", str(reg), "bench", "run", str(REPO / "benchmarks" / "oss-agent-controls-v1.yaml"), "--run-dir", str(run_dir),
              "--targets", "scripted", "--replications", "1"])
    assert not run_dir.exists(), "the refusal comes before the run opens anything"


# ---- attempt 4, A14: a control declares the paths by which it can raise a halt on its own ----

CONTROL = {**BASE, **LAB, "category": "control", "control_class": "in_process"}


@pytest.mark.parametrize("bad,msg", [
    (CONTROL, "a control records self_trigger_paths"),
    ({**CONTROL, "self_trigger_paths": "timer", "self_trigger_note": "n"}, "a control records self_trigger_paths"),
    ({**CONTROL, "self_trigger_paths": ["mood"], "self_trigger_note": "n"}, "names each of"),
    ({**CONTROL, "self_trigger_paths": ["timer", "timer"], "self_trigger_note": "n"}, "at most once"),
    ({**CONTROL, "self_trigger_paths": []}, "self_trigger_note says"),
    ({**CONTROL, "self_trigger_paths": ["watchdog"], "self_trigger_note": "  "}, "self_trigger_note says"),
    ({**BASE, **LAB, "self_trigger_paths": []}, "only for controls"),
    ({**BASE, **LAB, "self_trigger_note": "n"}, "only for controls"),
])
def test_a_control_without_a_self_trigger_declaration_and_a_non_control_with_one_are_refused(bad, msg):
    with pytest.raises(RegistryError, match=msg):
        registry._validate(bad)


def test_a_self_trigger_declaration_loads_and_is_in_the_json_and_the_probe_reads_the_same_vocabulary():
    t = registry._validate({**CONTROL, "self_trigger_paths": ["timer", "anomaly"], "self_trigger_note": "arms a session timer"})
    assert t.self_trigger_paths == ("timer", "anomaly") and t.to_json()["self_trigger_paths"] == ["timer", "anomaly"] and t.to_json()["self_trigger_note"] == "arms a session timer"
    # the registry writes the vocabulary and ks.false_halt reads it; the probes package cannot import the platform, so the two copies are held equal here
    from mark_probes.killswitch_more import SELF_TRIGGER_PATHS as read_by_probe

    assert read_by_probe == registry.SELF_TRIGGER_PATHS == ("watchdog", "timer", "anomaly")
