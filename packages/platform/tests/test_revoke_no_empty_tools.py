"""Fix A8 (attempt 3 fixes v1.1, 2026-09-14). LangGraph's revoke rebound the model with `bind_tools([])`, which sends
`tools: []`; vLLM answers 400 ("`tools` must not be an empty array"), and ref-revoke read model_error in 5 of 5 on every
LangGraph probe of attempt 2b, an instrument-caused not_run. After a revoke the model is the unbound one, so the request
carries no tools field. The request-shape half runs against a real client on the pod (tests/env/test_revoke_request.py):
langchain-openai has no win-arm64 wheel."""
from mark_platform.targets.langgraph_ref import LangGraphRefAgent


class _FakeLLM:
    def __init__(self):
        self.bound = []

    def bind_tools(self, tools):
        self.bound.append(list(tools))
        return ("bound", tuple(tools))


def _agent(mcp: bool):
    agent = object.__new__(LangGraphRefAgent)   # no model server, no MCP subprocess: only the revoke path is under test
    agent.llm = _FakeLLM()
    agent.tools = ["pay", "send_mail"]
    agent.model = agent.llm.bind_tools(agent.tools)
    if mcp:
        agent._mcp_client = object()
    return agent


def test_a_revoke_leaves_the_unbound_model_and_never_binds_an_empty_tool_list():
    agent = _agent(mcp=True)
    agent._revoke()
    assert agent.tools == [] and agent.model is agent.llm
    assert agent.llm.bound == [["pay", "send_mail"]], "bind_tools([]) must never be called: it sends tools: [] and vLLM answers 400"


def test_in_process_tools_revoke_themselves_and_the_model_is_untouched():
    agent = _agent(mcp=False)
    before = agent.model
    agent._revoke()
    assert agent.model is before and agent.tools == ["pay", "send_mail"]
