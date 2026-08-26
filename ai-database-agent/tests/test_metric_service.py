from pathlib import Path

import pytest

from dbagent.business.metric_service import MetricError, MetricService

METRICS_PATH = Path(__file__).resolve().parents[1] / "src/dbagent/business/metrics.json"


@pytest.fixture
def service() -> MetricService:
    return MetricService(METRICS_PATH)


def test_list_metrics_loads_all_entries(service: MetricService):
    names = {m.name for m in service.list_metrics()}
    assert names == {
        "completed_widgets",
        "completed_order_total",
        "payments_total",
        "active_item_count",
        "payments_in_period",
    }


def test_get_metric_returns_definition(service: MetricService):
    metric = service.get_metric("completed_widgets")
    assert metric is not None
    assert "Completed" in metric.sql


def test_get_unknown_metric_returns_none(service: MetricService):
    assert service.get_metric("does_not_exist") is None


def test_render_sql_without_date_placeholders(service: MetricService):
    sql = service.render_sql("completed_widgets")
    assert sql == service.get_metric("completed_widgets").sql


def test_render_sql_unknown_metric_raises(service: MetricService):
    with pytest.raises(MetricError, match="Unknown metric"):
        service.render_sql("does_not_exist")


def test_render_sql_with_valid_dates_substitutes_them(service: MetricService):
    sql = service.render_sql(
        "payments_in_period", start_date="2026-04-01", end_date="2026-05-01"
    )
    assert "2026-04-01" in sql
    assert "2026-05-01" in sql
    assert "{start_date}" not in sql


def test_render_sql_missing_dates_raises(service: MetricService):
    with pytest.raises(MetricError, match="requires both start_date and end_date"):
        service.render_sql("payments_in_period")


@pytest.mark.parametrize("bad_date", ["2026/04/01", "not-a-date", "2026-04-01; DROP TABLE x", "'; --"])
def test_render_sql_rejects_malformed_dates(service: MetricService, bad_date: str):
    with pytest.raises(MetricError, match="must be YYYY-MM-DD"):
        service.render_sql(
            "payments_in_period", start_date=bad_date, end_date="2026-05-01"
        )


def test_missing_metrics_file_yields_empty_registry():
    service = MetricService("does/not/exist.json")
    assert service.list_metrics() == []
