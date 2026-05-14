from __future__ import annotations

import json
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
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    channel.queue_declare(queue=settings.rabbitmq_ingest_queue, durable=True)
    return connection, channel


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
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception("ingest_job_handler_failed")
            # Сообщение подтверждаем после обработки, повторную постановку
            # выполняет worker по своей retry-логике.
            ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=settings.rabbitmq_ingest_queue, on_message_callback=_on_message)
    logger.info("rabbitmq_consumer_started", queue=settings.rabbitmq_ingest_queue)
    try:
        channel.start_consuming()
    finally:
        connection.close()

