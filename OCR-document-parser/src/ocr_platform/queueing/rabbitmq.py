from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

import pika

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class IngestJob:
    pipeline_run_id: str
    attempt: int = 0


def _open_channel() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    settings = get_settings()
    url = settings.rabbitmq_url
    separator = "&" if "?" in url else "?"
    url = f"{url}{separator}heartbeat=0"
    connection = pika.BlockingConnection(pika.URLParameters(url))
    channel = connection.channel()
    channel.queue_declare(queue=settings.rabbitmq_ingest_queue, durable=True)
    return connection, channel


def _safe_ack(
    ch: pika.adapters.blocking_connection.BlockingChannel,
    delivery_tag: int,
    max_retries: int = 3,
) -> None:
    for attempt in range(max_retries):
        try:
            ch.basic_ack(delivery_tag=delivery_tag)
            return
        except (
            pika.exceptions.StreamLostError,
            pika.exceptions.ConnectionWrongStateError,
        ) as exc:
            if attempt == max_retries - 1:
                logger.error(
                    "failed_to_ack_message",
                    delivery_tag=delivery_tag,
                    error=str(exc),
                )
                raise
            logger.warning(
                "ack_retrying",
                delivery_tag=delivery_tag,
                attempt=attempt + 1,
                max_retries=max_retries,
            )
            time.sleep(1.0)


def publish_ingest_job(job: IngestJob) -> None:
    settings = get_settings()
    connection, channel = _open_channel()
    try:
        body = json.dumps({"pipeline_run_id": job.pipeline_run_id, "attempt": job.attempt})
        channel.basic_publish(
            exchange="",
            routing_key=settings.rabbitmq_ingest_queue,
            body=body,
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        )
    finally:
        connection.close()


def consume_ingest_jobs(handler: Callable[[IngestJob], None]) -> None:
    settings = get_settings()

    while True:
        connection = None
        try:
            connection, channel = _open_channel()
            channel.basic_qos(prefetch_count=1)

            def _on_message(
                ch: pika.adapters.blocking_connection.BlockingChannel,
                method: pika.spec.Basic.Deliver,
                properties: pika.spec.BasicProperties,
                body: bytes,
            ) -> None:
                del properties
                try:
                    payload = json.loads(body.decode("utf-8"))
                    job = IngestJob(
                        pipeline_run_id=str(payload["pipeline_run_id"]),
                        attempt=int(payload.get("attempt", 0)),
                    )
                    handler(job)
                    _safe_ack(ch, method.delivery_tag)
                except Exception:
                    logger.exception("ingest_job_handler_failed")
                    try:
                        _safe_ack(ch, method.delivery_tag)
                    except Exception:
                        logger.exception("failed_to_ack_after_handler_error")

            channel.basic_consume(
                queue=settings.rabbitmq_ingest_queue,
                on_message_callback=_on_message,
            )
            logger.info("rabbitmq_consumer_started", queue=settings.rabbitmq_ingest_queue)
            channel.start_consuming()
        except (
            pika.exceptions.StreamLostError,
            pika.exceptions.ConnectionWrongStateError,
            pika.exceptions.ConnectionClosedByBroker,
        ) as exc:
            logger.warning("rabbitmq_connection_lost_reconnecting", error=str(exc))
            time.sleep(2.0)
        except Exception as exc:
            logger.exception("rabbitmq_unexpected_error_reconnecting", error=str(exc))
            time.sleep(5.0)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
