"""mark-probes: versioned, signed-gate probe families."""
from .base import HaltPlan, Probe, Replication
from .gate import Gate, Verdict, decide, gate_body, load_gate
from .evidence_claims import PROBES as EVIDENCE_PROBES
from .gate_bypass import PROBES as GATE_PROBES
from .self_modification import PROBES as SELFMOD_PROBES
from .killswitch import PROBES as KILLSWITCH_PROBES
from .scope import PROBES as SCOPE_PROBES

PROBES: dict[str, type[Probe]] = {**KILLSWITCH_PROBES, **SCOPE_PROBES, **GATE_PROBES, **EVIDENCE_PROBES, **SELFMOD_PROBES}

__all__ = ["HaltPlan", "Probe", "Replication", "Gate", "Verdict", "decide", "gate_body", "load_gate", "PROBES"]
