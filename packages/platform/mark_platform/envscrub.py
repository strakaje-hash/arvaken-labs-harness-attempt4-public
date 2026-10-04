"""What an agent or test process may inherit from the process that started it (incident 2026-09-12, see
benchmarks/runs/decisive-2-20260912/HISTORY.md).

A parent's environment is not the harness's to pass on. On the OpenHands pod of decisive attempt 2 the shell that
started the chain had sourced Runpod's injected RUNPOD_API_KEY before setup.sh removed it from /etc/rp_environment;
the env test found the key in the environment the runner inherited and printed its value into a log. The rule is
structural: a child environment starts from `scrubbed(parent)`, which drops the host's identifiers (RUNPOD_*) and
every credential-shaped name, and the harness then adds back, by name, only what it issued itself (the mock world's
token for an in-process control). Every check reports NAMES, never values."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

HOST_PREFIXES = ("RUNPOD_",)
CREDENTIAL_SHAPED = re.compile(r"(API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|CREDENTIAL|ACCESS_KEY)", re.IGNORECASE)


def forbidden_names(env: Mapping[str, str] | Iterable[str], *, allow: Iterable[str] = ()) -> list[str]:
    """The names in `env` that are a host identifier or credential-shaped, minus the explicit `allow` list."""
    allowed = set(allow)
    return sorted(k for k in env if k not in allowed and (k.upper().startswith(HOST_PREFIXES) or CREDENTIAL_SHAPED.search(k)))


def scrubbed(env: Mapping[str, str]) -> dict[str, str]:
    bad = set(forbidden_names(env))
    return {k: v for k, v in env.items() if k not in bad}
