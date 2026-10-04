"""Replay fidelity (founder rulings 2026-09-12): "replayable" is a measured rate per run, under the run's own server.

- Every scenario's captured first request is re-sent byte-identical, one at a time, and the reply's path hash is compared
  with the captured reply's; the rate, the mismatches and whether the server's condition is the run's are recorded in
  the bundle before signing. A signed bundle, and a second record under one arm name, are refused.
- What a rate licenses is pre-registered in the benchmark spec: replayable_at (the paper's word) and poor_below (the
  trigger for the eager arm), with the measured rate stated between them and no word without them.
- The eager arm compares two replays in opposite orders with each other, never with the run."""
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mark_ledger.canonical import sha256_hex
from mark_ledger.store import Ledger
from mark_platform import serving
from mark_platform.replay_fidelity import (CLASSIFICATION_RULES, CLASSIFICATION_RULES_HASH, FidelityRefused, classification_rules_record, classify_mismatch, replay_claim,
                                           replay_fidelity, replay_order_dependence, replay_thresholds)


def test_the_classification_rules_are_pinned_so_benign_cannot_grow_quietly():
    """The list of arguments declared never executed decides whether a divergence is benign (founder ruling 2026-09-12).
    Changing any rule without a version bump, a new pinned hash here and the benchmark specs' classification_rules_version fails."""
    assert CLASSIFICATION_RULES["version"] == 1
    assert CLASSIFICATION_RULES["non_effect_arguments"] == {"terminal": ["summary"]}
    assert CLASSIFICATION_RULES["benign"] == ["description_only", "content_only"]
    assert CLASSIFICATION_RULES_HASH == "65dbd0eea7a106dcac3c91fbb716150b9e1d3a3ea1d92c71f3d99a7f00badc05"


def test_a_spec_pre_registering_other_rules_reads_no_mismatch_as_benign(tmp_path, model_server, monkeypatch):
    rec = classification_rules_record({"classification_rules_version": 2})
    assert rec["matches_pre_registration"] is False and rec["benign_exemption"] is False and rec["hash"] == CLASSIFICATION_RULES_HASH
    assert classification_rules_record(None)["benign_exemption"] is False
    url, state = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch, spec_replay={**SPEC_REPLAY, "classification_rules_version": 2})
    out = replay_fidelity(run_dir, arm="run-server", **_kw())
    assert out["classification_rules"]["benign_exemption"] is False and "no mismatch counts as benign" in out["reading"]

MODEL = "M"
SPEC_REPLAY = {"poor_below": 0.95, "replayable_at": 0.99, "classification_rules_version": 1}
LOG = ("(APIServer pid=1) INFO non-default args: {'seed': 7, 'max_num_seqs': 1024, 'enable_prefix_caching': False}\n"
       "(APIServer pid=1) INFO Chunked prefill is enabled with max_num_batched_tokens=8192.\n"
       "(EngineCore pid=2) INFO Initializing a V1 LLM engine (v0.29.0) with config: seed=7, enforce_eager=False, enable_prefix_caching=False, "
       "compilation_config={'cudagraph_mode': <CUDAGraphMode.FULL_AND_PIECEWISE: (2, 1)>, 'cudagraph_capture_sizes': [1, 2]}\n")


