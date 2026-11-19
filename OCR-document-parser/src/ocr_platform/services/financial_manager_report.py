"""Read report tables with Argon and preserve page-level evidence and raw text."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import json
import re

import pymupdf
from pydantic import BaseModel, TypeAdapter, ValidationError, create_model

from ocr_platform.api.schemas import ValidationIssue
from ocr_platform.config.financial_manager_report_profile import PROFILE_ID, extraction_config
from ocr_platform.observability.logging import get_logger
from ocr_platform.services.financial_manager_report_prompts import (
    EXTRACT_REPORT, RETRY_MONEY, RETRY_REQUIRED, TRANSCRIBE_PAGES,
)
from ocr_platform.services.financial_manager_report_schema import (
    Fact, FinancialManagerReport, Money, MoneyRetry, PageText, ReportModel, Section, Source, Transcription,
)
from ocr_platform.services.llm_gateway import call_llm_json_with_fallback

REVIEW_CODE = "financial_manager_report_requires_review"
TRANSCRIPTION_BATCH_SIZE = 2
RASTER_PAGE_FRACTION = 0.15
UNCERTAIN_REASONS = {"unreadable", "ambiguous", "unsupported_source"}
CELL_LABELS = ("table", "row", "column")
MISSING_REASONS = {
    "not_stated": "В отчёте не указано.",
    "unreadable": "Фрагмент или ячейка не читается.",
    "ambiguous": "Значение или его принадлежность неоднозначны.",
    "not_applicable": "Поле не применимо к этой строке.",
    "unsupported_source": "Не подтверждён источник или значение ячейки.",
}

logger = get_logger(__name__)


@dataclass
class ReportPage:
    number: int
    native_text: str | None
    image: dict


@dataclass
class MoneyRetryTarget:
    row: BaseModel
    field: str
    pages: set[int]
    labels: dict
    context: dict


def read_pdf(storage_path: str) -> list[ReportPage]:
    pages = []
    with pymupdf.open(storage_path) as pdf:
        if not pdf.is_pdf or pdf.needs_pass or not len(pdf):
            raise ValueError("Для отчёта нужен доступный для чтения PDF с минимум одной страницей.")
        for number, page in enumerate(pdf, 1):
            text = page.get_text(sort=True).strip()
            # A scanned table may coexist with a small searchable header on the same page.
            raster_area = sum((pymupdf.Rect(item["bbox"]) & page.rect).get_area()
                              for item in page.get_image_info())
            native_text = (text if any(c.isalpha() for c in text)
                           and raster_area < RASTER_PAGE_FRACTION * page.rect.get_area() else None)
            scale = min(3.0, 2400 / max(page.rect.width, page.rect.height))
            pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            encoded = base64.b64encode(pixels.tobytes("jpeg", jpg_quality=90)).decode("ascii")
            pages.append(ReportPage(number, native_text, {
                "type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded, "detail": "high"},
            }))
    return pages


def _parse_report_fields(schema: type[BaseModel], payload: object) -> BaseModel:
    if not isinstance(payload, dict):
        raise ValueError("Report response is not an object")
    fields = {}
    for name, spec in schema.model_fields.items():
        if name not in payload:
            continue
        try:
            fields[name] = TypeAdapter(spec.rebuild_annotation()).validate_python(payload[name])
        except ValidationError:
            continue
    if not fields:
        raise ValueError("Report response has no usable fields")
    return schema.model_construct(**fields)


async def _ask(schema, prompt, pages, config, *, task, document_id, pipeline_run_id, attempts=2, targets=None,
               allow_partial_fields=False, retry_empty=False):
    numbers = ", ".join(str(page.number) for page in pages)
    user_content = f"Физические номера страниц PDF в порядке изображений: {numbers}."
    if targets:
        user_content += "\n" + json.dumps(targets, ensure_ascii=False, separators=(",", ":"))
    # Argon rejects nested JSON schemas in response_format. Check the contract locally.
    system_prompt = prompt + "\nJSON Schema ответа:\n" + json.dumps(
        schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"),
    )
    for attempt in range(attempts):
        response = await asyncio.to_thread(
            call_llm_json_with_fallback, provider=config["provider"], models=[config["model"]],
            task_name=task, request_type=task, profile_id=PROFILE_ID, document_id=document_id,
            pipeline_run_id=pipeline_run_id, temperature=0.0,
            timeout_seconds=float(config.get("timeout_seconds", 180)),
            reasoning_effort=config.get("reasoning_effort", "low"), max_tokens=int(config.get("max_tokens", 16384)),
            system_prompt=system_prompt,
            user_content=user_content,
            image_parts=[page.image for page in pages],
            response_schema={"type": "json_object"},
        )
        if response is None:
            if retry_empty:
                continue
            return None
        try:
            parsed = (_parse_report_fields(schema, response.parsed) if allow_partial_fields
                      else schema.model_validate(response.parsed))
            if isinstance(parsed, Transcription) and sorted(page.page for page in parsed.pages) != [
                page.number for page in pages
            ]:
                raise ValueError("Wrong transcription page IDs")
            return parsed
        except (ValidationError, ValueError):
            logger.warning("financial_manager_report_invalid_model_response", task=task, attempt=attempt + 1,
                           document_id=document_id, pipeline_run_id=pipeline_run_id)
    return None


def _source(source: Source | None, page_count: int) -> dict | None:
    if not source or source.page > page_count:
        return None
    result = source.model_dump(exclude_none=True)
    for key in ("table", "row", "column", "quote"):
        value = result.get(key, "").strip()
        if value:
            result[key] = value
        else:
            result.pop(key, None)
    if not result.get("quote") and not (result.get("table") and (result.get("row") or result.get("column"))):
        return None
    return result


def _quoted_money(quote: str, value: str) -> bool:
    number = re.sub(r"\s+", "", quote).replace("−", "-")
    number = re.sub(r"(?:руб(?:лей|ля|ль)?\.?|₽)$", "", number, flags=re.IGNORECASE)
    if re.fullmatch(r"-?[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{1,2})?", number):
        number = number.replace(".", "")
    # A single decimal separator in the cell may be either a comma or a dot.
    number = number.replace(",", ".")
    return bool(re.fullmatch(r"-?[0-9]+(?:\.[0-9]{1,2})?", number)
                and Decimal(number) == Decimal(value))


def _fact(fact: Fact, page_count: int) -> tuple[object, dict | None, str | None]:
    source = _source(fact.source, page_count)
    value = fact.value.strip() if isinstance(fact.value, str) else fact.value
    if value is None or value == "":
        return None, source, fact.reason or "not_stated"
    if not source or (isinstance(fact, Fact[Money]) and not _quoted_money(source.get("quote", ""), value)):
        return None, source, "unsupported_source"
    return value, source, None


def _money_retry_targets(report: FinancialManagerReport, page_count: int) -> list[MoneyRetryTarget]:
    targets = []
    for section_name, section in report:
        if not isinstance(section, Section):
            continue
        section_source = _source(section.source, page_count) or {}
        for row_index, row in enumerate(section.rows, 1):
            row_source = _source(getattr(row, "source", None), page_count) or {}
            row_pages = {row_source["page"]} if row_source else set()
            row_context = {"section": section_name, "row_index": row_index}
            if row_source:
                row_context["row_source"] = row_source
            if hasattr(row, "row_kind"):
                row_context["row_kind"] = row.row_kind
            money_cells = []
            for field, fact in row:
                if isinstance(fact, Fact[Money]):
                    value, source, reason = _fact(fact, page_count)
                    if reason in UNCERTAIN_REASONS:
                        money_cells.append((field, source or {}))
                    elif value is not None:
                        row_pages.add(source["page"])
                elif isinstance(fact, Fact) and field in {
                    "name", "creditor_name", "claim_type", "queue", "queue_part", "outside_register",
                }:
                    value, source, _ = _fact(fact, page_count)
                    if value is not None:
                        row_context[field] = value
                        if field in {"name", "creditor_name", "claim_type"}:
                            row_pages.add(source["page"])
            for field, cell_source in money_cells:
                pages = row_pages | ({cell_source["page"]} if cell_source else set())
                if not pages:
                    continue
                labels = {
                    **({"table": section_source["table"]} if "table" in section_source else {}),
                    **{key: row_source[key] for key in ("table", "row") if key in row_source},
                    **{key: cell_source[key] for key in CELL_LABELS if key in cell_source},
                }
                targets.append(MoneyRetryTarget(row, field, pages, labels, row_context))
    return targets


async def _retry_money(report: FinancialManagerReport, pages: list[ReportPage], config: dict, **context) -> None:
    pending = _money_retry_targets(report, len(pages))
    if not pending:
        return
    targets = dict(enumerate(pending, 1))
    requested_pages = set().union(*(target.pages for target in pending))
    response = await _ask(
        MoneyRetry, RETRY_MONEY, [page for page in pages if page.number in requested_pages], config,
        task="financial_manager_report_money_retry", attempts=1, **context,
        targets=[
            {"target_id": target_id, "field": target.field, "pages": sorted(target.pages),
             "location": target.labels, **target.context}
            for target_id, target in targets.items()
        ],
    )
    if response is None:
        return
    counts = Counter(cell.target_id for cell in response.cells)
    for cell in response.cells:
        target = targets.get(cell.target_id)
        if target is None or counts[cell.target_id] != 1:
            continue
        value, source, _ = _fact(cell.fact, len(pages))
        if (value is None or source["page"] not in target.pages
                or not all(key in source for key in CELL_LABELS)
                or any(source[key].casefold().split() != label.casefold().split()
                       for key, label in target.labels.items())):
            continue
        setattr(target.row, target.field, cell.fact)


def _row(row: BaseModel, page_count: int) -> tuple[dict, bool]:
    values, sources, reasons = {}, {}, {}
    uncertain = False
    for key, fact in row:
        if isinstance(fact, Fact):
            value, source, reason = _fact(fact, page_count)
            values[key] = value
            if source:
                sources[key] = source
            if reason:
                reasons[key] = reason
                uncertain |= reason in UNCERTAIN_REASONS
        elif isinstance(fact, Source):
            values[key] = _source(fact, page_count)
            uncertain |= values[key] is None
        else:
            values[key] = fact
    values["sources"] = sources
    values["null_reasons"] = reasons
    return values, uncertain


def _section(section: Section, page_count: int) -> tuple[dict, str | None]:
    status, source, rows, uncertain = section.status, _source(section.source, page_count), [], False
    for row in section.rows:
        result, row_uncertain = _row(row, page_count)
        rows.append(result)
        uncertain |= row_uncertain
    if (status == "listed" and (not rows or uncertain)
            or status == "none_declared" and (rows or not (source and re.search(r"\w", source.get("quote", ""))))
            or status in {"unreadable", "not_found"} and rows):
        status = "partial"
    issue = {
        "partial": "Раздел прочитан частично или содержит неподтверждённые ячейки.",
        "unreadable": "Раздел найден, но не читается.",
        "not_found": "Раздел не найден в исходном отчёте.",
    }.get(status)
    return {"status": status, "source": source, "rows": rows}, issue


def _has_required_value(node: object, page_count: int) -> bool:
    if isinstance(node, Fact):
        return _fact(node, page_count)[0] is not None
    if isinstance(node, Section):
        if node.rows:
            return True
        return _section(node, page_count)[0]["status"] == "none_declared"
    return False


async def _retry_required(report: FinancialManagerReport, pages: list[ReportPage], profile: dict, **context) -> None:
    pending = {
        name: (spec.rebuild_annotation(), ...)
        for name, spec in FinancialManagerReport.model_fields.items()
        if profile["fields"][name].get("required") and not _has_required_value(getattr(report, name, None), len(pages))
    }
    if not pending:
        return
    schema = create_model("MissingRequiredReportFields", __base__=ReportModel, **pending)
    response = await _ask(
        schema, EXTRACT_REPORT + RETRY_REQUIRED, pages, profile["models"]["llm_extraction"],
        task="financial_manager_report_required_retry", attempts=1, allow_partial_fields=True, **context,
    )
    if response is None:
        return
    for name, node in response:
        if _has_required_value(node, len(pages)):
            setattr(report, name, node)


def build_fields(report: FinancialManagerReport | None, page_count: int, profile: dict) -> dict:
    fields = {}
    for name, cfg in profile["fields"].items():
        if name == "report_text_complete":
            continue
        node = getattr(report, name, None)
        evidence, issue = None, None
        if isinstance(node, Fact):
            value, evidence, reason = _fact(node, page_count)
            reasoning = MISSING_REASONS.get(reason) if reason else evidence.get("quote")
            if value is None and (cfg.get("required") or reason in UNCERTAIN_REASONS):
                issue = reasoning
        elif isinstance(node, Section):
            value, issue = _section(node, page_count)
            reasoning = issue
        elif report is None:
            value = None
            reasoning = issue = "Argon не вернул пригодный структурированный результат."
        else:
            value = None
            reasoning = issue = "Argon не вернул значение поля." if cfg.get("required") else None
        if name == "debtor_inn" and value is not None:
            quote_digits = re.sub(r"\D", "", evidence.get("quote", ""))
            if value != quote_digits:
                value = None
                reasoning = issue = MISSING_REASONS["unsupported_source"]
        fields[name] = {
            "value": value, "source": "document" if evidence or isinstance(node, Section) else None,
            "extraction_method": "llm", "reasoning": reasoning,
        }
        if evidence:
            fields[name]["evidence"] = evidence
        if issue:
            fields[name]["validation_issue"] = issue
    return fields


def _page_text(page: ReportPage, parsed: PageText | None) -> tuple[str, bool]:
    if page.native_text is not None:
        return page.native_text, True
    if parsed and parsed.status == "blank" and not (parsed.text or "").strip():
        return "", True
    if parsed and parsed.status in {"readable", "partial"} and (parsed.text or "").strip():
        if parsed.status == "readable":
            return parsed.text, True
        return parsed.text + "\n[Страница прочитана частично]", False
    return "[Текст страницы не получен]", False


async def extract_fields(storage_path: str, profile: dict, *, document_id=None, pipeline_run_id=None) -> dict:
    config = extraction_config(profile)
    pages = await asyncio.to_thread(read_pdf, storage_path)
    context = {"document_id": document_id, "pipeline_run_id": pipeline_run_id}
    report = await _ask(FinancialManagerReport, EXTRACT_REPORT, pages, config,
                        task="financial_manager_report_fields", allow_partial_fields=True, retry_empty=True, **context)
    if report is not None:
        await _retry_required(report, pages, profile, **context)
        await _retry_money(report, pages, config, **context)
    transcribed = {}
    scanned = [page for page in pages if page.native_text is None]
    for start in range(0, len(scanned), TRANSCRIPTION_BATCH_SIZE):
        batch = scanned[start:start + TRANSCRIPTION_BATCH_SIZE]
        parsed = await _ask(Transcription, TRANSCRIBE_PAGES, batch, config,
                            task="financial_manager_report_transcription", retry_empty=True, **context)
        if parsed:
            transcribed.update({page.page: page for page in parsed.pages})
    pages_text, incomplete = [], []
    for page in pages:
        text, complete = _page_text(page, transcribed.get(page.number))
        pages_text.append(f"[Страница {page.number}]\n{text}")
        if not complete:
            incomplete.append(page.number)
    fields = build_fields(report, len(pages), profile)
    fields["report_text_complete"] = {"value": not incomplete, "source": "document"}
    if incomplete:
        page_list = ", ".join(map(str, incomplete))
        issue = f"Полный текст не получен для страниц: {page_list}."
        fields["report_text_complete"].update(reasoning=issue, validation_issue=issue)
    fields["_raw_text"] = "\n\f\n".join(pages_text)
    return fields


def field_issues(fields: dict, profile: dict) -> list[ValidationIssue]:
    issues = []
    for name, cfg in profile["fields"].items():
        info = fields.get(name) or {}
        issue = info.get("validation_issue")
        if not issue and (cfg.get("required") and info.get("value") is None
                          or name == "report_text_complete" and info.get("value") is False):
            issue = info.get("reasoning") or "Не получен обязательный результат."
        if issue:
            issues.append(ValidationIssue(code=REVIEW_CODE, field_name=name, severity="error", message=issue))
    return issues
