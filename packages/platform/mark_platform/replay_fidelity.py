"""Replay fidelity (founder rulings 2026-09-12): "replayable" is a measured rate per run, under that run's own server.

The Q3 replays showed that a serving condition reproduces itself: 30 of 30 body-condition combinations gave one token
path. They also showed that the run's own server reproduced only 6 of 10 captured first replies, so something
history-dependent sits in the condition; prefix-cache contents are the prime candidate. Measurement runs now serve with
prefix caching off, and every run ends with the fidelity check, on the host that ran it, before the pod is released:

- every scenario's captured first request is re-sent byte-identical, one at a time, in capture order, to the server
  the run recorded;
- the reply's path hash (reply_paths.HASH_RULE) is compared with the captured reply's;
- every mismatch is classified by where the two replies diverge and whether the divergence reaches an effect
  (`classify_mismatch`), and its replayed body is kept beside the captured one. A percentage never carries an
  assumption that a mismatch is benign: on Q3 one divergence was a summary's wording (token 29) and another decided
  whether a finish call followed (token 756), which decides whether the agent takes another turn;
- the rate, the classified mismatches, the concurrency in effect during the replay and the server's condition at
  replay time are recorded, with whether that condition is the run's. A different condition measures replay under that
  condition, not fidelity to the run;
- the bundle records it before signing, like the close re-decision: results.json, a ledger record and the unsigned
  manifest. A signed bundle is refused, and so is a second record under the same arm name.

What a rate licenses is pre-registered in the benchmark spec (`replay:`), never set in this code:
- `replayable_at` (0.99): the paper's word "replayable", at or above the rate. "All but one" also permits it, but only
  when that one mismatch is classified benign, and then only once it is explained;
- `poor_below` (0.95): operational, the trigger for the order-dependence arm;
- between the two, the measured rate is stated and the word is not used. A run whose spec pre-registers neither states
  its rate only.

The order-dependence arm (`replay_order_dependence`) runs only when the run-server arm is poor, against a server
restarted with --enforce-eager. An eager server cannot measure fidelity to a run served with CUDA graphs, because its
code paths differ. It answers the question underneath: does output stop depending on request history when graphs are
off? The captured first requests are replayed in three orders (as captured, reversed, and shuffled with a recorded
seed), and the replays are compared with each other. Three orders agreeing is strong evidence of order independence,
not proof."""
from __future__ import annotations

import hashlib
import http.client
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from mark_ledger.canonical import object_hash, sha256_hex
from mark_ledger.store import Ledger, Provenance

from .model_proxy import TOOL_CALL_MARKUP, reply_choices, summarize_exchange
from .serving import ServingSampler, serving_condition, serving_record

Post = Callable[[str, int, str, bytes], tuple[int, bytes, str]]
SHUFFLE_SEED = 7

# Arguments a tool never executes. A difference confined to these is `description_only` (benign); every other argument
# is effect-bearing unless declared here. OpenHands' terminal `summary` describes the command for the user and the
# history; the command runs from `command` (the Q3 token-29 divergence was a summary's wording).
# Versioned and hashed with the rest of the classification rules (founder ruling 2026-09-12): this list decides whether
# a divergence is benign, so it can only grow with a version bump, a new pinned hash in the tests and the benchmark
# spec's `classification_rules_version`, never quietly.
NON_EFFECT_ARGUMENTS: dict[str, frozenset[str]] = {"terminal": frozenset({"summary"})}

MISMATCH_CLASSES = {
    "parse_state": "one reply's tool calls parsed and the other's did not: the agent acts in one and takes the text as a final answer in the other",
    "turn_structure": "a different number of tool calls, a finish call in only one, or a different finish reason: decides whether the agent takes another turn",
    "action": "a tool name or an effect-bearing argument differs: a different call would execute",
    "description_only": "only arguments the tool never executes differ (NON_EFFECT_ARGUMENTS): benign for effects; the text enters the conversation history",
    "content_only": "only the free text differs, the tool calls are identical: benign for effects; the text enters the conversation history",
    "undetermined": "the replies could not be compared structurally (no reply, an HTTP error, or several choices)",
}

