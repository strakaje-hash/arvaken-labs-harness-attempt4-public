"""Target registry loader + validator (Task 3.1). The YAML is the record; this module refuses entries that
would let an unpinned, unlicensed or unlaunchable target into a run."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REGISTRY_SCHEMA = "mark.target-registry/1"
CATEGORIES = ("agent", "control", "framework-native-control", "reference")
# SPDX ids of OSI-approved licenses this registry accepts. Anything else stops the run and asks (ground rule 4).
OSI_APPROVED = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0", "ISC", "GPL-3.0-only", "GPL-3.0-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "GPL-2.0-only", "GPL-2.0-or-later"}
_SHA = re.compile(r"^[0-9a-f]{40}$")
# Layer 1 change-set B5 (founder rulings 2026-09-15): terms state, edition and study set, recorded here so later passes inherit them.
STUDY_SETS = ("labs", "platform_demo")
EDITIONS = ("community", "managed", "commercial", "lab_built")
EXECUTION_CLASSES = ("Permitted", "Permitted-Safe-Harbor", "Restricted", "Undetermined")   # Testability Ledger v2 §0.3
PUBLICATION_CLASSES = ("Unconditional", "Conditional-Reciprocal", "Consent-Required", "Embargo-Coordinated", "Prohibited", "Undetermined", "Undetermined-by-Absence")
FAMILY_TAGS = ("halt", "halt:pre_execution", "halt:in_flight", "gate", "identity/credential", "guardrail", "evidence")
CLAIM_STATES = ("claimed", "none_found", "undetermined")
FIVE_PART = ("url", "revision", "retrieved_at", "section", "quote")
DELTAS_SCHEMA = "mark.enterprise-deltas/1"
# Publication and Independence Policy 2.0, §26: how an open-source subject is classified for the publication preconditions at
# signing (the signed copy is cited by hash in the records, never here)
SECTION_26 = ("open_source_edition_of_commercial_product", "open_source_subject", "not_classified_at_signing")


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    category: str
    license: str
    license_checked: str
    launch_module: str
    instrumented: str
    halt: str
    repo: str | None = None
    sha: str | None = None
    pypi: tuple[dict[str, str], ...] = field(default_factory=tuple)
    notes: str = ""
    # Task 5.3 control-class rule: in_process (shares the agent's memory space) | out_of_process (separate process,
    # gateway or proxy) | reference_instrument (the harness's own gateway: never an evaluated control, rendered
    # beside the rows, source published with the bundle; founder decision A10). Never aggregated across classes.
    control_class: str | None = None
    # Change-set B5:
    # - study_set separates the Lab's study from a platform capability demonstration;
    # - edition lab_built marks the harness's own target, so its provenance is stated;
    # - tag_states records each control-family tag as claimed from the project's own document. A run demonstrates a tag,
    #   and the report reads it; the registry never records that.
    study_set: str = ""
    edition: str = ""
    license_url: str | None = None
    license_retrieved_at: str | None = None
    enterprise_delta_ref: str | None = None
    execution_class: str = ""
    publication_class: str = ""
    tag_states: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    section_26: str | None = None
    # Attempt 4, A14: the paths by which the control can raise a halt on its OWN (watchdog | timer | anomaly), and a sentence
    # saying so from the adapter's wiring. ks.false_halt applies only to a control declaring at least one; a control that
    # fires only when the harness presses it cannot false-alarm, and its rows are not_run: no_self_trigger_path. Every
    # control records the field; an empty list is a declaration, not a default.
    self_trigger_paths: tuple[str, ...] = field(default_factory=tuple)
    self_trigger_note: str = ""
    # Founder ruling 2026-09-23: the tool by which the target's framework ends a conversation, when it has one (OpenHands:
    # `finish`), or None when it ends another way; and a sentence saying so from the adapter's wiring. The early-exit rule
    # reads it: a last turn that is a call to this tool alone is the model's final answer, as a plain text answer is. Every
    # agent records it; None is a declaration, not a default -- an undeclared target would wait for a pass to stop on it.
    finish_tool: str | None = None
    finish_tool_note: str = ""

    @property
    def subject(self) -> str:
        """The upstream subject the study sets are disjoint over: the repository, else the first pinned package, else the
        harness's own target. Two targets on one repository are one subject (langgraph-ref and langgraph-interrupt)."""
        if self.repo:
            return self.repo.rstrip("/").lower()
        if self.pypi:
            return "pypi:" + str(self.pypi[0]["name"]).lower()
        return "lab:" + self.id

    @property
    def version(self) -> str:
        if self.sha:
            return self.sha
        if self.pypi:
            return ",".join(f"{p['name']}=={p['version']}" for p in self.pypi)
        return "in-repo"

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "category": self.category, "license": self.license, "repo": self.repo, "sha": self.sha, "pypi": list(self.pypi), "launch_module": self.launch_module, "halt": self.halt, "control_class": self.control_class,
                "study_set": self.study_set, "subject": self.subject, "edition": self.edition, "version": self.version, "license_url": self.license_url, "license_retrieved_at": self.license_retrieved_at,
                "enterprise_delta_ref": self.enterprise_delta_ref, "execution_class": self.execution_class, "publication_class": self.publication_class, "tag_states": [dict(s) for s in self.tag_states], "section_26": self.section_26,
                "self_trigger_paths": list(self.self_trigger_paths), "self_trigger_note": self.self_trigger_note,
                "finish_tool": self.finish_tool, "finish_tool_note": self.finish_tool_note}


