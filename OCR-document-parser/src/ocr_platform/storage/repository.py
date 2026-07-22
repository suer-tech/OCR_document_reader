from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ocr_platform.config.settings import get_settings
from ocr_platform.storage import models


settings = get_settings()
engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def init_db() -> None:
    models.Base.metadata.create_all(bind=engine)
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    for table_name, table in models.Base.metadata.tables.items():
        if inspector.has_table(table_name):
            existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
            model_cols = {col.name for col in table.columns}
            missing = model_cols - existing_cols
            if missing:
                with engine.connect() as conn:
                    for col_name in missing:
                        col_type = table.columns[col_name].type.compile(engine.dialect)
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
                    conn.commit()


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def generate_id() -> str:
    return str(uuid4())