CLASSIFICATION_RULES: dict[str, Any] = {
    "version": 1,
    "order_of_consequence": ["parse_state", "turn_structure", "action", "description_only", "content_only", "undetermined"],
    "benign": ["description_only", "content_only"],
    "non_effect_arguments": {tool: sorted(args) for tool, args in sorted(NON_EFFECT_ARGUMENTS.items())},
    "default": "an argument not declared in non_effect_arguments is effect-bearing",
    "classes": MISMATCH_CLASSES,
}
CLASSIFICATION_RULES_HASH = object_hash(CLASSIFICATION_RULES)


class FidelityRefused(Exception):
    pass


def _post(host: str, port: int, path: str, body: bytes, timeout: float = 900) -> tuple[int, bytes, str]:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        return r.status, r.read(), r.getheader("Content-Type") or ""
    except OSError as e:
        return 599, f"{type(e).__name__}: {e}".encode(), ""
    finally:
        conn.close()


def first_exchanges(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Each scenario's first model call, by proxy sequence, in capture order."""
    p = run_dir / "model-calls.jsonl"
    if not p.exists():
        return {}
    calls = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    first: dict[str, dict[str, Any]] = {}
    for c in sorted(calls, key=lambda c: c.get("seq") or 0):
        sid = c.get("scenario_id")
        if sid and sid not in first:
            first[sid] = c
    return first


# ---------------------------------------------------------------- classifying a mismatch
def _args(a: Any) -> Any:
    if isinstance(a, str):
        try:
            return json.loads(a)
        except ValueError:
            return {"(unparsed arguments)": a}
    return a


def _unparsed(ch: dict[str, Any]) -> bool:
    return TOOL_CALL_MARKUP in (ch.get("content") or "") and not ch.get("tool_call_list")


def classify_mismatch(captured: list[dict[str, Any]], replay: list[dict[str, Any]]) -> dict[str, Any]:
    """Where two replies to one prompt diverge, and whether the divergence reaches an effect (a different executed call,
    a different number of calls, or a different decision to take another turn). Checked in that order of consequence."""
    def out(cls: str, location: str) -> dict[str, Any]:
        benign = cls in ("description_only", "content_only")
        return {"class": cls, "location": location, "benign": benign, "reaches_effect": None if cls == "undetermined" else not benign, "meaning": MISMATCH_CLASSES[cls]}

    if len(captured) != 1 or len(replay) != 1:
        return out("undetermined", f"{len(captured)} captured choice(s), {len(replay)} replayed choice(s)")
    c, r = captured[0], replay[0]
    if _unparsed(c) != _unparsed(r):
        return out("parse_state", f"captured {'unparsed' if _unparsed(c) else 'parsed'}, replay {'unparsed' if _unparsed(r) else 'parsed'}")
    ct, rt = c.get("tool_call_list") or [], r.get("tool_call_list") or []
    c_finish, r_finish = any(t.get("name") == "finish" for t in ct), any(t.get("name") == "finish" for t in rt)
    if len(ct) != len(rt) or c_finish != r_finish or c.get("finish_reason") != r.get("finish_reason"):
        first = next((i for i, (a, b) in enumerate(zip(ct, rt)) if a.get("name") != b.get("name") or _args(a.get("arguments")) != _args(b.get("arguments"))), min(len(ct), len(rt)))
        return out("turn_structure", f"tool calls {len(ct)} vs {len(rt)} (first difference at call {first}); finish call {c_finish} vs {r_finish}; "
                                     f"finish_reason {c.get('finish_reason')} vs {r.get('finish_reason')}")
    description: str | None = None
    for i, (a, b) in enumerate(zip(ct, rt)):
        if a.get("name") != b.get("name"):
            return out("action", f"tool_calls[{i}].name: {a.get('name')} vs {b.get('name')}")
        aa, bb = _args(a.get("arguments")), _args(b.get("arguments"))
        if aa == bb:
            continue
        keys = sorted(k for k in set(aa) | set(bb) if aa.get(k) != bb.get(k)) if isinstance(aa, dict) and isinstance(bb, dict) else ["(arguments)"]
        location = f"tool_calls[{i}].arguments: {', '.join(keys)}"
        if not all(k in NON_EFFECT_ARGUMENTS.get(a.get("name") or "", frozenset()) for k in keys):
            return out("action", location)
        description = description or location
    if description:
        return out("description_only", description)
    cc, rc = c.get("content") or "", r.get("content") or ""
    if cc != rc:
        p = next((i for i, (x, y) in enumerate(zip(cc, rc)) if x != y), min(len(cc), len(rc)))
        return out("content_only", f"content at character {p}")
    return out("undetermined", "no structural difference found although the path hashes differ")


def replay_thresholds(results: dict[str, Any]) -> dict[str, Any] | None:
    """The pre-registered thresholds, from the benchmark spec the run recorded; None when it pre-registered none."""
    rp = (results.get("benchmark_spec") or {}).get("replay") or {}
    if rp.get("poor_below") is None or rp.get("replayable_at") is None:
        return None
    return {"poor_below": float(rp["poor_below"]), "replayable_at": float(rp["replayable_at"]), "classification_rules_version": rp.get("classification_rules_version"),
            "source": "benchmark spec `replay:` recorded in results.json"}


def classification_rules_record(thresholds: dict[str, Any] | None) -> dict[str, Any]:
    """The rules this code classifies by, against the version the run's spec pre-registered. When they differ (or the
    spec names none), no mismatch counts as benign: the conservative side."""
    pre = (thresholds or {}).get("classification_rules_version")
    matches = pre is not None and int(pre) == CLASSIFICATION_RULES["version"]
    return {"version": CLASSIFICATION_RULES["version"], "hash": CLASSIFICATION_RULES_HASH, "pre_registered_version": pre, "matches_pre_registration": matches,
            "benign_exemption": matches, "non_effect_arguments": CLASSIFICATION_RULES["non_effect_arguments"]}


def replay_claim(compared: int, matched: int, same_condition: bool, thresholds: dict[str, Any] | None, mismatch_classes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the paper may say about a run-server arm. `claim` is one of: not_a_fidelity_measurement, nothing_comparable,
    no_thresholds, replayable, replayable_if_mismatch_explained, rate_only. `poor` is the separate operational trigger."""
    rate = (matched / compared) if compared else None
    pct = f"{rate:.1%}" if rate is not None else "n/a"
    base = {"rate": rate, "thresholds": thresholds}
    if not same_condition:
        return {**base, "claim": "not_a_fidelity_measurement", "poor": None,
                "reading": "the server's condition at replay differs from the run's (or the run recorded none): this arm measures replay under that condition, not fidelity to the run"}
    if not compared:
        return {**base, "claim": "nothing_comparable", "poor": None, "reading": "no captured first reply was comparable"}
    if thresholds is None:
        return {**base, "claim": "no_thresholds", "poor": None,
                "reading": f"{matched} of {compared} captured first replies reproduced ({pct}); the run's spec pre-registers no replay thresholds, so the rate is stated and the word 'replayable' is not used"}
    poor = rate < thresholds["poor_below"]
    classes = mismatch_classes or []
    not_benign = sum(1 for m in classes if not m.get("benign"))
    if rate >= thresholds["replayable_at"]:
        claim, words = "replayable", f"at or above the pre-registered {thresholds['replayable_at']:.0%}: 'replayable'"
    elif compared - matched == 1 and len(classes) == 1 and classes[0].get("benign"):
        m = classes[0]
        claim, words = "replayable_if_mismatch_explained", f"all but one, and that mismatch is classified benign ({m['class']} at {m['location']}): 'replayable' once it is explained"
    elif compared - matched == 1:
        m = classes[0] if classes else {"class": "unclassified", "location": "-"}
        claim, words = "rate_only", f"all but one, but that mismatch is not classified benign ({m['class']} at {m['location']}): the measured rate is stated and the word 'replayable' is not used"
    else:
        claim, words = "rate_only", f"below the pre-registered {thresholds['replayable_at']:.0%}: the measured rate is stated and the word 'replayable' is not used"
    reading = f"{matched} of {compared} captured first replies reproduced ({pct}), {words}"
    if classes:
        reading += f"; {not_benign} of {len(classes)} mismatch(es) reach an effect or could not be classified"
    if poor:
        reading += f"; below the operational {thresholds['poor_below']:.0%}, so the --enforce-eager order-dependence arm runs"
    return {**base, "claim": claim, "poor": poor, "reading": reading}


# ---------------------------------------------------------------- shared bundle handling
def _load_verified(run_dir: Path, *, record_key: str, arm: str) -> tuple[dict[str, Any], dict[str, Any], Ledger, str, str]:
    rp, mp = run_dir / "results.json", run_dir / "manifest.unsigned.json"
    if (run_dir / "manifest.json").exists():
        raise FidelityRefused("the bundle is already signed: replays are recorded before signing, under the run's own host")
    results = json.loads(rp.read_text(encoding="utf-8"))
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    if any(a.get("arm") == arm for a in results.get(record_key) or []):
        raise FidelityRefused(f"a {record_key} record under arm {arm!r} already exists in this bundle")
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(run_dir / "ledger")
    v = led.verify(chain)
    if not v.ok or v.chain_root != manifest["evidence"]["chain_root"]:
        raise FidelityRefused("the ledger does not verify or its root differs from the unsigned manifest")
    before_sha = sha256_hex(rp.read_bytes())
    if before_sha != manifest["evidence"]["results_sha256"]:
        raise FidelityRefused("results.json hash differs from the unsigned manifest")
    return results, manifest, led, chain, before_sha


def _conditions(results: dict[str, Any], llm_url: str | None) -> tuple[str, dict[str, Any], dict[str, Any], bool]:
    serving = (results.get("environment") or {}).get("serving") or {}
    upstream = serving.get("llm_url") or llm_url
    if not upstream:
        raise FidelityRefused("no model server URL: the run recorded none and none was given")
    run_condition = serving_condition(serving)
    now = serving_record(upstream, serving.get("model") or "")
    now_condition = serving_condition(now)
    same = bool(run_condition.get("hash")) and run_condition.get("hash") == now_condition.get("hash") and object_hash(serving.get("process_args")) == object_hash(now.get("process_args"))
    return upstream, run_condition, now_condition, same


def _replay_one(run_dir: Path, sid: str, c: dict[str, Any], host: str, port: int, send: Post) -> dict[str, Any]:
    d = run_dir / "model-capture" / sid
    req_p, resp_p = d / f"{c['seq']:06d}.request.json", d / f"{c['seq']:06d}.response.json"
    if not (req_p.exists() and resp_p.exists()):
        return {"status": "not_captured"}
    body, captured_body = req_p.read_bytes(), resp_p.read_bytes()
    captured = summarize_exchange(body, c.get("http_status") or 200, captured_body)
    if (c.get("http_status") or 200) >= 400 or not captured.get("reply_path_sha256"):
        return {"status": "not_comparable", "captured_http_status": c.get("http_status")}
    status, data, ctype = send(host, port, c.get("path") or "/v1/chat/completions", body)
    replay = summarize_exchange(body, status, data, ctype)
    return {"status": "compared", "captured": captured, "replay": replay, "replay_http_status": status, "replay_body": data, "replay_ctype": ctype,
            "captured_choices": reply_choices(captured_body, bool(json.loads(body or b"{}").get("stream")), "")[0],
            "replay_choices": reply_choices(data, bool(json.loads(body or b"{}").get("stream")), ctype)[0] if status < 400 else []}


def _keep_body(run_dir: Path, dirname: str, name: str, data: bytes) -> dict[str, str]:
    """A replayed reply kept as a new artifact beside the run (the captured bodies are never touched)."""
    d = run_dir / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return {"file": f"{dirname}/{name}", "sha256": hashlib.sha256(data).hexdigest()}


def _cells_by_scenario(results: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in results.get("results") or []:
        for rep in r.get("per_replication") or []:
            out[rep.get("scenario_id")] = {"probe": r["probe"]["id"], "target": r["target"]["id"], "control": r["control"]["id"], "workload": r["workload"]["id"]}
    return out


def _safe(arm: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", arm)


def _record(run_dir: Path, results: dict[str, Any], manifest: dict[str, Any], led: Ledger, chain: str, before_sha: str, *, record_key: str, arm: str,
            summary: dict[str, Any], rows: list[dict[str, Any]], env_keys: tuple[str, ...], note: str, engine_version: str, repo_commit: str) -> dict[str, Any]:
    safe, stem = _safe(arm), record_key.replace("_", "-")
    rp, mp = run_dir / "results.json", run_dir / "manifest.unsigned.json"
    rows_name = f"{stem}-{safe}.jsonl"
    rows_path = run_dir / rows_name
    rows_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    summary = {**summary, "rows_file": rows_name, "rows_sha256": sha256_hex(rows_path.read_bytes())}
    shutil.copyfile(rp, run_dir / f"results.before-{stem}-{safe}.json")
    shutil.copyfile(mp, run_dir / f"manifest.unsigned.before-{stem}-{safe}.json")
    results.setdefault(record_key, []).append(summary)
    rp.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    after_sha = sha256_hex(rp.read_bytes())
    led.append(chain, record_key, {**summary, "engine_version": engine_version, "repo_commit": repo_commit, "results_sha256_before": before_sha, "results_sha256_after": after_sha},
               Provenance(engine_version, object_hash(results.get("environment") or {}), f"platform-{stem}"))
    from .anchoring import local_anchor

    local_anchor(led, chain)
    manifest["evidence"]["chain_root"] = led.chain_root(chain)
    manifest["evidence"]["results_sha256"] = after_sha
    manifest["environment"].setdefault(record_key, []).append({k: summary.get(k) for k in env_keys})
    manifest["notes"] = ((manifest.get("notes") or "") + f" {note}").strip()
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {"ok": True, **summary, "results_sha256_after": after_sha, "chain_root_after": manifest["evidence"]["chain_root"]}


# ---------------------------------------------------------------- the two arms
def replay_fidelity(run_dir: str | Path, *, arm: str, engine_version: str, repo_commit: str, llm_url: str | None = None, post: Post | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    results, manifest, led, chain, before_sha = _load_verified(run_dir, record_key="replay_fidelity", arm=arm)
    upstream, run_condition, now_condition, same = _conditions(results, llm_url)
    u = urlsplit(upstream)
    host, port = u.hostname or "127.0.0.1", u.port or 80
    send = post or _post
    cells = _cells_by_scenario(results)
    bodies_dir = f"replay-fidelity-{_safe(arm)}-replies"
    rows: list[dict[str, Any]] = []
    sampler = ServingSampler(upstream).start()
    try:
        for sid, c in first_exchanges(run_dir).items():
            one = _replay_one(run_dir, sid, c, host, port, send)
            row: dict[str, Any] = {"scenario_id": sid, **(cells.get(sid) or {}), "status": one["status"]}
            if one["status"] == "not_comparable":
                row["captured_http_status"] = one.get("captured_http_status")
            elif one["status"] == "compared":
                cap, rep = one["captured"], one["replay"]
                row.update(match=rep.get("reply_path_sha256") == cap["reply_path_sha256"], captured_reply_path=cap["reply_path_sha256"], replay_reply_path=rep.get("reply_path_sha256"),
                           replay_http_status=one["replay_http_status"], captured_completion_tokens=cap.get("completion_tokens"), replay_completion_tokens=rep.get("completion_tokens"),
                           captured_body=f"model-capture/{sid}/{c['seq']:06d}.response.json")
                if not row["match"]:
                    row["classification"] = (classify_mismatch(one["captured_choices"], one["replay_choices"]) if one["replay_http_status"] < 400
                                             else {"class": "undetermined", "location": f"replay HTTP {one['replay_http_status']}", "benign": False, "reaches_effect": None, "meaning": MISMATCH_CLASSES["undetermined"]})
                    row["replay_body"] = _keep_body(run_dir, bodies_dir, f"{sid}.response.json", one["replay_body"])
            rows.append(row)
    finally:
        concurrency = sampler.stop()
    compared = [r for r in rows if r["status"] == "compared"]
    matched = sum(1 for r in compared if r["match"])
    mismatches = [r for r in compared if not r["match"]]
    classes = [r["classification"] for r in mismatches]
    by_target: dict[str, dict[str, int]] = {}
    for r in compared:
        t = by_target.setdefault(r.get("target") or "unattributed", {"compared": 0, "matched": 0})
        t["compared"] += 1
        t["matched"] += int(r["match"])
    by_class: dict[str, int] = {}
    for m in classes:
        by_class[m["class"]] = by_class.get(m["class"], 0) + 1
    thresholds = replay_thresholds(results)
    rules = classification_rules_record(thresholds)
    # the claim reads a mismatch as benign only under the pre-registered rules version; the classification itself is kept either way
    claim = replay_claim(len(compared), matched, same, thresholds, classes if rules["benign_exemption"] else [{**m, "benign": False} for m in classes])
    if thresholds is not None and not rules["benign_exemption"]:
        claim["reading"] += (f"; the run's spec pre-registers classification rules version {rules['pre_registered_version']!r} and this code classifies by version "
                             f"{rules['version']}, so no mismatch counts as benign")
    summary = {"schema": "mark.replay-fidelity/1", "arm": arm, "applied": "after close, before signing, on the host that ran the matrix", "upstream": upstream,
               "order": "sequential, one request at a time, scenarios in capture order", "scenarios": len(rows), "compared": len(compared), "matched": matched,
               "not_captured": sum(1 for r in rows if r["status"] == "not_captured"), "not_comparable": sum(1 for r in rows if r["status"] == "not_comparable"),
               "by_target": by_target, "mismatch_classes": by_class, "mismatches_reaching_an_effect_or_unclassified": sum(1 for m in classes if not m["benign"]),
               "mismatches": mismatches[:50], "classification_rules": rules,
               "concurrency_in_effect": concurrency, "run_condition": run_condition, "condition_at_replay": now_condition, "same_condition_as_run": same,
               "rate": claim["rate"], "thresholds": claim["thresholds"], "replay_claim": claim["claim"], "poor": claim["poor"], "reading": claim["reading"]}
    return _record(run_dir, results, manifest, led, chain, before_sha, record_key="replay_fidelity", arm=arm, summary=summary, rows=rows,
                   env_keys=("arm", "compared", "matched", "rate", "mismatch_classes", "classification_rules", "replay_claim", "poor", "same_condition_as_run", "reading"),
                   note=f"Replay fidelity ({arm}): {claim['reading']}.", engine_version=engine_version, repo_commit=repo_commit)


def replay_order_dependence(run_dir: str | Path, *, arm: str, engine_version: str, repo_commit: str, llm_url: str | None = None, post: Post | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    results, manifest, led, chain, before_sha = _load_verified(run_dir, record_key="replay_order_dependence", arm=arm)
    upstream, run_condition, now_condition, same = _conditions(results, llm_url)
    u = urlsplit(upstream)
    host, port = u.hostname or "127.0.0.1", u.port or 80
    send = post or _post
    cells = _cells_by_scenario(results)
    firsts = first_exchanges(run_dir)
    captured_order = list(firsts)
    shuffled = list(captured_order)
    random.Random(SHUFFLE_SEED).shuffle(shuffled)
    orders = {"as_captured": captured_order, "reversed": list(reversed(captured_order)), "shuffled": shuffled}
    coincide = sorted(f"shuffled == {k}" for k in ("as_captured", "reversed") if shuffled == orders[k])
    passes: dict[str, dict[str, Any]] = {name: {} for name in orders}
    sampler = ServingSampler(upstream).start()
    try:
        for name, seq in orders.items():
            for sid in seq:
                passes[name][sid] = _replay_one(run_dir, sid, firsts[sid], host, port, send)
    finally:
        concurrency = sampler.stop()
    bodies_dir = f"replay-order-dependence-{_safe(arm)}-replies"
    rows: list[dict[str, Any]] = []
    for sid in captured_order:
        got = {name: passes[name][sid] for name in orders}
        row: dict[str, Any] = {"scenario_id": sid, **(cells.get(sid) or {}), "status": got["as_captured"]["status"]}
        if all(g["status"] == "compared" for g in got.values()):
            paths = {name: g["replay"].get("reply_path_sha256") for name, g in got.items()}
            row.update(agree=len(set(paths.values())) == 1 and all(paths.values()), paths=paths,
                       matches_captured={name: p == got[name]["captured"]["reply_path_sha256"] for name, p in paths.items()},
                       completion_tokens={name: g["replay"].get("completion_tokens") for name, g in got.items()})
            if not row["agree"]:
                ref = got["as_captured"]
                row["classification_against_as_captured"] = {name: classify_mismatch(ref["replay_choices"], g["replay_choices"]) for name, g in got.items()
                                                             if name != "as_captured" and paths[name] != paths["as_captured"]}
                row["replay_bodies"] = {name: _keep_body(run_dir, bodies_dir, f"{sid}.{name}.response.json", g["replay_body"]) for name, g in got.items()}
        rows.append(row)
    compared = [r for r in rows if "agree" in r]
    agreed = sum(1 for r in compared if r["agree"])
    eager = now_condition.get("enforce_eager")
    if not compared:
        reading = "no captured first request was comparable"
    elif agreed == len(compared):
        reading = (f"order-independent under this condition: all {len(compared)} first replies were the same in three orders "
                   "(as captured, reversed, shuffled): strong evidence, not proof")
    else:
        reading = f"order-dependent under this condition: {len(compared) - agreed} of {len(compared)} first replies differed between orders; the history dependence persists"
    if coincide:
        reading += f" ({', '.join(coincide)}: too few scenarios for three distinct orders)"
    if eager is not True:
        reading += f" (the server at replay did not state enforce_eager=True: {eager!r}, so this is not the eager arm it was meant to be)"
    summary = {"schema": "mark.replay-order-dependence/1", "arm": arm, "applied": "after close, before signing, on the host that ran the matrix, after a server restart",
               "question": "does output stop depending on request history under this serving condition",
               "not_fidelity": "the server's condition differs from the run's, so no rate here is fidelity to the run",
               "upstream": upstream, "orders": ["as captured", "reversed", f"shuffled (random.Random({SHUFFLE_SEED}))"],
               "shuffled_order_sha256": hashlib.sha256(json.dumps(shuffled).encode()).hexdigest(), "orders_coincide": coincide,
               "scenarios": len(rows), "compared": len(compared), "agreed": agreed,
               "agreement_rate": (agreed / len(compared)) if compared else None, "order_independent": (agreed == len(compared)) if compared else None,
               "enforce_eager_at_replay": eager, "disagreements": [r for r in compared if not r["agree"]][:50], "concurrency_in_effect": concurrency,
               "classification_rules": classification_rules_record(replay_thresholds(results)),
               "run_condition": run_condition, "condition_at_replay": now_condition, "same_condition_as_run": same, "reading": reading}
    return _record(run_dir, results, manifest, led, chain, before_sha, record_key="replay_order_dependence", arm=arm, summary=summary, rows=rows,
                   env_keys=("arm", "compared", "agreed", "order_independent", "enforce_eager_at_replay", "reading"),
                   note=f"Replay order dependence ({arm}): {reading}.", engine_version=engine_version, repo_commit=repo_commit)
