from typing import Any

from dbagent.agent.service import AgentService
from dbagent.ai.provider import LLMProvider


class FakeToolExecutor:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {"table": arguments.get("table", ""), "columns": ["id", "name"]}


class ScriptedProvider(LLMProvider):
    """Returns a scripted sequence of assistant messages, one per call."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = responses
        self._index = 0

    def chat(self, messages, tools=None) -> dict[str, Any]:
        response = self._responses[self._index]
        self._index = min(self._index + 1, len(self._responses) - 1)
        return response


def _tool_call_message(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": tool_name, "arguments": arguments}}],
    }


def _final_message(content: str) -> dict[str, Any]:
    return {"role": "assistant", "content": content}


def test_agent_calls_tool_then_returns_final_answer():
    provider = ScriptedProvider(
        [
            _tool_call_message("get_table_schema", {"table": "property"}),
            _final_message("The property table has an id and name column."),
        ]
    )
    executor = FakeToolExecutor()
    agent = AgentService(provider, executor)

    response = agent.ask("What columns does the property table have?")

    assert response.answer == "The property table has an id and name column."
    assert response.stopped_reason == "completed"
    assert len(response.steps) == 1
    assert response.steps[0].tool == "get_table_schema"
    assert executor.calls == [("get_table_schema", {"table": "property"})]


def test_agent_answers_directly_without_tools():
    provider = ScriptedProvider([_final_message("Hello there.")])
    executor = FakeToolExecutor()
    agent = AgentService(provider, executor)

    response = agent.ask("hi")

    assert response.answer == "Hello there."
    assert response.steps == []


def test_agent_stops_after_max_steps():
    provider = ScriptedProvider(
        [_tool_call_message("search_tables", {"query": "x"})]  # never finishes
    )
    executor = FakeToolExecutor()
    agent = AgentService(provider, executor, max_steps=3, max_tool_calls=100)

    response = agent.ask("infinite question")

    assert response.stopped_reason == "max_steps_exceeded"
    assert len(response.steps) == 3


def test_agent_stops_after_max_tool_calls():
    provider = ScriptedProvider(
        [_tool_call_message("search_tables", {"query": "x"})]  # never finishes
    )
    executor = FakeToolExecutor()
    agent = AgentService(provider, executor, max_steps=100, max_tool_calls=2)

    response = agent.ask("infinite question")

    assert response.stopped_reason == "max_tool_calls_exceeded"
    assert len(response.steps) == 2
