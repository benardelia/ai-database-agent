import json
from typing import Any

from dbagent.agent.models import AgentResponse, AgentStep
from dbagent.ai.provider import LLMProvider, LLMProviderError
from dbagent.ai.tools import AGENT_TOOLS, TOOL_NAMES, ToolExecutor

SYSTEM_PROMPT = """You are a database reasoning assistant.

You may inspect database metadata and run queries only through the provided tools:
- search_tables: find tables relevant to a business concept
- get_table_schema: inspect a table's columns, keys and relationships
- find_relationships: discover how a table connects to other tables
- get_sample_rows: see a few example values from a table's columns (e.g.
  what values a "status" column actually contains) -- optional, only use it
  if you're unsure what values to filter/compare against
- list_business_metrics: trusted, pre-defined metrics (e.g. "completed_widgets",
  "total_revenue") for this database
- compute_metric: run a metric from list_business_metrics and get the real result
- validate_sql: check a generated SQL statement before running it
- execute_readonly_sql: run a validated read-only SELECT and get real rows back

You must:
1. Understand the user's question.
2. If the question matches a standard business concept (a total, a count of
   completed/successful things, revenue, etc.), call list_business_metrics
   first. If a metric matches, call compute_metric ONCE for that single
   matching metric -- it's the trusted, consistent definition. Do not call
   compute_metric again for other metrics the user did not ask about, and
   do not also write your own SQL for the same thing afterward. Only fall
   back to writing SQL yourself if no metric matches the question.
3. Otherwise: use search_tables to identify relevant tables before assuming
   any exist.
4. Use get_table_schema to inspect real columns. Only call find_relationships
   if the question actually needs a join across more than one table -- for a
   single-table count/lookup, skip it and go straight to writing SQL.
5. Never invent tables or columns that the tools did not return.
6. Write a single read-only SELECT statement using only tables/columns you
   have actually inspected through the tools above.
7. Call validate_sql on it, and only proceed if it reports valid=true. If it
   is rejected, fix the SQL based on the error and try again.
8. Call execute_readonly_sql to run the validated SQL and get real results.
9. Base your final answer only on the actual values inside the "rows" array
   that execute_readonly_sql/compute_metric returned -- e.g. for
   `rows: [[0]]` the answer is 0. Other fields like "returned_row_count"
   describe the shape of the result (how many rows came back), not the
   answer itself -- never use them as if they were a data value. Never
   invent, estimate, or round numbers that were not in "rows".
10. If the question is ambiguous given the schema, say so and ask for
    clarification instead of guessing.
11. Never attempt INSERT/UPDATE/DELETE/DROP/ALTER or any other write -- you
    only have read access, and the tools will refuse anything else anyway.
12. Do not describe or narrate a tool call you are about to make ("Let me
    check the schema..."). Just call it. Only produce a text response when
    you are giving your real final answer or asking a clarifying question.
13. STOP as soon as a tool call's "rows" contain the answer to the actual
    question asked. Most questions need exactly one data-producing call
    (compute_metric or execute_readonly_sql). Do not keep calling more
    tools "to be thorough", do not explore metrics/tables/values the
    question didn't ask about, and do not re-verify a result you already
    have with a second, different query. Give your final text answer
    immediately once you have the number/rows you need.
"""

# Questions that clearly require data (contain a data-shaped keyword) should
# end with an execute_readonly_sql call before the agent gives up and answers
# in plain text. Small local models sometimes narrate the next step instead
# of taking it (Phase 40: error recovery) -- this bounds how many times the
# agent gets nudged to actually continue instead of silently stalling.
DATA_QUESTION_HINTS = (
    "how many",
    "how much",
    "count",
    "total",
    "average",
    "list",
    "which",
    "show",
    "top",
    "most",
    "least",
)