def _reply(text):
    return json.dumps({"choices": [{"message": {"content": None, "tool_calls": [{"function": {"name": "terminal", "arguments": json.dumps({"command": text})}}]}, "finish_reason": "tool_calls"}],
                       "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}}).encode()


@pytest.fixture
def model_server():
    # history: a reply that depends on how many requests the server has answered (the order-dependent case)
    state = {"suffix": "", "posts": 0, "history": False}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            state["posts"] += 1
            extra = f" after {state['posts']}" if state["history"] else ""
            self._send(200, _reply(req["messages"][0]["content"] + state["suffix"] + extra))

        def do_GET(self):
            if self.path == "/v1/models":
                self._send(200, json.dumps({"data": [{"id": MODEL, "max_model_len": 8192}]}).encode())
            elif self.path == "/version":
                self._send(200, b'{"version": "0.29.0"}')
            else:
                self._send(404, b"")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", state
    srv.shutdown()


def _bundle(tmp_path, url, monkeypatch, *, spec_replay=SPEC_REPLAY):
    from mark_platform.runner import calibrate, close_run, open_run

    log = tmp_path / "vllm.log"
    log.write_text(LOG, encoding="utf-8")
    monkeypatch.setenv("MARK_SERVING_LOG", str(log))
    monkeypatch.setattr(serving, "serve_cmdlines", lambda: [])
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "fidelity", llm_url=url, llm_model=MODEL, tools_mode="inproc", sandbox_cmd=[])
    ctx.benchmark_spec = {"id": "test", "replay": spec_replay}
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        for sid in ("s-a", "s-a", "s-b"):
            body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": sid}], "prompt_cache_key": sid}).encode()
            urllib.request.urlopen(urllib.request.Request(ctx.model_proxy.agent_url(sid) + "/chat/completions", data=body, method="POST",
                                                          headers={"Content-Type": "application/json"}), timeout=10).read()
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return run_dir


def _kw():
    return {"engine_version": "test", "repo_commit": "test"}


def _choice(calls, content=None, finish="tool_calls"):
    return [{"content": content, "finish_reason": finish, "tool_call_list": [{"name": n, "arguments": json.dumps(a)} for n, a in calls]}]


PAY = ("terminal", {"command": "markcall pay 12.5 INV-1", "summary": "Run markcall pay command for INV-1", "security_risk": "LOW"})


def test_a_mismatch_is_classified_by_where_the_replies_diverge_and_whether_that_reaches_an_effect():
    base = _choice([PAY, PAY])
    worded = _choice([PAY, ("terminal", {**PAY[1], "summary": "Run markcall pay command for invoice INV-1"})])
    m = classify_mismatch(base, worded)
    assert m["class"] == "description_only" and m["benign"] is True and m["reaches_effect"] is False and "tool_calls[1].arguments: summary" in m["location"]
    other_command = classify_mismatch(base, _choice([PAY, ("terminal", {**PAY[1], "command": "markcall pay 12.5 INV-2"})]))
    assert other_command["class"] == "action" and other_command["benign"] is False and other_command["reaches_effect"] is True
    risk = classify_mismatch(base, _choice([PAY, ("terminal", {**PAY[1], "security_risk": "HIGH"})]))
    assert risk["class"] == "action", "an argument not declared non-executing is effect-bearing"
    with_finish = classify_mismatch(base, _choice([PAY, PAY, ("finish", {"message": "DONE"})]))
    assert with_finish["class"] == "turn_structure" and with_finish["benign"] is False and "finish call False vs True" in with_finish["location"]
    unparsed = classify_mismatch(base, [{"content": "<tool_call>{bad</tool_call>", "finish_reason": "stop", "tool_call_list": []}])
    assert unparsed["class"] == "parse_state" and unparsed["reaches_effect"] is True
    text = classify_mismatch(_choice([PAY], content="I will pay."), _choice([PAY], content="I shall pay."))
    assert text["class"] == "content_only" and text["benign"] is True and text["location"] == "content at character 2"
    assert classify_mismatch(base, [])["class"] == "undetermined" and classify_mismatch(base, [])["reaches_effect"] is None


def test_what_a_rate_licenses_follows_the_pre_registered_thresholds_only():
    t = {**SPEC_REPLAY, "source": "spec"}
    benign = [{"class": "description_only", "location": "tool_calls[1].arguments: summary", "benign": True}]
    effect = [{"class": "turn_structure", "location": "finish call False vs True", "benign": False}]
    assert replay_claim(100, 99, True, t, effect)["claim"] == "replayable" and replay_claim(100, 99, True, t, effect)["poor"] is False
    assert "1 of 1 mismatch(es) reach an effect" in replay_claim(100, 99, True, t, effect)["reading"]
    assert replay_claim(1000, 999, True, t, benign)["claim"] == "replayable"
    one_off = replay_claim(50, 49, True, t, benign)
    assert one_off["claim"] == "replayable_if_mismatch_explained" and "classified benign" in one_off["reading"] and one_off["poor"] is False
    one_off_effect = replay_claim(50, 49, True, t, effect)
    assert one_off_effect["claim"] == "rate_only" and "not classified benign" in one_off_effect["reading"], "a mismatch is never assumed benign"
    assert replay_claim(50, 49, True, t, None)["claim"] == "rate_only"
    between = replay_claim(100, 96, True, t, effect * 4)
    assert between["claim"] == "rate_only" and between["poor"] is False and "not used" in between["reading"]
    poor = replay_claim(100, 90, True, t, effect * 10)
    assert poor["claim"] == "rate_only" and poor["poor"] is True and "enforce-eager" in poor["reading"]
    small_one_off = replay_claim(4, 3, True, t, benign)
    assert small_one_off["claim"] == "replayable_if_mismatch_explained" and small_one_off["poor"] is True, "the claim and the operational trigger are separate"
    assert replay_claim(100, 100, True, None)["claim"] == "no_thresholds" and replay_claim(100, 100, True, None)["poor"] is None
    assert replay_claim(100, 100, False, t)["claim"] == "not_a_fidelity_measurement"
    assert replay_claim(0, 0, True, t)["claim"] == "nothing_comparable"
    assert replay_thresholds({"benchmark_spec": {"replay": SPEC_REPLAY}})["replayable_at"] == 0.99 and replay_thresholds({"benchmark_spec": None}) is None


def test_the_pre_registered_values_live_in_the_benchmark_specs():
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "benchmarks"
    for name in ("oss-agent-controls-v1.yaml", "first-session.yaml"):
        assert yaml.safe_load((root / name).read_text(encoding="utf-8"))["replay"] == SPEC_REPLAY, name


def test_the_run_server_reproduces_every_first_reply_and_the_bundle_records_it(tmp_path, model_server, monkeypatch):
    url, state = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch)
    posts_before = state["posts"]
    out = replay_fidelity(run_dir, arm="run-server", **_kw())
    assert state["posts"] - posts_before == 2, "one replay per scenario: its first exchange only"
    assert out["compared"] == 2 and out["matched"] == 2 and out["rate"] == 1.0 and out["poor"] is False and out["same_condition_as_run"] is True
    assert out["replay_claim"] == "replayable" and out["thresholds"]["replayable_at"] == 0.99
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    assert results["replay_fidelity"][0]["replay_claim"] == "replayable" and manifest["environment"]["replay_fidelity"][0]["rate"] == 1.0
    assert sha256_hex((run_dir / "results.json").read_bytes()) == manifest["evidence"]["results_sha256"]
    v = Ledger(run_dir / "ledger").verify(manifest["evidence"]["chain_id"])
    assert v.ok and v.chain_root == manifest["evidence"]["chain_root"]
    rows = [json.loads(line) for line in (run_dir / out["rows_file"]).read_text(encoding="utf-8").splitlines()]
    assert {r["scenario_id"] for r in rows} == {"s-a", "s-b"} and sha256_hex((run_dir / out["rows_file"]).read_bytes()) == out["rows_sha256"]
    with pytest.raises(FidelityRefused, match="already exists"):
        replay_fidelity(run_dir, arm="run-server", **_kw())


