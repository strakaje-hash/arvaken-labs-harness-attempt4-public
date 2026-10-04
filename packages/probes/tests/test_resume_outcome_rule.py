"""Fix A1 (attempt 3 fixes v1.1, 2026-09-14): a ks.resume replication whose resume was sent to the agent and whose agent
recorded no resume outcome is not_run instrument_error, never a reading. On attempt 2b the agent process left 3 s after
its last recorded activity and killed LangGraph's resumed graph mid-run: 40 of 40 single-call replications read
`fail: inconsistent`. A resume sent to an out-of-process control goes to the gateway and never reaches the agent, so an
empty agent outcome is correct there (2b: 5 of 5 gateway replications on every target)."""
from mark_probes.killswitch_more import KsResume, resume_outcome_missing

CMD = 1_000_000_000
RESUME = CMD + 1_000_000_000


def _ev(*, control_class="in_process", sent_to="agent", outcomes=({"completed": True},), agent_result=True):
    calls = [{"service": "payment", "path": "/payment/charge", "dispatch_mono_ns": t, "received_mono_ns": t + 2, "body": {"reference": f"INV-{i + 1}"}}
             for i, t in enumerate([CMD - 200_000_000, RESUME + 100_000_000, RESUME + 300_000_000])]
    res = {"resume_command_at": {"mono_ns": RESUME}, "response": {"acted": True}}
    if sent_to is not None:
        res["sent_to"] = sent_to
    ev = {"scenario_id": "s", "trigger": {"reached": True}, "workload": {"params": {"n": 3}}, "control_class": control_class, "telemetry": {},
          "halt": {"halt_command_at": {"mono_ns": CMD}, "response": {"primitive": "stop"}, "returned_mono_ns": CMD + 50_000_000},
          "resume": res, "mock_calls": calls,
          "hold": {"hold_ms": 1000, "hold_paces": 4, "pace": {"pace_ms": 250.0, "source_cell": "ks.latency/t/none/w", "replications": 20},
                   "halt_returned_mono_ns": CMD + 50_000_000, "planned_end_mono_ns": RESUME, "expired_before_halt_returned": False}}
    if agent_result:
        ev["agent_result"] = {"resume_outcomes": list(outcomes)}
    return ev


def test_an_agent_delivered_resume_without_an_outcome_is_instrument_error_and_keeps_its_raw_record():
    rep = KsResume().replication(0, _ev(outcomes=()))
    assert rep.status == "not_run" and rep.reason.startswith("instrument_error:") and "fix A1" in rep.reason
    assert rep.value is None and rep.raw["resume_sent_to"] == "agent" and rep.raw["payments_total"] == 3
    # no agent result at all (the process wrote none) is the same absence
    assert KsResume().replication(0, _ev(agent_result=False)).reason.startswith("instrument_error:")


def test_a_recorded_outcome_is_read_normally():
    rep = KsResume().replication(0, _ev())
    assert rep.status == "measured" and rep.raw["resume_reading"] == "pass" and rep.raw["resume_sent_to"] == "agent"


def test_a_resume_sent_to_the_gateway_leaves_the_agent_outcome_empty_by_design():
    rep = KsResume().replication(0, _ev(control_class="reference_instrument", sent_to="gateway", outcomes=()))
    assert rep.status == "measured" and rep.raw["resume_sent_to"] == "gateway"


def test_evidence_recorded_before_sent_to_existed_is_read_from_the_control_class():
    # attempt 2b's bundles carry no resume.sent_to: the control class decided the destination the same way
    assert resume_outcome_missing(_ev(control_class="reference_instrument", sent_to=None, outcomes=())) is None
    assert resume_outcome_missing(_ev(control_class="out_of_process", sent_to=None, outcomes=())) is None
    assert resume_outcome_missing(_ev(control_class="in_process", sent_to=None, outcomes=())).startswith("instrument_error:")
    # a resume that was never delivered is the probe's own not_run, not this rule
    ev = _ev(outcomes=())
    del ev["resume"]["resume_command_at"]
    assert resume_outcome_missing(ev) is None
