import json
import re
from pathlib import Path

from dbagent.business.metrics_model import BusinessMetric

# Strict full-string match: only ever accept an unambiguous YYYY-MM-DD
# value here. These strings get substituted directly into SQL text (the
# metric's own .sql template, not user/model input), so this is the only
# thing standing between a date argument and a syntax/injection surface.
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MetricError(Exception):
    pass


class MetricService:
    """Loads a per-database business_metric registry (Phase 26) and
    renders a metric's trusted SQL template into a runnable statement.
    Rendered SQL still goes through SqlValidator/ReadOnlyQueryService like
    any other query -- this only replaces *where the SQL comes from*, not
    the safety pipeline it runs through."""

    def __init__(self, metrics_path: str | Path | None = None):
        self._metrics: dict[str, BusinessMetric] = self._load(metrics_path)

    def _load(self, metrics_path: str | Path | None) -> dict[str, BusinessMetric]:
        if metrics_path is None:
            return {}
        path = Path(metrics_path)
        if not path.exists():
            return {}
        raw = json.loads(path.read_text())
        return {entry["name"]: BusinessMetric(**entry) for entry in raw}

    def list_metrics(self) -> list[BusinessMetric]:
        return list(self._metrics.values())

    def get_metric(self, name: str) -> BusinessMetric | None:
        return self._metrics.get(name)

    def render_sql(
        self, name: str, start_date: str | None = None, end_date: str | None = None
    ) -> str:
        metric = self._metrics.get(name)
        if metric is None:
            raise MetricError(
                f"Unknown metric '{name}'. Available: {sorted(self._metrics)}"
            )

        needs_dates = "{start_date}" in metric.sql or "{end_date}" in metric.sql
        if not needs_dates:
            return metric.sql

        if not start_date or not end_date:
            raise MetricError(
                f"Metric '{name}' requires both start_date and end_date (YYYY-MM-DD)."
            )
        for label, value in (("start_date", start_date), ("end_date", end_date)):
            if not _DATE_PATTERN.match(value):
                raise MetricError(f"Invalid {label} '{value}': must be YYYY-MM-DD.")

        return metric.sql.format(start_date=start_date, end_date=end_date)
