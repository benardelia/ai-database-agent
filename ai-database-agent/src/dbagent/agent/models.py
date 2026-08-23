from typing import Any

from pydantic import BaseModel


class AgentStep(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result_summary: str


class AgentResponse(BaseModel):
    question: str
    answer: str
    steps: list[AgentStep] = []
    stopped_reason: str = "completed"