def test_a_server_that_answers_differently_is_poor_and_the_mismatches_are_named(tmp_path, model_server, monkeypatch):
    url, state = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch)
    state["suffix"] = " (another path)"
    out = replay_fidelity(run_dir, arm="run-server", **_kw())
    assert out["matched"] == 0 and out["poor"] is True and out["replay_claim"] == "rate_only"
    assert {m["scenario_id"] for m in out["mismatches"]} == {"s-a", "s-b"} and all(m["captured_reply_path"] != m["replay_reply_path"] for m in out["mismatches"])
    # the suffix changes the executed command: classified action, and the replayed body kept beside the captured one
    assert out["mismatch_classes"] == {"action": 2} and out["mismatches_reaching_an_effect_or_unclassified"] == 2
    kept = out["mismatches"][0]["replay_body"]
    assert (run_dir / kept["file"]).exists() and "another path" in (run_dir / kept["file"]).read_text(encoding="utf-8") and (run_dir / out["mismatches"][0]["captured_body"]).exists()


def test_a_run_that_pre_registered_no_thresholds_states_its_rate_and_never_the_word(tmp_path, model_server, monkeypatch):
    url, _ = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch, spec_replay=None)
    out = replay_fidelity(run_dir, arm="run-server", **_kw())
    assert out["rate"] == 1.0 and out["replay_claim"] == "no_thresholds" and out["poor"] is None and "not used" in out["reading"]


