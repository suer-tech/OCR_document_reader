from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from admin_ops.stats import daily_report
from ocr_platform.storage.models import Base, Document, PipelineRun


def utc_naive(*parts: int) -> datetime:
    # Production ORM stores naive UTC timestamps.
    return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)


def test_daily_report_uses_local_day_and_no_document_contents() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Document(id="doc-1", source_type="api", document_type="court_decision_ru"),
                Document(id="doc-2", source_type="api", document_type="court_decision_ru"),
                Document(id="doc-3", source_type="api", document_type="passport_main"),
            ]
        )
        session.add_all(
            [
                PipelineRun(id="run-1", document_id="doc-1", profile_id="court", status="done",
                            created_at=utc_naive(2026, 9, 14, 20), started_at=utc_naive(2026, 9, 14, 20),
                            finished_at=utc_naive(2026, 9, 14, 20, 1), retry_count=1),
                PipelineRun(id="run-2", document_id="doc-2", profile_id="court", status="failed",
                            created_at=utc_naive(2026, 9, 15, 1), started_at=utc_naive(2026, 9, 15, 1),
                            finished_at=utc_naive(2026, 9, 15, 1, 5), retry_count=2,
                            last_error="sensitive error text"),
                PipelineRun(id="run-3", document_id="doc-3", profile_id="passport", status="processing",
                            created_at=utc_naive(2026, 9, 13, 10), retry_count=0),
            ]
        )
        session.commit()
        result = daily_report(
            session, now=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
            timezone_name="Asia/Yekaterinburg",
        )
    assert result["date"] == "2026-09-15"
    assert result["totals"] == {
        "processed": 2, "documents_unique": 2, "errors": 1,
        "retries": 3, "in_progress": 1,
    }
    assert result["by_type"]["court_decision_ru"]["avg_seconds"] == 60.0
    assert "sensitive error text" not in str(result)
