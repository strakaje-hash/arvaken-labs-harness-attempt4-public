"""What a replication is on a model-driven target (founder ruling 2026-09-12, after the Q1 and Q3 replays).

The per-replication scenario id is in the agent's prompt (the working path in the tool description), and swapping only
that string between two captured first requests flipped the model's reply both ways, 3 of 3, in every cell tried. So on
a model-driven target the variation between replications was variation in the prompt's id string driving the model
down different token paths: realistic (deployments carry varying paths, ids and timestamps), but unstated.

It is now declared and counted, never pinned away:
- the workload declares `variation: scenario_id`;
- every replication records its first request's prompt hash and its first reply's path hash;
- every cell reports the number of distinct first-reply paths among its measured replications, and a cell whose
  replications all share one path is flagged as effectively one sample of agent behaviour;
- `min_replications` is read against distinct paths, informationally: the signed gates count replications.

An unparsed reply stays not_run model_error per replication: the slip is a model property at a prompt, not a control
result."""
from __future__ import annotations

from typing import Any

HASH_RULE = ("SHA-256 over canonical JSON of the first reply: its content, the tool-call names, the tool-call arguments parsed as JSON and "
             "re-serialised with sorted keys, and the finish reason. Whitespace-only differences in tool-call arguments merge into one path, "
             "which undercounts distinct paths: the conservative direction for the single-path flag.")


def first_reply(calls: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The first model call of a replication, by proxy sequence."""
    if not calls:
        return None
    c = min(calls, key=lambda c: c.get("seq") or 0)
    return {"seq": c.get("seq"), "prompt_sha256": c.get("prompt_sha256"), "reply_path_sha256": c.get("reply_path_sha256"), "http_status": c.get("http_status")}


def cell_reply_paths(per_replication: list[dict[str, Any]], *, model_driven: bool, variation: str | None, min_replications: int | None) -> dict[str, Any]:
    measured = [r for r in per_replication if r.get("status") == "measured"]
    firsts = [((r.get("raw") or {}).get("model") or {}).get("first_reply") or {} for r in measured]
    paths = [f.get("reply_path_sha256") for f in firsts if f.get("reply_path_sha256")]
    prompts = {f.get("prompt_sha256") for f in firsts if f.get("prompt_sha256")}
    out: dict[str, Any] = {"model_driven": model_driven, "variation": variation, "hash_rule": HASH_RULE, "measured": len(measured),
                           "measured_with_first_reply": len(paths), "min_replications": min_replications}
    if not model_driven:
        return {**out, "distinct_first_reply_paths": None, "distinct_prompts": None, "single_path": False,
                "reading": "not model-driven: no model output varies between replications"}
    distinct = len(set(paths))
    single = len(paths) >= 2 and distinct == 1
    against = None if min_replications is None else ("met" if distinct >= min_replications else "not met")
    reading = f"{len(measured)} measured replication(s), {distinct} distinct first-reply path(s)"
    if len(paths) < len(measured):
        reading += f" ({len(measured) - len(paths)} measured without a recorded first reply)"
    if single:
        reading += "; effectively one sample of agent behaviour"
    if against is not None:
        reading += f"; min_replications {min_replications} read against distinct paths: {against} (informational: the signed gates count replications)"
    return {**out, "distinct_first_reply_paths": distinct, "distinct_prompts": len(prompts), "single_path": single,
            "min_replications_against_distinct_paths": against, "reading": reading}
