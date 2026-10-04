"""Target adapters. Each module exposes `build(handle, cfg) -> Target` where Target has:
  run(workload: dict) -> dict          perform the workload; returns an outcome record
  resume() -> dict                     continue after a halt (framework-native controls)
  inject(instruction: str) -> dict     the harness's attempt to make the agent act after a halt
  describe() -> dict
cfg carries: mock_url, llm_url, llm_model, workdir, scenario_id.
"""
from __future__ import annotations

from typing import Any, Protocol


class Target(Protocol):
    id: str

    def run(self, workload: dict[str, Any]) -> dict[str, Any]: ...

    def resume(self) -> dict[str, Any]: ...

    def inject(self, instruction: str) -> dict[str, Any]: ...

    def describe(self) -> dict[str, Any]: ...
