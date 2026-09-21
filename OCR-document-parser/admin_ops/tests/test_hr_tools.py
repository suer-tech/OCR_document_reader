import asyncio
import json

import httpx

from admin_ops.alerts import HrAlertTracker
from admin_ops.config import OpsSettings
from admin_ops import hr_tools


def test_hr_tools_require_https_fixed_boundary_and_reject_arbitrary_arguments():
    assert not hr_tools.configured(OpsSettings(hr_base_url="http://hr.invalid/api/ops", hr_read_token="x" * 40))
    assert not hr_tools.configured(OpsSettings(hr_base_url="https://user:pass@hr.invalid/api/ops", hr_read_token="x" * 40))
    settings = OpsSettings(hr_base_url="https://hr.example/api/ops", hr_read_token="x" * 40)
    assert hr_tools.configured(settings)
    model = hr_tools.HR_TOOL_MODELS["hr_metric_history"]
    assert model.model_validate({"metric": "queue_ready", "hours": 24}).metric == "queue_ready"
    for payload in ({"metric": "up"}, {"metric": "queue_ready", "query": "secret"}, {"metric": "queue_ready", "hours": 1000}):
        try:
            model.model_validate(payload)
            assert False, payload
        except ValueError:
            pass


def test_hr_response_requires_system_identity_and_hides_transport_details(monkeypatch):
    class Response:
        content = json.dumps({"status": "ok", "system": "ocr"}).encode()
        def raise_for_status(self): pass
        def json(self): return json.loads(self.content)

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    settings = OpsSettings(hr_base_url="https://hr.example/api/ops", hr_read_token="x" * 40)
    result = asyncio.run(hr_tools.fetch_hr_alerts(settings))
    assert result == {"status": "unavailable", "system": "hr", "reason": "ValueError"}
    assert "hr.example" not in json.dumps(result)


def test_hr_alerts_are_deduplicated_and_gateway_loss_is_debounced():
    tracker = HrAlertTracker(unavailable_threshold=3)
    alert = {"name": "HrWebDown", "severity": "critical", "state": "firing"}
    assert tracker.success([alert]) == [("firing", "HrWebDown", "critical")]
    assert tracker.success([alert]) == []
    assert tracker.success([]) == [("resolved", "HrWebDown", "critical")]
    assert tracker.failure() == []
    assert tracker.failure() == []
    assert tracker.failure() == [("visibility_lost", "HrMonitoringUnreachable", "critical")]
    assert tracker.failure() == []
    assert tracker.success([]) == [("visibility_restored", "HrMonitoringReachable", "warning")]
