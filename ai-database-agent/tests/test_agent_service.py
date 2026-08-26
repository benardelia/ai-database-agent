from typing import Any

from dbagent.agent.service import SYSTEM_PROMPT, AgentService
from dbagent.ai.provider import LLMProvider, LLMProviderError


class FakeToolExecutor:
    """`results` maps a tool name to either a single canned dict (returned
    every time) or a list of dicts (returned in order, one per call, last
    one repeats after exhausted) -- the list form lets a test simulate a
    tool that fails once then succeeds."""

    def __init__(self, results: dict[str, Any] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._results = results or {}
        self._call_counts: dict[str, int] = {}

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        if tool_name not in self._results:
            return {"table": arguments.get("table", ""), "columns": ["id", "name"]}

        configured = self._results[tool_name]
        if isinstance(configured, list):
            index = self._call_counts.get(tool_name, 0)
            self._call_counts[tool_name] = index + 1
            return configured[min(index, len(configured) - 1)]
        return configured


class ScriptedProvider(LLMProvider):
    """Returns a scripted sequence of assistant messages, one per call, and
    records the `tools`/`messages` arguments it was called with each time."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = responses
        self._index = 0
        self.tools_seen: list[Any] = []
        self.messages_seen: list[list[dict[str, Any]]] = []

    def chat(self, messages, tools=None) -> dict[str, Any]:
        self.tools_seen.append(tools)
        self.messages_seen.append(messages)
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


def test_tools_are_withheld_after_successful_data_result():
    """Regression test: observed live with llama3.2 that after a
    successful compute_metric/execute_readonly_sql call, the model would
    keep calling more (unrelated) tools instead of answering, eventually
    exhausting max_steps despite already having the right answer. A prompt
    instruction alone wasn't reliable, so this is enforced structurally --
    once a data-producing call succeeds, the next provider.chat call must
    receive tools=None, leaving the model no way to keep calling tools."""
    provider = ScriptedProvider(
        [
            _tool_call_message("compute_metric", {"name": "completed_widgets"}),
            _final_message("There are 7 completed widgets."),
        ]
    )
    executor = FakeToolExecutor(results={"compute_metric": {"rows": [[7]], "metric": "completed_widgets"}})
    agent = AgentService(provider, executor)

    response = agent.ask("how many completed orders are there?")

    assert response.answer == "There are 7 completed widgets."
    assert response.stopped_reason == "completed"
    assert provider.tools_seen[0] is not None  # first call offered tools
    assert provider.tools_seen[1] is None  # forced no-tools after success


def test_metric_success_immediately_after_wrong_metric_guess_gets_one_grace_round():
    """Regression test: observed live with llama3.2 asking for revenue --
    it guessed a nonexistent metric name ('total_revenue'), got an
    'Unknown metric' error, then called a *different*, unrelated metric
    ('completed_widgets', a count) that succeeded, and confidently answered
    "7" (an unrelated metric) to a revenue question. The very next success after a name-guessing
    failure must not force-stop -- it gets one grace round instead of
    being trusted immediately."""
    provider = ScriptedProvider(
        [
            _tool_call_message("compute_metric", {"name": "total_revenue"}),
            _tool_call_message("compute_metric", {"name": "completed_widgets"}),
            _final_message("Still investigating."),
        ]
    )
    executor = FakeToolExecutor(
        results={
            "compute_metric": [
                {"error": "Unknown metric 'total_revenue'. Available: [...]"},
                {"rows": [[7]], "metric": "completed_widgets"},
            ]
        }
    )
    agent = AgentService(provider, executor)

    agent.ask("what is our total revenue?")

    # tools_seen[2] is the call *after* the grace-round success -- must
    # still be offered, not forced to a text-only answer.
    assert provider.tools_seen[2] is not None


def test_metric_success_two_calls_after_wrong_guess_is_trusted():
    """The grace round is spent on the call right after a bad guess; the
    success *after that* (i.e. once the model has had a chance to correct
    itself) should be trusted and force-stop normally -- otherwise a model
    that DOES self-correct would still burn its whole step budget for
    nothing, which is exactly what was observed live before this fix."""
    provider = ScriptedProvider(
        [
            _tool_call_message("compute_metric", {"name": "total_revenue"}),
            _tool_call_message("compute_metric", {"name": "completed_widgets"}),
            _tool_call_message("compute_metric", {"name": "completed_order_total"}),
            _final_message("Total revenue from completed orders is 58210.75."),
        ]
    )
    executor = FakeToolExecutor(
        results={
            "compute_metric": [
                {"error": "Unknown metric 'total_revenue'. Available: [...]"},
                {"rows": [[7]], "metric": "completed_widgets"},
                {"rows": [["58210.75"]], "metric": "completed_order_total"},
            ]
        }
    )
    agent = AgentService(provider, executor)

    response = agent.ask("what is our total revenue?")

    assert response.answer == "Total revenue from completed orders is 58210.75."
    assert provider.tools_seen[3] is None  # forced no-tools after the trusted success

    # tools_seen: [call0 offered, call1 offered, call2 -- must STILL be
    # offered because the success followed a wrong-name guess]
    assert provider.tools_seen[2] is not None


def test_exhausted_nudges_on_stray_tool_call_returns_clear_failure_not_garbage():
    """Regression test: observed live with llama3.2 -- after nudges ran
    out, the raw stray-tool-call-as-text content (e.g. a literal
    '{"name": "get_table_schema", ...}' string) was returned as if it were
    the final answer. That must never reach the user as an "answer";
    return an honest failure instead."""
    stray_message = _final_message('{"name": "get_table_schema", "parameters": {"table": "x"}}')
    provider = ScriptedProvider([stray_message])  # repeats forever, ignores tools param
    executor = FakeToolExecutor()
    agent = AgentService(provider, executor, max_steps=100, max_nudges=2)

    response = agent.ask("something")

    assert response.stopped_reason == "stray_tool_call_unresolved"
    assert "get_table_schema" not in response.answer
    assert '{"name"' not in response.answer


def test_tools_remain_available_after_a_failed_data_call():
    """Only a *successful* data result should withhold tools on the next
    turn -- an error must leave tools available so the model can retry
    with a corrected call (Phase 40: self-correction)."""
    provider = ScriptedProvider(
        [
            _tool_call_message("execute_readonly_sql", {"sql": "SELECT bad syntax"}),
            _final_message("Done."),
        ]
    )
    executor = FakeToolExecutor(results={"execute_readonly_sql": {"error": "syntax error"}})
    agent = AgentService(provider, executor)

    agent.ask("how many rows in x?")

    assert provider.tools_seen[0] is not None
    assert provider.tools_seen[1] is not None  # still offered after the failed call


class FailingProvider(LLMProvider):
    """Always raises LLMProviderError, simulating an Ollama timeout /
    connection failure / bad response."""

    def __init__(self, error_message: str = "Ollama did not respond within 180s."):
        self._error_message = error_message

    def chat(self, messages, tools=None) -> dict[str, Any]:
        raise LLMProviderError(self._error_message)


def test_llm_provider_error_returns_clean_response_not_exception():
    """Regression test: a raw httpx.ReadTimeout from Ollama was propagating
    all the way through to a 500 Internal Server Error with a stack trace
    at the API layer (POST /api/ai/query). Phase 40 says LLM failure is a
    recoverable condition the agent must handle -- this must come back as
    a normal AgentResponse, not raise, so callers never see an unhandled
    exception."""
    provider = FailingProvider("Ollama did not respond within 180s.")
    executor = FakeToolExecutor()
    agent = AgentService(provider, executor)

    response = agent.ask("how many districts are there?")

    assert response.stopped_reason == "llm_provider_error"
    assert "Ollama did not respond within 180s." in response.answer
    assert response.steps == []


def test_database_context_is_appended_to_system_prompt():
    provider = ScriptedProvider([_final_message("hi")])
    executor = FakeToolExecutor()
    agent = AgentService(
        provider,
        executor,
        database_context="There is no table called order_records -- orders are order_table.",
    )

    assert "order_records" in agent.system_prompt
    assert "order_table" in agent.system_prompt

    agent.ask("something")

    # The composed prompt (not just the base SYSTEM_PROMPT) must be what
    # actually gets sent to the provider as the system message.
    sent_system_message = provider.messages_seen[0][0]
    assert sent_system_message["role"] == "system"
    assert "order_records" in sent_system_message["content"]


def test_without_database_context_prompt_is_unchanged():
    provider = ScriptedProvider([_final_message("hi")])
    executor = FakeToolExecutor()
    agent = AgentService(provider, executor)

    assert agent.system_prompt == SYSTEM_PROMPT
