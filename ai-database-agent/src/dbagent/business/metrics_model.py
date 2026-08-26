from pydantic import BaseModel


class BusinessMetric(BaseModel):
    """A trusted, versioned metric definition (Phase 15/26). The point is
    that "completed orders" or "total revenue" get computed the same way
    every time, instead of the model re-deriving the business logic (and
    picking a slightly different filter/join) on every question."""

    name: str
    description: str
    sql: str
    category: str | None = None
    version: int = 1
