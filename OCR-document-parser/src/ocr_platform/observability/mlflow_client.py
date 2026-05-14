from __future__ import annotations

from contextlib import contextmanager
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Any, Iterator
import contextvars
import socket
import time
from urllib.parse import urlparse

import mlflow
from mlflow.entities import Metric, Param, RunTag
from mlflow.tracking import MlflowClient

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import get_logger


logger = get_logger(__name__)

MlflowMetricPayload = list[tuple[str, float, int]]
MlflowTextPayload = list[tuple[str, str]]

_task_queue: Queue[dict[str, Any]] | None = None
_worker_started = False
_worker_lock = Lock()
_current_payload: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mlflow_current_payload",
    default=None,
)


def _ensure_worker() -> None:
    global _task_queue, _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        settings = get_settings()
        _task_queue = Queue(maxsize=max(10, settings.mlflow_async_queue_size))
        thread = Thread(target=_worker_loop, daemon=True, name="mlflow-async-worker")
        thread.start()
        _worker_started = True


def _worker_loop() -> None:
    while True:
        if _task_queue is None:
            return
        try:
            payload = _task_queue.get(timeout=1.0)
        except Empty:
            continue
        try:
            _execute_mlflow_payload(payload)
        except Exception as exc:
            logger.warning(
                "mlflow_async_task_failed",
                run_name=str(payload.get("name", "unknown")),
                error_type=type(exc).__name__,
                error=str(exc),
            )
        finally:
            _task_queue.task_done()


def _execute_mlflow_payload(payload: dict[str, Any]) -> None:
    name = str(payload["name"])
    tracking_uri = payload.get("tracking_uri")
    tags: dict[str, str] = payload.get("tags", {})
    params: dict[str, str] = payload.get("params", {})
    metrics: MlflowMetricPayload = payload.get("metrics", [])
    texts: MlflowTextPayload = payload.get("texts", [])

    settings = get_settings()
    retries = max(1, settings.mlflow_async_retries)
    backoff_seconds = max(0.0, settings.mlflow_async_retry_backoff_seconds)

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if tracking_uri and not _is_tracking_uri_reachable(tracking_uri):
                raise ConnectionError(f"MLflow tracking URI is unreachable: {tracking_uri}")
            client = MlflowClient(tracking_uri=tracking_uri) if tracking_uri else MlflowClient()
            experiment_name = settings.mlflow_experiment_name
            experiment = client.get_experiment_by_name(experiment_name)
            if experiment is None:
                experiment_id = client.create_experiment(
                    name=experiment_name,
                    artifact_location=settings.mlflow_experiment_artifact_location,
                )
            else:
                experiment_id = experiment.experiment_id
            run = client.create_run(experiment_id=experiment_id, tags=tags | {"mlflow.runName": name})
            run_id = run.info.run_id

            if params or tags:
                batch_params = [Param(key=k[:250], value=str(v)[:6000]) for k, v in params.items()]
                batch_tags = [RunTag(key=k[:250], value=str(v)[:6000]) for k, v in tags.items()]
                client.log_batch(run_id=run_id, metrics=[], params=batch_params, tags=batch_tags)

            if metrics:
                batch_metrics = [
                    Metric(
                        key=key[:250],
                        value=float(value),
                        timestamp=int(ts),
                        step=0,
                    )
                    for key, value, ts in metrics
                ]
                # Keep chunks small to avoid request-size issues.
                chunk_size = 200
                for idx in range(0, len(batch_metrics), chunk_size):
                    client.log_batch(
                        run_id=run_id,
                        metrics=batch_metrics[idx : idx + chunk_size],
                        params=[],
                        tags=[],
                    )

            if texts:
                if tracking_uri:
                    mlflow.set_tracking_uri(tracking_uri)
                with mlflow.start_run(run_id=run_id):
                    for text, artifact_file in texts:
                        mlflow.log_text(text, artifact_file)

            client.set_terminated(run_id=run_id, status="FINISHED")
            return
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                delay = backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "mlflow_async_task_retry",
                    run_name=name,
                    attempt=attempt,
                    retries=retries,
                    delay_seconds=delay,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if delay > 0:
                    time.sleep(delay)
                continue
            break

    if last_exc is not None:
        raise last_exc


