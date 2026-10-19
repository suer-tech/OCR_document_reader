from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded

from ocr_platform.storage.pipeline_lock import PipelineRunBusy, pipeline_run_lock


def test_budget_failure_is_terminal_in_worker(monkeypatch):
    from ocr_platform.workers import pipeline_worker as worker

    failed = []

    async def exhausted(run_id):
        raise UsageLimitExceeded("request_limit of 3")

    monkeypatch.setattr(worker, "process_pipeline_run", exhausted)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(worker_max_retries=3))
    monkeypatch.setattr(worker, "_set_failed", lambda *args: failed.append(args))
    monkeypatch.setattr(worker, "publish_ingest_job", lambda job: pytest.fail("must not retry a budget error"))
    worker._handle_job(worker.IngestJob("run"))
    assert len(failed) == 1 and failed[0][0] == "run"


def test_worker_does_not_mark_busy_run_failed_or_retry_it(monkeypatch):
    from ocr_platform.workers import pipeline_worker as worker

    async def busy(run_id):
        raise PipelineRunBusy(run_id)

    monkeypatch.setattr(worker, "process_pipeline_run", busy)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(worker_max_retries=3))
    monkeypatch.setattr(worker, "_set_failed", lambda *a: pytest.fail("busy run is not failed"))
    monkeypatch.setattr(worker, "_set_retrying", lambda *a: pytest.fail("busy run is not a retry"))
    with pytest.raises(PipelineRunBusy):
        worker._handle_job(worker.IngestJob("run"))


def test_busy_delivery_is_requeued_without_ack(monkeypatch):
    from ocr_platform.queueing import rabbitmq

    events = []

    class StopTest(BaseException):
        pass

    class Channel:
        def basic_qos(self, **kwargs):
            pass

        def basic_consume(self, **kwargs):
            self.callback = kwargs["on_message_callback"]

        def basic_nack(self, **kwargs):
            events.append(("nack", kwargs))

        def basic_ack(self, **kwargs):
            pytest.fail("must retain an active delivery")

        def start_consuming(self):
            self.callback(self, SimpleNamespace(delivery_tag=7), None, b'{"pipeline_run_id":"run"}')
            raise StopTest()

    channel = Channel()
    connection = SimpleNamespace(sleep=lambda seconds: events.append(("sleep", seconds)), close=lambda: None)
    monkeypatch.setattr(rabbitmq, "_open_channel", lambda: (connection, channel))
    monkeypatch.setattr(rabbitmq, "get_settings", lambda: SimpleNamespace(rabbitmq_ingest_queue="test"))

    def busy(job):
        raise PipelineRunBusy(job.pipeline_run_id)

    with pytest.raises(StopTest):
        rabbitmq.consume_ingest_jobs(busy)
    assert events == [("nack", {"delivery_tag": 7, "requeue": True}), ("sleep", 5.0)]


def test_sqlite_development_lock_excludes_duplicate_and_releases(monkeypatch):
    from ocr_platform.storage import repository

    monkeypatch.setattr(repository, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    with pytest.raises(ValueError):
        with pipeline_run_lock("same") as first:
            assert first
            with pipeline_run_lock("same") as duplicate:
                assert not duplicate
            with pipeline_run_lock("other") as other:
                assert other
            raise ValueError("processing failed")
    with pipeline_run_lock("same") as released:
        assert released
