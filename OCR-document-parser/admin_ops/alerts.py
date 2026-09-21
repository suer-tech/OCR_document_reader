from __future__ import annotations

import time
from collections import defaultdict


def critical_conditions(metrics: dict[str, float | None]) -> dict[str, bool]:
    requests = metrics.get("request_rate_5m")
    errors = metrics.get("error_rate_5m")
    ready = metrics.get("rabbitmq_ready")
    return {
        "API metrics endpoint down": metrics.get("api_up") == 0,
        "Worker metrics endpoint down": metrics.get("worker_up") == 0,
        "HTTP 5xx above 10%": bool(
            requests is not None and errors is not None and requests >= 0.05 and errors / requests > 0.1
        ),
        "RabbitMQ ready queue above 500": ready is not None and ready > 500,
    }


class AlertTracker:
    """Require two observations and deduplicate alerts independently of Codex."""

    def __init__(self, cooldown_seconds: int = 1800):
        self.streaks: dict[str, int] = defaultdict(int)
        self.last_sent: dict[str, float] = {}
        self.cooldown_seconds = cooldown_seconds

    def observe(self, metrics: dict[str, float | None], now: float | None = None) -> list[str]:
        current = time.monotonic() if now is None else now
        firing: list[str] = []
        for name, active in critical_conditions(metrics).items():
            self.streaks[name] = self.streaks[name] + 1 if active else 0
            if self.streaks[name] >= 2 and current - self.last_sent.get(name, float("-inf")) >= self.cooldown_seconds:
                firing.append(name)
                self.last_sent[name] = current
        return firing


class HrAlertTracker:
    """Deduplicate remote Alertmanager state and debounce loss of HR visibility."""

    def __init__(self, unavailable_threshold: int = 3):
        self.active: dict[str, str] = {}
        self.unavailable_streak = 0
        self.unavailable_sent = False
        self.unavailable_threshold = unavailable_threshold

    def success(self, alerts: list[dict]) -> list[tuple[str, str, str]]:
        messages: list[tuple[str, str, str]] = []
        current = {
            item["name"]: item.get("severity", "warning")
            for item in alerts
            if isinstance(item, dict) and isinstance(item.get("name"), str)
            and item.get("state") == "firing" and item.get("severity") in {"warning", "critical"}
        }
        for name, severity in current.items():
            if name not in self.active:
                messages.append(("firing", name, severity))
        for name, severity in self.active.items():
            if name not in current:
                messages.append(("resolved", name, severity))
        self.active = current
        self.unavailable_streak = 0
        if self.unavailable_sent:
            messages.append(("visibility_restored", "HrMonitoringReachable", "warning"))
            self.unavailable_sent = False
        return messages

    def failure(self) -> list[tuple[str, str, str]]:
        self.unavailable_streak += 1
        if self.unavailable_streak >= self.unavailable_threshold and not self.unavailable_sent:
            self.unavailable_sent = True
            return [("visibility_lost", "HrMonitoringUnreachable", "critical")]
        return []
