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