class RegistryError(ValueError):
    pass


def default_path() -> Path:
    return Path(__file__).resolve().parents[3] / "targets" / "registry.yaml"


def deltas_path() -> Path:
    return default_path().parent / "enterprise-deltas.yaml"


def load(path: str | Path | None = None, *, deltas: str | Path | None = None) -> dict[str, Target]:
    """`deltas` defaults to the repository's targets/enterprise-deltas.yaml, including for a registry copied elsewhere (tests)."""
    p = Path(path) if path else default_path()
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(f"{p}: schema must be {REGISTRY_SCHEMA}")
    out: dict[str, Target] = {}
    for e in doc.get("targets") or []:
        t = _validate(e)
        if t.id in out:
            raise RegistryError(f"duplicate target id {t.id}")
        out[t.id] = t
    if not out:
        raise RegistryError("registry has no targets")
    known = load_enterprise_deltas(deltas)
    for t in out.values():
        if t.enterprise_delta_ref and t.enterprise_delta_ref not in known:
            raise RegistryError(f"{t.id}: enterprise_delta_ref {t.enterprise_delta_ref!r} is not an entry of the enterprise deltas file")
    return out


def load_enterprise_deltas(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Change-set B4: what the commercial or managed edition adds, feature by feature, cited from the project's own documentation.
    A named commercial edition needs its source to the five-part standard and at least one feature; with none named, the
    entry lists the documents checked (what they say, not that no such edition exists)."""
    p = Path(path) if path else deltas_path()
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != DELTAS_SCHEMA:
        raise RegistryError(f"{p}: schema must be {DELTAS_SCHEMA}")
    out: dict[str, dict[str, Any]] = {}
    for key, d in (doc.get("deltas") or {}).items():
        if not isinstance(d, dict) or not d.get("subject") or not d.get("tested_edition") or not isinstance(d.get("features"), list):
            raise RegistryError(f"enterprise delta {key}: needs subject, tested_edition and a features list")
        if d.get("commercial_edition"):
            src = d.get("source") or {}
            missing = [k for k in FIVE_PART if not src.get(k)] + ([] if d["features"] else ["features"])
            if missing:
                raise RegistryError(f"enterprise delta {key}: a named commercial edition needs its source to the five-part standard and its features; missing {missing}")
            for label, s in (("source", src), ("commercial_edition_named_by", d.get("commercial_edition_named_by"))):
                if s is not None and not _evidence_exists(s.get("evidence")):
                    raise RegistryError(f"enterprise delta {key}: {label} must name its preserved evidence file under targets/evidence/, got {s.get('evidence')!r}")
        elif not d.get("documents_checked"):
            raise RegistryError(f"enterprise delta {key}: with no commercial edition named, list the documents checked")
        out[str(key)] = d
    return out


def study_sets_record(targets: Any) -> dict[str, Any]:
    """Change-set B5: the Lab's study set and a platform demonstration set are disjoint by upstream subject, not by target id
    (two targets on one repository are one subject). Recorded in every run manifest; a bench run refuses to open on an overlap."""
    ts = list(targets)
    sets = {s: sorted({t.subject for t in ts if t.study_set == s}) for s in STUDY_SETS}
    overlap = sorted(set(sets["labs"]) & set(sets["platform_demo"]))
    return {"rule": "labs and platform_demo are disjoint by upstream subject (the repository, else the package)", "labs": sets["labs"],
            "platform_demo": sets["platform_demo"], "overlap": overlap, "disjoint": not overlap}


def repo_root() -> Path:
    return default_path().parents[1]


def _evidence_exists(path: Any) -> bool:
    """A claimed source's document is preserved byte-exact in the repository, so the claim can be checked against it later."""
    return bool(path) and str(path).startswith("targets/evidence/") and (repo_root() / str(path)).is_file()


def _iso(tid: str, what: str, value: Any) -> None:
    from datetime import datetime

    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise RegistryError(f"{tid}: {what} must be an ISO 8601 timestamp, got {value!r}") from None


def _validate_tag_states(tid: str, states: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(states, list):
        raise RegistryError(f"{tid}: tag_states must be a list (empty for a target that is not a control)")
    seen: set[str] = set()
    for s in states:
        if not isinstance(s, dict) or s.get("tag") not in FAMILY_TAGS:
            raise RegistryError(f"{tid}: a tag_states entry names a family tag in {FAMILY_TAGS}, got {s!r}")
        tag = s["tag"]
        if tag in seen:
            raise RegistryError(f"{tid}: tag {tag} recorded twice")
        seen.add(tag)
        if "demonstrated" in s:
            raise RegistryError(f"{tid}: tag {tag}: the registry never records a demonstrated state; a run demonstrates it and the report reads it")
        state = s.get("claimed")
        if state == "claimed":
            src = s.get("source") or {}
            missing = [k for k in FIVE_PART if not src.get(k)]
            if missing:
                raise RegistryError(f"{tid}: tag {tag} is claimed without the five-part standard, missing {missing}; record it as undetermined, naming what is missing")
            if len(str(src["quote"]).split()) >= 15:
                raise RegistryError(f"{tid}: tag {tag}: the verbatim quote must be under fifteen words")
            if not _evidence_exists(src.get("evidence")):
                raise RegistryError(f"{tid}: tag {tag}: a claimed source must name its preserved evidence file under targets/evidence/, got {src.get('evidence')!r}")
            _iso(tid, f"tag {tag} source.retrieved_at", src["retrieved_at"])
        elif state == "none_found":
            if not s.get("documents_checked"):
                raise RegistryError(f"{tid}: tag {tag}: none_found names the documents checked")
        elif state == "undetermined":
            if not s.get("missing"):
                raise RegistryError(f"{tid}: tag {tag}: undetermined names the missing element")
        else:
            raise RegistryError(f"{tid}: tag {tag}: claimed must be one of {CLAIM_STATES}, got {state!r}")
    return tuple(dict(s) for s in states)


SELF_TRIGGER_PATHS = ("watchdog", "timer", "anomaly")


def _validate_self_trigger(tid: str, e: dict[str, Any], *, is_control: bool) -> tuple[tuple[str, ...], str]:
    """A14: every control declares its self-trigger paths and a note; a target that is not a control declares neither."""
    if not is_control:
        if "self_trigger_paths" in e or "self_trigger_note" in e:
            raise RegistryError(f"{tid}: self_trigger_paths is only for controls")
        return (), ""
    if "self_trigger_paths" not in e or not isinstance(e["self_trigger_paths"], list):
        raise RegistryError(f"{tid}: a control records self_trigger_paths (a list, empty when the control fires only on the harness's command)")
    paths = tuple(str(x) for x in e["self_trigger_paths"])
    bad = [x for x in paths if x not in SELF_TRIGGER_PATHS]
    if bad or len(set(paths)) != len(paths):
        raise RegistryError(f"{tid}: self_trigger_paths names each of {SELF_TRIGGER_PATHS} at most once, got {list(paths)}")
    note = str(e.get("self_trigger_note") or "").strip()
    if not note:
        raise RegistryError(f"{tid}: self_trigger_note says, from the adapter's wiring, what can raise a halt without the harness's command (or that nothing can)")
    return paths, note


def _validate_finish_tool(tid: str, e: dict[str, Any], *, is_control: bool) -> tuple[str | None, str]:
    """Founder ruling 2026-09-23: every agent declares how its framework ends a conversation -- the finish tool's name, or
    null -- with a note from the adapter's wiring; a control declares neither. A missing key is refused, never defaulted."""
    if is_control:
        if "finish_tool" in e or "finish_tool_note" in e:
            raise RegistryError(f"{tid}: finish_tool is only for agents")
        return None, ""
    if "finish_tool" not in e:
        raise RegistryError(f"{tid}: an agent records finish_tool (the tool its framework ends a conversation with, or null when it has none)")
    name = e["finish_tool"]
    if name is not None and (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name)):
        raise RegistryError(f"{tid}: finish_tool is a tool name or null, got {name!r}")
    note = str(e.get("finish_tool_note") or "").strip()
    if not note:
        raise RegistryError(f"{tid}: finish_tool_note says, from the adapter's wiring, how the target ends a conversation")
    return name, note


def _validate(e: dict[str, Any]) -> Target:
    tid = e.get("id")
    if not tid or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", str(tid)):
        raise RegistryError(f"bad target id {tid!r}")
    for k in ("name", "category", "license", "license_checked", "launch", "instrumented", "halt"):
        if not e.get(k):
            raise RegistryError(f"{tid}: missing {k}")
    if e["category"] not in CATEGORIES:
        raise RegistryError(f"{tid}: category {e['category']!r} not in {CATEGORIES}")
    if e["license"] not in OSI_APPROVED:
        raise RegistryError(f"{tid}: license {e['license']!r} is not an accepted OSI SPDX id; stop and ask before adding it")
    launch = e["launch"]
    if not isinstance(launch, dict) or not launch.get("module"):
        raise RegistryError(f"{tid}: launch.module required")
    repo, sha = e.get("repo"), e.get("sha")
    pypi = tuple(e.get("pypi") or [])
    if repo:
        if not sha or not _SHA.match(str(sha)):
            raise RegistryError(f"{tid}: repo targets need a 40-hex sha pin")
    elif e["category"] not in ("reference", "control") or e.get("pypi"):
        if not pypi:
            raise RegistryError(f"{tid}: an external target needs repo+sha or pypi pins")
    for p in pypi:
        if not isinstance(p, dict) or not p.get("name") or not p.get("version"):
            raise RegistryError(f"{tid}: pypi pins need name and version")
    control_class = e.get("control_class")
    if e["category"] in ("control", "framework-native-control") or (e["category"] == "reference" and str(launch["module"]).split(".")[-2:-1] == ["controls"]):
        if control_class not in ("in_process", "out_of_process", "reference_instrument"):
            raise RegistryError(f"{tid}: a control needs control_class in_process | out_of_process | reference_instrument")
    elif control_class is not None:
        raise RegistryError(f"{tid}: control_class is only for controls")
    # change-set B5: study set, edition, terms state and tag states
    if e.get("study_set") not in STUDY_SETS:
        raise RegistryError(f"{tid}: study_set must be one of {STUDY_SETS}, got {e.get('study_set')!r}")
    edition = e.get("edition")
    if edition not in EDITIONS:
        raise RegistryError(f"{tid}: edition must be one of {EDITIONS}, got {edition!r}")
    if edition in ("managed", "commercial"):
        raise RegistryError(f"{tid}: edition {edition!r}: commercial targets are not registered in this pass")
    if edition == "lab_built" and (e.get("enterprise_delta_ref") or e.get("section_26")):
        # Policy 2.0 §3B and Part IX: the Lab's own reference implementations (a Lab-built agent on a pinned library is one)
        raise RegistryError(f"{tid}: a lab_built target is the Lab's own reference implementation and carries no enterprise delta or section_26 classification")
    if edition == "community":
        for k in ("license_url", "license_retrieved_at", "enterprise_delta_ref"):
            if not e.get(k):
                raise RegistryError(f"{tid}: an open-source subject needs {k}")
        if e.get("section_26") not in SECTION_26:
            raise RegistryError(f"{tid}: section_26 must be one of {SECTION_26} (Policy 2.0 §26), got {e.get('section_26')!r}")
    if e.get("license_url"):
        if not str(e["license_url"]).startswith("https://"):
            raise RegistryError(f"{tid}: license_url must be an https URL to the licence at the pin, got {e['license_url']!r}")
        if not e.get("license_retrieved_at"):
            raise RegistryError(f"{tid}: a license_url needs license_retrieved_at")
        _iso(tid, "license_retrieved_at", e["license_retrieved_at"])
    for k, allowed in (("execution_class", EXECUTION_CLASSES), ("publication_class", PUBLICATION_CLASSES)):
        if e.get(k) not in allowed:
            raise RegistryError(f"{tid}: {k} must be one of {allowed}, got {e.get(k)!r}")
    if "tag_states" not in e:
        raise RegistryError(f"{tid}: tag_states must be recorded (an empty list for a target that is not a control)")
    tag_states = _validate_tag_states(tid, e["tag_states"])
    self_trigger_paths, self_trigger_note = _validate_self_trigger(tid, e, is_control=control_class is not None)
    finish_tool, finish_tool_note = _validate_finish_tool(tid, e, is_control=control_class is not None)
    return Target(id=tid, name=e["name"], category=e["category"], license=e["license"], license_checked=e["license_checked"], launch_module=launch["module"],
                  instrumented=e["instrumented"], halt=e["halt"], repo=repo, sha=sha, pypi=pypi, notes=e.get("notes", "") or "", control_class=control_class,
                  study_set=e["study_set"], edition=edition, license_url=e.get("license_url"), license_retrieved_at=(str(e["license_retrieved_at"]) if e.get("license_retrieved_at") else None),
                  enterprise_delta_ref=e.get("enterprise_delta_ref"), execution_class=e["execution_class"], publication_class=e["publication_class"], tag_states=tag_states,
                  section_26=e.get("section_26"), self_trigger_paths=self_trigger_paths, self_trigger_note=self_trigger_note,
                  finish_tool=finish_tool, finish_tool_note=finish_tool_note)


def applicable(control: Target, agent: Target) -> tuple[bool, str]:
    """Framework-native controls only apply to their framework's agent. Returns (ok, reason)."""
    if control.category != "framework-native-control":
        return True, ""
    pairs = {"langgraph-interrupt": {"langgraph-ref"}, "openhands-pause": {"openhands-sdk"}}
    allowed = pairs.get(control.id, set())
    return (agent.id in allowed, "" if agent.id in allowed else f"control_not_applicable: {control.id} is native to {sorted(allowed)}, not {agent.id}")
