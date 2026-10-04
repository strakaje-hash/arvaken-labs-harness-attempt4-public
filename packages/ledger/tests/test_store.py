"""The ledger is append-only and hash-chained; every tamper `verify` can see, it must report."""
import json

from mark_ledger.store import GENESIS, Ledger, Provenance

PROV = Provenance(engine_version="0.1.0", environment_fingerprint="test", actor="pytest", probe_id="ks.latency", probe_version="1")


def _fill(tmp_path, n=3):
    led = Ledger(tmp_path / "ledger")
    recs = [led.append("run-1", "probe_result", {"i": i, "value": None if i == 1 else i * 0.5, "not_run": i == 1}, PROV) for i in range(n)]
    return led, recs


def test_chain_links_and_verifies(tmp_path):
    led, recs = _fill(tmp_path)
    assert recs[0].prev_hash == GENESIS
    assert recs[1].prev_hash == recs[0].hash and recs[2].prev_hash == recs[1].hash
    assert [r.seq for r in recs] == [0, 1, 2]
    v = led.verify("run-1")
    assert v.ok and v.records == 3 and v.chain_root == recs[2].hash and v.problems == []
    assert json.loads(led.get_object(recs[1].content_hash)) == {"i": 1, "not_run": True, "value": None}
    assert led.list_chains() == ["run-1"]


def test_empty_chain_does_not_verify(tmp_path):
    led = Ledger(tmp_path / "ledger")
    assert led.verify("nothing").ok is False


def _edit_line(led, chain, seq, mutate):
    p = led._chain_path(chain)
    lines = p.read_text(encoding="utf-8").splitlines()
    d = json.loads(lines[seq])
    mutate(d)
    lines[seq] = json.dumps(d, separators=(",", ":"), sort_keys=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_tamper_with_a_record_field_is_detected(tmp_path):
    led, _ = _fill(tmp_path)
    _edit_line(led, "run-1", 1, lambda d: d.__setitem__("kind", "probe_result_edited"))
    v = led.verify("run-1")
    assert not v.ok and any("seq 1: hash does not match" in p for p in v.problems)


def test_tamper_with_a_payload_is_detected(tmp_path):
    led, recs = _fill(tmp_path)
    led._object_path(recs[2].content_hash).write_bytes(b'{"i":2,"not_run":false,"value":9.9}')
    v = led.verify("run-1")
    assert not v.ok and any("seq 2: payload bytes do not match" in p for p in v.problems)


def test_deleting_a_record_breaks_the_chain(tmp_path):
    led, _ = _fill(tmp_path)
    p = led._chain_path("run-1")
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    v = led.verify("run-1")
    assert not v.ok and any("expected seq 1" in p for p in v.problems) and any("prev_hash" in p for p in v.problems)


def test_rehash_after_edit_still_breaks_the_next_link(tmp_path):
    """An attacker who edits a record AND recomputes its hash still breaks the link from the next record."""
    led, _ = _fill(tmp_path)
    from mark_ledger.store import EvidenceRecord

    def mutate(d):
        d["kind"] = "edited"
        d["hash"] = EvidenceRecord.from_json(d).compute_hash()

    _edit_line(led, "run-1", 0, mutate)
    v = led.verify("run-1")
    assert not v.ok and any("seq 1: prev_hash" in p for p in v.problems)


def test_missing_payload_is_detected(tmp_path):
    led, recs = _fill(tmp_path)
    led._object_path(recs[0].content_hash).unlink()
    v = led.verify("run-1")
    assert not v.ok and any("payload" in p and "missing" in p for p in v.problems)


def test_anchor_must_name_a_real_head(tmp_path):
    led, recs = _fill(tmp_path)
    led.record_anchor({"chain_id": "run-1", "chain_root": "f" * 64, "anchored_at": "x", "authority": "local-only", "anchored": False, "receipt": {}})
    v = led.verify("run-1")
    assert not v.ok and any("never a head" in p for p in v.problems)
    led2, recs2 = _fill(tmp_path / "b")
    led2.record_anchor({"chain_id": "run-1", "chain_root": recs2[1].hash, "anchored_at": "x", "authority": "local-only", "anchored": False, "receipt": {}})
    v2 = led2.verify("run-1")
    assert v2.ok and v2.anchors[0]["anchored"] is False