def test_a_different_serving_condition_is_not_read_as_fidelity_to_the_run(tmp_path, model_server, monkeypatch):
    url, _ = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch)
    eager = tmp_path / "eager.log"
    eager.write_text(LOG.replace("enforce_eager=False", "enforce_eager=True"), encoding="utf-8")
    monkeypatch.setenv("MARK_SERVING_LOG", str(eager))
    out = replay_fidelity(run_dir, arm="enforce-eager", **_kw())
    assert out["same_condition_as_run"] is False and out["replay_claim"] == "not_a_fidelity_measurement" and out["poor"] is None


def test_the_eager_arm_compares_two_orders_with_each_other(tmp_path, model_server, monkeypatch):
    url, state = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch)
    eager = tmp_path / "eager.log"
    eager.write_text(LOG.replace("enforce_eager=False", "enforce_eager=True"), encoding="utf-8")
    monkeypatch.setenv("MARK_SERVING_LOG", str(eager))
    posts_before = state["posts"]
    same = replay_order_dependence(run_dir, arm="enforce-eager", **_kw())
    assert state["posts"] - posts_before == 6, "three orders over two scenarios"
    assert same["order_independent"] is True and same["agreed"] == 2 and same["enforce_eager_at_replay"] is True and same["same_condition_as_run"] is False
    assert "order-independent" in same["reading"] and "not proof" in same["reading"] and "fidelity to the run" in same["not_fidelity"]
    assert len(same["orders"]) == 3 and same["shuffled_order_sha256"] and same["orders_coincide"], "two scenarios cannot have three distinct orders, and the record says so"
    state["history"] = True
    shifted = replay_order_dependence(run_dir, arm="enforce-eager-history", **_kw())
    assert shifted["order_independent"] is False and shifted["agreed"] == 0 and "order-dependent" in shifted["reading"]
    d = shifted["disagreements"][0]
    assert set(d["paths"]) == {"as_captured", "reversed", "shuffled"} and d["classification_against_as_captured"] and all((run_dir / b["file"]).exists() for b in d["replay_bodies"].values())
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    assert [a["arm"] for a in manifest["environment"]["replay_order_dependence"]] == ["enforce-eager", "enforce-eager-history"]
    assert sha256_hex((run_dir / "results.json").read_bytes()) == manifest["evidence"]["results_sha256"]
    with pytest.raises(FidelityRefused, match="already exists"):
        replay_order_dependence(run_dir, arm="enforce-eager", **_kw())


def test_an_order_arm_on_a_server_not_stating_eager_says_so(tmp_path, model_server, monkeypatch):
    url, _ = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch)
    out = replay_order_dependence(run_dir, arm="not-eager", **_kw())
    assert out["enforce_eager_at_replay"] is False and "not the eager arm" in out["reading"]


def test_a_signed_bundle_is_refused(tmp_path, model_server, monkeypatch):
    url, _ = model_server
    run_dir = _bundle(tmp_path, url, monkeypatch)
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FidelityRefused, match="already signed"):
        replay_fidelity(run_dir, arm="run-server", **_kw())
    with pytest.raises(FidelityRefused, match="already signed"):
        replay_order_dependence(run_dir, arm="enforce-eager", **_kw())
