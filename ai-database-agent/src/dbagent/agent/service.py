import json
from typing import Any

from dbagent.agent.models import AgentResponse, AgentStep
from dbagent.ai.provider import LLMProvider
from dbagent.ai.tools import AGENT_TOOLS, TOOL_NAMES, ToolExecutor

SYSTEM_PROMPT = """You are a database reasoning assistant.

You may inspect database metadata and run queries only through the provided tools:
- search_tables: find tables relevant to a business concept
- get_table_schema: inspect a table's columns, keys and relationships
- find_relationships: discover how a table connects to other tables
- validate_sql: check a generated SQL statement before running it
- execute_readonly_sql: run a validated read-only SELECT and get real rows back

You must:
1. Understand the user's question.
2. Use search_tables to identify relevant tables before assuming any exist.
3. Use get_table_schema to inspect real columns. Only call find_relationships
   if the question actually needs a join across more than one table -- for a
   single-table count/lookup, skip it and go straight to writing SQL.
4. Never invent tables or columns that the tools did not return.
5. Write a single read-only SELECT statement using only tables/columns you
   have actually inspected through the tools above.
6. Call validate_sql on it, and only proceed if it reports valid=true. If it
   is rejected, fix the SQL based on the error and try again.
7. Call execute_readonly_sql to run the validated SQL and get real results.
8. Base your final answer only on the actual values inside the "rows" array
   that execute_readonly_sql returned -- e.g. for `rows: [[0]]` the answer
   is 0. Other fields like "returned_row_count" describe the shape of the
   result (how many rows came back), not the answer itself -- never use
   them as if they were a data value. Never invent, estimate, or round
   numbers that were not in "rows".
9. If the question is ambiguous given the schema, say so and ask for
   clarification instead of guessing.
10. Never attempt INSERT/UPDATE/DELETE/DROP/ALTER or any other write -- you
    only have read access, and the tools will refuse anything else anyway.
11. Do not describe or narrate a tool call you are about to make ("Let me
    check the schema..."). Just call it. Only produce a text response when
    you are giving your real final answer or asking a clarifying question.
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
    ):
        self._provider = provider
        self._tool_executor = tool_executor
        self._max_steps = max_steps
        self._max_tool_calls = max_tool_calls
        self._max_nudges = max_nudges

    def ask(self, question: str) -> AgentResponse:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        steps: list[AgentStep] = []
        tool_calls_made = 0
        nudges_used = 0
        looks_data_driven = any(
            hint in question.lower() for hint in DATA_QUESTION_HINTS
        )

        for _ in range(self._max_steps):
            message = self._provider.chat(messages, tools=AGENT_TOOLS)
            messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = message.get("content", "")
                executed_sql = any(s.tool == "execute_readonly_sql" for s in steps)
                nudge_reason = None

                if self._looks_like_stray_tool_call(content):
                    nudge_reason = (
                        "Call the tool using the tool-calling mechanism, not as "
                        "plain text."
                    )
                elif steps and looks_data_driven and not executed_sql:
                    nudge_reason = (
                        "Don't just describe the next step -- actually call the "
                        "appropriate tool now and continue until you have run "
                        "execute_readonly_sql and can answer with real data."
                    )

                if nudge_reason and nudges_used < self._max_nudges:
                    nudges_used += 1
                    messages.append({"role": "user", "content": nudge_reason})
                    continue

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