class AgentService:
    """Runs the full tool-calling loop (Phases 6-11): schema discovery,
    NL -> SQL generation by the model itself, sqlglot-based validation, and
    read-only execution, bounded by max_steps / max_tool_calls so a
    confused model can't loop forever (Phase 30)."""

    def __init__(
        self,
        provider: LLMProvider,
        tool_executor: ToolExecutor,
        max_steps: int = 10,
        max_tool_calls: int = 15,
        max_nudges: int = 3,
        database_context: str | None = None,
    ):
        self._provider = provider
        self._tool_executor = tool_executor
        self._max_steps = max_steps
        self._max_tool_calls = max_tool_calls
        self._max_nudges = max_nudges
        # Optional, per-database schema/domain notes (see
        # DatabaseProfile.context_path). Additive, not a replacement -- the
        # core rules above (read-only, stop conditions, metric priority,
        # etc.) still apply; this only supplements them with concrete facts
        # about *this* database, which cuts down on a small model
        # hallucinating table names (e.g. "order_records" instead of the
        # real "order_table") instead of using search_tables/get_table_schema.
        self._system_prompt = SYSTEM_PROMPT
        if database_context:
            self._system_prompt += (
                "\n\nDatabase-specific notes (verified against the real "
                "schema -- still confirm with get_table_schema if unsure, "
                "but you can trust these as a starting point):\n\n"
                + database_context
            )

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def ask(self, question: str) -> AgentResponse:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": question},
        ]
        steps: list[AgentStep] = []
        tool_calls_made = 0
        nudges_used = 0
        looks_data_driven = any(
            hint in question.lower() for hint in DATA_QUESTION_HINTS
        )
        # A prompt instruction alone ("stop once you have the answer") was
        # not reliable enough for a 3B model -- observed live continuing to
        # explore unrelated metrics/tables after already getting the
        # correct answer. Enforce it structurally instead: once a
        # data-producing call succeeds, the *next* turn omits tool
        # definitions entirely, so the model has no choice but to answer in
        # text. Multi-query questions are out of scope until Phase 17/29
        # (multi-step reasoning) anyway, so this is also the right
        # constraint for what this milestone actually supports.
        got_data_result = False
        # Guards a specific observed failure: the model guesses a metric
        # name that doesn't exist ("total_revenue"), gets an error, then
        # calls a *different*, unrelated-but-real metric that happens to
        # succeed ("completed_widgets", a count -- not what a revenue
        # question needed) and would otherwise get force-stopped into
        # confidently answering with the wrong number. After a wrong guess,
        # the *next* success gets one grace round (not force-stopped, since
        # it might be another wrong guess) -- but the one after that is
        # trusted, so a model that corrects itself still terminates rather
        # than burning its whole budget (Phase 40: correct once, not
        # unlimited retries).
        metric_name_previously_guessed_wrong = False

        for _ in range(self._max_steps):
            tools = None if got_data_result else AGENT_TOOLS
            try:
                message = self._provider.chat(messages, tools=tools)
            except LLMProviderError as exc:
                # Phase 40: LLM failure (timeout, connection refused, bad
                # response) is a recoverable-by-the-caller condition, not a
                # crash -- surface it the same way every other stop reason
                # is surfaced (a normal AgentResponse), not an exception
                # that turns into a raw 500 at the API layer.
                return AgentResponse(
                    question=question,
                    answer=f"The AI backend failed: {exc}",
                    steps=steps,
                    stopped_reason="llm_provider_error",
                )
            messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = message.get("content", "")
                nudge_reason = None

                if self._looks_like_stray_tool_call(content):
                    nudge_reason = (
                        "Call the tool using the tool-calling mechanism, not as "
                        "plain text."
                    )
                elif steps and looks_data_driven and not got_data_result:
                    nudge_reason = (
                        "Don't just describe the next step -- actually call the "
                        "appropriate tool now and continue until you have run "
                        "execute_readonly_sql and can answer with real data."
                    )

                if nudge_reason and nudges_used < self._max_nudges:
                    nudges_used += 1
                    messages.append({"role": "user", "content": nudge_reason})
                    continue

                if self._looks_like_stray_tool_call(content):
                    # Nudges exhausted and the model is still emitting a
                    # tool call as plain text -- returning that raw text as
                    # if it were the answer would present garbage to the
                    # user (observed live: a literal '{"name":
                    # "get_table_schema", ...}' string shown as the
                    # "answer"). Say plainly that it failed instead.
                    return AgentResponse(
                        question=question,
                        answer=(
                            "I was not able to produce a clear answer -- I kept "
                            "attempting to call a tool incorrectly instead of "
                            "either calling it properly or answering directly."
                        ),
                        steps=steps,
                        stopped_reason="stray_tool_call_unresolved",
                    )

                return AgentResponse(question=question, answer=content, steps=steps)

            for call in tool_calls:
                if tool_calls_made >= self._max_tool_calls:
                    return AgentResponse(
                        question=question,
                        answer=(
                            "I could not complete this analysis within the "
                            "allowed number of tool calls."
                        ),
                        steps=steps,
                        stopped_reason="max_tool_calls_exceeded",
                    )

                function = call.get("function", {})
                tool_name = function.get("name", "")
                arguments = function.get("arguments", {}) or {}

                result = self._tool_executor.execute(tool_name, arguments)
                tool_calls_made += 1

                if tool_name == "compute_metric":
                    if "error" in result and "Unknown metric" in result.get("error", ""):
                        metric_name_previously_guessed_wrong = True
                    elif "error" not in result:
                        if metric_name_previously_guessed_wrong:
                            metric_name_previously_guessed_wrong = False  # grace round used
                        else:
                            got_data_result = True
                elif tool_name == "execute_readonly_sql" and "error" not in result:
                    got_data_result = True

                result_json = json.dumps(result)
                steps.append(
                    AgentStep(
                        tool=tool_name,
                        arguments=arguments,
                        result_summary=result_json[:500],
                    )
                )
                messages.append({"role": "tool", "content": result_json})

        return AgentResponse(
            question=question,
            answer=(
                "I could not complete this analysis within the allowed "
                "number of reasoning steps."
            ),
            steps=steps,
            stopped_reason="max_steps_exceeded",
        )

    @staticmethod
    def _looks_like_stray_tool_call(content: str) -> bool:
        """Small local models occasionally emit a tool call as JSON-ish
        plain text instead of using the structured tool_calls field. Detect
        that so the agent can ask for a corrected, properly-formatted call
        once rather than treating the raw text as the final answer."""
        if not content:
            return False
        return any(name in content for name in TOOL_NAMES)
