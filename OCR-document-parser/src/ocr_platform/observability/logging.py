from __future__ import annotations

import logging
import os
from pathlib import Path

import structlog


def configure_logging() -> None:
    log_level = os.getenv("OCR_LOG_LEVEL", "INFO").upper()
    log_file_path = os.getenv("OCR_LOG_FILE_PATH", "logs/app.json.log").strip()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file_path:
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format="%(message)s", handlers=handlers)

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.EventRenamer("message"),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

