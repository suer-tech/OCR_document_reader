from __future__ import annotations

from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

from ocr_platform.api.main import app


def test_prometheus_payload_contains_ocr_metric_definitions() -> None:
    payload = generate_latest().decode("utf-8")

    assert "# HELP ocr_requests_total" in payload
    assert "# HELP ocr_pipeline_latency_seconds" in payload
    assert "# HELP ocr_quality_score" in payload


def test_prometheus_content_type_is_exposed_without_openapi_entry() -> None:
    client = TestClient(app)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


def test_http_requests_are_instrumented_by_route_and_status() -> None:
    client = TestClient(app)
    labels = {"method": "GET", "route": "/health", "status_code": "200"}
    before = REGISTRY.get_sample_value("ocr_http_requests_total", labels) or 0

    response = client.get("/health")

    assert response.status_code == 200
    assert REGISTRY.get_sample_value("ocr_http_requests_total", labels) == before + 1
    assert REGISTRY.get_sample_value(
        "ocr_http_request_duration_seconds_count",
        {"method": "GET", "route": "/health"},
    )
    assert REGISTRY.get_sample_value(
        "ocr_http_requests_in_progress", {"method": "GET"}
    ) == 0


def test_metrics_scrapes_are_not_counted_as_api_traffic() -> None:
    client = TestClient(app)
    labels = {"method": "GET", "route": "/metrics", "status_code": "200"}
    before = REGISTRY.get_sample_value("ocr_http_requests_total", labels)

    assert client.get("/metrics").status_code == 200

    assert REGISTRY.get_sample_value("ocr_http_requests_total", labels) == before
