"""No model calls (founder ruling 2026-09-12). A model-driven agent that made zero model calls through the proxy did not
operate under the probe's conditions, whatever the cause. The first pod env-test after the model proxy had two agents
denied their model by the egress allowlist, doing nothing and reporting ok. That outcome is now impossible, not
invisible: every replication of a model-driven target is checked, and the egress log names the cause when it can."""
from mark_platform import serving
from mark_platform.model_integrity import no_model_calls_reason
from mark_platform.runner import calibrate, close_run, open_run, run_cell

SERVER = ("127.0.0.1", 8000)


def test_each_cause_is_named_and_the_invariant_fires_either_way():
    denied = [{"decision": "deny", "host": "127.0.0.1", "port": 8000}, {"decision": "deny", "host": "raw.githubusercontent.com", "port": 443}]
    r = no_model_calls_reason(True, [], denied, SERVER)
    assert r["reason"].startswith("no_model_calls: model_unreachable") and r["sub_reason"] == "model_unreachable" and r["egress_denials_of_model_server"] == 1
    r = no_model_calls_reason(True, [], [{"host": "example.com", "port": 80}], SERVER)
    assert r["reason"].startswith("no_model_calls: cause not identified") and r["sub_reason"] == "unidentified"
    r = no_model_calls_reason(True, None, [], SERVER)
    assert r["sub_reason"] == "proxy_absent"
    assert no_model_calls_reason(True, [{"http_status": 200}], denied, SERVER) is None
    assert no_model_calls_reason(False, [], denied, SERVER) is None, "a target that does not use the model is never checked"


def test_every_replication_of_a_model_driven_target_without_model_calls_is_not_run(tmp_path, monkeypatch):
    """The scripted agent never calls the model; declared model-driven for this test, every replication must fire."""
    monkeypatch.setitem(serving.REQUEST_PARAMS, "scripted", {"temperature": 0, "seed": 7})
    ctx = open_run(tmp_path / "run", "no-model-calls", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        res = run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 2)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    reps = res["per_replication"]
    assert len(reps) == 2 and all(p["status"] == "not_run" and p["reason"].startswith("no_model_calls:") for p in reps), [p["reason"] for p in reps]
    assert all(p["raw"]["no_model_calls"]["sub_reason"] == "unidentified" and p["raw"]["model"]["calls"] == 0 for p in reps)
    assert not res["verdict"]["decisive"]
