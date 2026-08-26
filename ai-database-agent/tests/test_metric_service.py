import json

import pytest

from dbagent.business.metric_service import MetricError, MetricService


@pytest.fixture
def metrics_path(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "completed_widgets",
                    "description": "Count of widgets whose status is Completed.",
                    "sql": "SELECT COUNT(*) AS completed_widgets FROM widget WHERE status = 'Completed'",
                    "category": "widgets",
                    "version": 1,
                },
                {
                    "name": "completed_order_total",
                    "description": "Sum of price across widgets whose status is Completed.",
                    "sql": "SELECT SUM(price) AS completed_order_total FROM widget WHERE status = 'Completed'",
                    "category": "widgets",
                    "version": 1,
                },
                {
                    "name": "payments_total",
                    "description": "Sum of amount across payments whose status is Success.",
                    "sql": "SELECT SUM(amount) AS payments_total FROM payment WHERE status = 'Success'",
                    "category": "payments",
                    "version": 1,
                },
                {
                    "name": "active_item_count",
                    "description": "Count of widgets.",
                    "sql": "SELECT COUNT(*) AS active_item_count FROM widget",
                    "category": "widgets",
                    "version": 1,
                },
                {
                    "name": "payments_in_period",
                    "description": "Sum of amount across successful payments with paid_at in [start_date, end_date).",
                    "sql": (
                        "SELECT SUM(amount) AS payments_in_period FROM payment "
                        "WHERE status = 'Success' AND paid_at >= '{start_date}' "
                        "AND paid_at < '{end_date}'"
                    ),
                    "category": "payments",
                    "version": 1,
                },
            ]
        )
    )
    return path


@pytest.fixture
def service(metrics_path) -> MetricService:
    return MetricService(metrics_path)


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