def _is_tracking_uri_reachable(tracking_uri: str) -> bool:
    parsed = urlparse(tracking_uri)
    if parsed.scheme not in {"http", "https"}:
        return True
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _append_mlflow_op(op_name: str, *args: Any, **kwargs: Any) -> None:
    payload = _current_payload.get()
    if payload is None:
        # Outside async context keep fluent mlflow behavior.
        getattr(mlflow, op_name)(*args, **kwargs)
        return

    if op_name == "set_tag":
        key = str(args[0])
        value = str(args[1])
        payload["tags"][key] = value
        return
    if op_name == "log_param":
        key = str(args[0])
        value = str(args[1])
        payload["params"][key] = value
        return
    if op_name == "log_metric":
        key = str(args[0])
        value = float(args[1])
        payload["metrics"].append((key, value, int(time.time() * 1000)))
        return
    if op_name == "log_text":
        text = str(args[0])
        artifact_file = str(args[1])
        payload["texts"].append((text, artifact_file))
        return

    raise ValueError(f"Unsupported mlflow operation: {op_name}")


def mlflow_set_tag(key: str, value: Any) -> None:
    _append_mlflow_op("set_tag", key, value)


def mlflow_log_param(key: str, value: Any) -> None:
    _append_mlflow_op("log_param", key, value)


def mlflow_log_metric(key: str, value: float) -> None:
    _append_mlflow_op("log_metric", key, value)


def mlflow_log_text(text: str, artifact_file: str) -> None:
    _append_mlflow_op("log_text", text, artifact_file)


def mlflow_run_exists_by_tag(tag_key: str, tag_value: str) -> bool:
    settings = get_settings()
    tracking_uri = settings.mlflow_tracking_uri
    if tracking_uri and not _is_tracking_uri_reachable(tracking_uri):
        raise ConnectionError(f"MLflow tracking URI is unreachable: {tracking_uri}")
    client = MlflowClient(tracking_uri=tracking_uri) if tracking_uri else MlflowClient()
    experiment_name = settings.mlflow_experiment_name
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return False
    experiment_id = experiment.experiment_id
    safe_value = tag_value.replace("'", "\\'")
    runs = client.search_runs(
        experiment_ids=[experiment_id],
        filter_string=f"tags.{tag_key} = '{safe_value}'",
        max_results=1,
    )
    return len(runs) > 0


def mlflow_log_payload_sync(
    *,
    name: str,
    tags: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    metrics: list[tuple[str, float]] | None = None,
    texts: MlflowTextPayload | None = None,
) -> None:
    now_ms = int(time.time() * 1000)
    payload: dict[str, Any] = {
        "name": name,
        "tracking_uri": get_settings().mlflow_tracking_uri,
        "tags": tags or {},
        "params": params or {},
        "metrics": [(k, float(v), now_ms) for k, v in (metrics or [])],
        "texts": texts or [],
    }
    _execute_mlflow_payload(payload)


@contextmanager
def mlflow_run(name: str) -> Iterator[None]:
    settings = get_settings()
    tracking_uri = settings.mlflow_tracking_uri
    if settings.mlflow_async_logging:
        _ensure_worker()
        payload: dict[str, Any] = {
            "name": name,
            "tracking_uri": tracking_uri,
            "tags": {},
            "params": {},
            "metrics": [],
            "texts": [],
        }
        token = _current_payload.set(payload)
        try:
            yield None
        finally:
            _current_payload.reset(token)
            has_data = bool(payload["tags"] or payload["params"] or payload["metrics"] or payload["texts"])
            if has_data and _task_queue is not None:
                try:
                    _task_queue.put(payload, timeout=max(0.1, settings.mlflow_async_enqueue_timeout_seconds))
                except Full:
                    logger.warning("mlflow_async_queue_full_fallback_sync", run_name=name)
                    try:
                        _execute_mlflow_payload(payload)
                    except Exception as exc:
                        logger.warning(
                            "mlflow_fallback_sync_failed",
                            run_name=name,
                            error_type=type(exc).__name__,
                            error=str(exc),
                        )
    else:
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)
        nested = mlflow.active_run() is not None
        with mlflow.start_run(run_name=name, nested=nested) as run:
            yield run

