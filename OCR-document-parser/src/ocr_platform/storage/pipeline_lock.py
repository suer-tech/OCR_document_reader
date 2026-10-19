"""Session advisory locks serialize a pipeline run across worker processes."""
from contextlib import contextmanager
from hashlib import sha256
from threading import Lock

from sqlalchemy import func, select


class PipelineRunBusy(Exception):
    """Another worker owns this run; the queue must retain the delivery."""


_local_guard = Lock()
_local_runs: set[str] = set()


@contextmanager
def pipeline_run_lock(pipeline_run_id: str):
    from ocr_platform.storage import repository

    if repository.engine.dialect.name == "sqlite":
        # SQLite development mode: protect concurrent tasks in this process.
        with _local_guard:
            acquired = pipeline_run_id not in _local_runs
            if acquired:
                _local_runs.add(pipeline_run_id)
        try:
            yield acquired
        finally:
            if acquired:
                with _local_guard:
                    _local_runs.remove(pipeline_run_id)
        return

    if repository.engine.dialect.name != "postgresql":
        raise RuntimeError("Pipeline run locking requires PostgreSQL")
    key = int.from_bytes(sha256(pipeline_run_id.encode()).digest()[:8], "big", signed=True)
    with repository.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        acquired = conn.scalar(select(func.pg_try_advisory_lock(key)))
        try:
            yield bool(acquired)
        finally:
            if acquired:
                try:
                    conn.execute(select(func.pg_advisory_unlock(key)))
                except Exception:
                    # Never return a connection holding a session lock to the pool.
                    conn.invalidate()
                    raise
