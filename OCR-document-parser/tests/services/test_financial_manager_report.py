"""Offline contract and worker tests for financial-manager report extraction."""

import asyncio
import base64
from contextlib import nullcontext
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import financial_manager_report as service
from ocr_platform.services.financial_manager_report_schema import FinancialManagerReport

MODEL = load_profile(service.PROFILE_ID)["models"]["llm_extraction"]["model"]


def src(page, quote, *, table=None, row=None, column=None):
    return dict(page=page, quote=quote, table=table, row=row, column=column)


def fact(value=None, source=None, reason=None):
    return dict(value=value, source=source, reason=reason if value is not None else reason or "not_stated")


def cell(value, quote, page, table, row, column):
    return fact(value, src(page, quote, table=table, row=row, column=column))


def section(rows, page, name):
    return dict(status="listed", source=src(page, name, table=name), rows=rows)


def model_report(*, procedure="РИ", page=2):
    prop = "Сведения о составе и стоимости имущества должника"
    sales = "Сведения о ходе реализации имущества должника"
    claims = "Сведения о размере требований кредиторов, включённых в реестр"
    expenses = "Сведения о наличии и исполнении требований по текущим платежам"
    property_rows = [
        dict(name=cell("Квартира", "Квартира", 1, prop, "1", "Имущество"),
             manager_valuation=fact(), appraiser_valuation=fact(), sole_home=fact(),
             secured_by_pledge=cell(True, "ипотекой", 1, prop, "1", "Исключается"),
             pledgee=fact(), pledge_details=fact("ипотека", src(1, "ипотекой")),
             exclusion_text=cell("Суд утвердил мировое соглашение.", "Суд утвердил мировое соглашение.",
                                 1, prop, "1", "Основания исключения"),
             exclusion_basis=fact("Определение суда", src(1, "Определение суда"))),
        dict(name=fact("Автомобиль", src(1, "Автомобиль")),
             manager_valuation=cell("300000.00", "300 000,00", 1, prop, "2", "Оценка управляющего"),
             appraiser_valuation=cell("290000.00", "290 000,00", 1, prop, "2", "Оценка оценщика"),
             sole_home=fact(reason="not_applicable"), secured_by_pledge=fact(),
             pledgee=fact(), pledge_details=fact(), exclusion_text=fact(), exclusion_basis=fact()),
    ]
    returns = "Торги не состоялись. Имущество возвращено должнику."
    sales_rows = [
        dict(name=fact("Автомобиль", src(page, "Автомобиль")),
             sale_method=fact("Торги", src(page, "Торги")), sale_result=fact("Реализовано", src(page, "Реализовано")),
             contract_price=cell("273113.00", "273 113,00", page, sales, "1", "Стоимость имущества по договору"),
             paid_amount=cell("270000.00", "270 000,00", page, sales, "1", "Оплачено"),
             unsold_return_text=fact(reason="not_applicable")),
        dict(name=fact("Доля", src(page, "Доля")), sale_method=fact(), sale_result=fact(returns, src(page, returns)),
             contract_price=fact(), paid_amount=fact(), unsold_return_text=fact(returns, src(page, returns))),
    ]

    def claim(kind, name, category, outside, amount, paid, caption, row_page):
        quote = amount.replace(".", ",")
        return dict(
            row_kind=kind, source=src(row_page, caption, table=claims, row=caption),
            queue=fact("Третья очередь", src(1, "Третья очередь")),
            queue_part=fact("Часть 1", src(1, "Часть 1")) if category == "secured" else fact(),
            creditor_name=fact(name, src(row_page, name)) if name else fact(reason="not_applicable"),
            claim_type=fact(caption, src(row_page, caption)), claim_category=fact(category, src(row_page, caption)),
            secured_by_pledge=fact(True, src(1, "Обязательства с залогом")) if category == "secured" else fact(),
            outside_register=fact(outside, src(row_page, caption)) if outside is not None else fact(reason="not_applicable"),
            included_amount=cell(amount, quote, row_page, claims, caption, "Сумма включённых требований"),
            paid_amount=cell(paid, paid.replace(".", ","), row_page, claims, caption, "Сумма погашённых требований"),
        )

    register = [
        claim("detail", "Котов", "secured", False, "54000.00", "45000.00", "Обязательства с залогом", 1),
        claim("detail", "Тарасенко", "principal", False, "1977.00", "0.00", "Основной долг", 1),
        claim("detail", "Тарасенко", "other", True, "11905.45", "0.00", "За реестром", page),
        claim("detail", "Кредитор А", "sanctions", False, "50.00", "0.00", "Санкции", page),
        claim("detail", "Кредитор Б", "salary_or_severance", False, "20.00", "0.00", "Выходное пособие", page),
        claim("detail", "Кредитор В", "procedure_interest", False, "18.00", "0.00", "Проценты в процедуре", page),
        claim("subtotal", None, "secured", False, "54000.00", "45000.00", "Всего по части 1", page),
        claim("total", None, "other", None, "56065.00", "45000.00", "Итого", page),
    ]

    def expense(kind, label, category, paid, balance):
        return dict(
            row_kind=kind, source=src(page, label, table=expenses, row=label),
            queue=fact("Первая очередь", src(1, "Первая очередь")), name=fact(label, src(page, label)),
            expense_category=fact(category, src(page, label)) if category else fact(reason="not_applicable"),
            obligation_amount=fact(reason="not_stated"),
            paid_amount=cell(paid, paid.replace(".", ","), page, expenses, label, "Сумма погашения"),
            unpaid_balance=cell(balance, balance.replace(".", ","), page, expenses, label, "Непогашенный остаток"),
        )

    return dict(
        case_number=fact("А01-42/2026", src(1, "Дело А01-42/2026")),
        debtor_full_name=fact("Тестов Тест Тестович", src(1, "Должник Тестов Тест Тестович")),
        debtor_inn=fact(), procedure_type=fact(procedure, src(1, "Реализация имущества" if procedure == "РИ"
                                                             else "Реструктуризация долгов")),
        report_date=fact("2026-11-19", src(1, "Дата отчёта 19.11.2026")),
        inventory_date=fact("2026-08-26", src(1, "Опись имущества должника проведена 26.08.2026")),
        property_inventory=section(property_rows, 1, prop), property_sales=section(sales_rows, 1, sales),
        creditor_register=section(register, 1, claims),
        current_payments=section([
            expense("detail", "Вознаграждение управляющего", "manager_remuneration", "25100.00", "0.00"),
            expense("detail", "Услуги оценщика", "engaged_services", "0.00", "7608.38"),
            expense("total", "Итого", None, "25100.00", "7608.38"),
        ], 1, expenses),
    )


def pdf_bytes(page_modes):
    with pymupdf.open() as doc:
        for number, mode in enumerate(page_modes, 1):
            page = doc.new_page(width=220, height=300)
            if mode == "native":
                page.insert_text((20, 30), f"NATIVE PAGE {number}")
            elif mode != "blank":
                with pymupdf.open() as image_doc:
                    src_page = image_doc.new_page(width=220, height=300)
                    src_page.insert_text((20, 30), f"SCANNED PAGE {number}")
                    image_rect = pymupdf.Rect(0, 0, 220, 120) if mode == "mixed_layer" else page.rect
                    page.insert_image(image_rect, pixmap=src_page.get_pixmap(), keep_proportion=False)
                if mode == "mixed_layer":
                    page.insert_text((20, 270), "Searchable caption on a raster page")
        return doc.tobytes()


@pytest.fixture
def local_api(monkeypatch, tmp_path):
    from ocr_platform.orchestration import run_processor as worker
    from ocr_platform.storage import file_storage, models, repository

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(file_storage, "_ensure_storage_dir", lambda: tmp_path)
    from ocr_platform.api import main

    broker = Mock()
    monkeypatch.setattr(main, "publish_ingest_job", broker)
    tracer = Mock(span=Mock(side_effect=lambda *args, **kwargs: nullcontext()))
    monkeypatch.setattr(worker, "get_pipeline_tracer", lambda: tracer)
    monkeypatch.setattr(worker, "mlflow_run", lambda *args: nullcontext())
    for name in ("mlflow_set_tag", "mlflow_log_param", "mlflow_log_metric", "mlflow_log_text"):
        monkeypatch.setattr(worker, name, Mock())
    webhook = AsyncMock()
    monkeypatch.setattr(worker, "_trigger_webhook_safely", webhook)
    monkeypatch.setattr(worker.ocr_service, "extract_text_at_ingest", Mock(side_effect=AssertionError("Generic OCR")))
    with TestClient(main.create_app()) as client:
        yield client, worker, broker, webhook
    engine.dispose()


@pytest.mark.parametrize(("kind", "modes", "procedure", "scanned"), [
    pytest.param("upload", ["native", "native"], "РИ", [], id="native-ri"),
    pytest.param("ingest", ["scan", "scan"], "РД", [1, 2], id="scans-rd"),
    pytest.param("upload", ["native", "mixed_layer"], "РИ", [2], id="mixed-ri"),
])
def test_report_pdf_through_api_worker_and_webhook(local_api, monkeypatch, kind, modes, procedure, scanned):
    client, worker, broker, webhook = local_api
    calls = []

    def gateway(**kwargs):
        calls.append(kwargs)
        assert kwargs["models"] == [MODEL] and kwargs["provider"] == "router_ai"
        assert kwargs["response_schema"] == {"type": "json_object"}
        assert "JSON Schema ответа:" in kwargs["system_prompt"]
        assert all(part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in kwargs["image_parts"])
        if kwargs["task_name"].endswith("_transcription"):
            assert len(kwargs["image_parts"]) == len(scanned)
            return SimpleNamespace(parsed={"pages": [
                {"page": number, "status": "readable", "text": f"SCANNED PAGE {number}"} for number in scanned
            ]})
        assert len(kwargs["image_parts"]) == len(modes)
        return SimpleNamespace(parsed=model_report(procedure=procedure))

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    content = pdf_bytes(modes)
    meta = dict(source_type="external", document_type=service.PROFILE_ID, idempotency_key=kind,
                webhook_url="https://example.invalid/hook")
    if kind == "upload":
        response = client.post("/documents/upload", data=meta, files={"file": ("report.pdf", content, "application/pdf")})
    else:
        response = client.post("/documents/ingest", json={**meta, "content_type": "pdf",
                               "content_base64": base64.b64encode(content).decode()})
    assert response.status_code == 202, response.text
    run = response.json()["pipeline_run_id"]
    doc = response.json()["document_id"]
    assert broker.call_args.args[0].pipeline_run_id == run
    asyncio.run(worker._process_pipeline_run_impl(run))
    assert client.get(f"/pipeline-runs/{run}").json()["status"] == "done"
    response = client.get(f"/documents/{doc}/result")
    assert response.status_code == 200
    result = response.json()
    assert result["fields"] == webhook.await_args.args[1]["fields"]
    assert result["raw_text"] == webhook.await_args.args[1]["raw_text"]
    for n in (1, 2):
        assert f"[Страница {n}]\n{'SCANNED' if n in scanned else 'NATIVE'} PAGE {n}" in result["raw_text"]
    assert result["raw_text"].index("[Страница 1]") < result["raw_text"].index("[Страница 2]")
    assert len(calls) == 1 + bool(scanned)
    fields = result["fields"]
    assert fields["case_number"]["evidence"]["page"] == 1
    assert fields["debtor_full_name"]["value"] == "Тестов Тест Тестович"
    assert fields["debtor_inn"]["value"] is None and "evidence" not in fields["debtor_inn"]
    assert fields["procedure_type"]["value"] == procedure
    assert fields["report_date"]["value"] == "2026-11-19"
    assert fields["inventory_date"]["value"] == "2026-08-26"
    assert fields["report_text_complete"]["value"] is True
    assert result["validation_status"] == "ok"
    assert result["human_review_required"] is False
    inventory = fields["property_inventory"]["value"]["rows"]
    assert inventory[0]["name"] == "Квартира" and "мировое" in inventory[0]["exclusion_text"]
    assert inventory[0]["sole_home"] is None and inventory[0]["secured_by_pledge"] is True
    assert inventory[1]["manager_valuation"] == "300000.00" and inventory[1]["appraiser_valuation"] == "290000.00"
    sales = fields["property_sales"]["value"]["rows"]
    assert sales[0]["contract_price"] == "273113.00" and sales[0]["paid_amount"] == "270000.00"
    assert sales[0]["sources"]["contract_price"]["column"] == "Стоимость имущества по договору"
    assert "возвращено должнику" in sales[1]["unsold_return_text"]
    rows = fields["creditor_register"]["value"]["rows"]
    assert rows[0]["claim_category"] == "secured" and rows[0]["paid_amount"] == "45000.00"
    assert rows[0]["secured_by_pledge"] is True
    assert [(r["included_amount"], r["outside_register"]) for r in rows if r["creditor_name"] == "Тарасенко"] == [
        ("1977.00", False), ("11905.45", True),
    ]
    assert rows[2]["sources"]["included_amount"]["page"] == 2 and rows[2]["sources"]["queue"]["page"] == 1
    assert {r["claim_category"] for r in rows if r["row_kind"] == "detail"} == {
        "secured", "principal", "sanctions", "salary_or_severance", "procedure_interest", "other",
    }
    assert [r["row_kind"] for r in rows[-2:]] == ["subtotal", "total"]
    payments = fields["current_payments"]["value"]["rows"]
    assert [r["expense_category"] for r in payments] == ["manager_remuneration", "engaged_services", None]
    assert payments[-1]["paid_amount"] == "25100.00" and payments[-1]["unpaid_balance"] == "7608.38"
    assert payments[-1]["obligation_amount"] is None and "32708.38" not in str(payments)
    assert payments[1]["paid_amount"] == "0.00"


@pytest.mark.parametrize("kind", ["upload", "ingest"])
def test_report_rejects_non_pdf_before_queue(local_api, kind):
    client, _, broker, _ = local_api
    meta = dict(source_type="external", document_type=service.PROFILE_ID, idempotency_key="wrong")
    if kind == "upload":
        reply = client.post("/documents/upload", data=meta, files={"file": ("report.txt", b"not PDF", "text/plain")})
    else:
        reply = client.post("/documents/ingest", json={**meta, "content_type": "text",
                            "content_base64": base64.b64encode(b"not PDF").decode()})
    assert reply.status_code == 400
    broker.assert_not_called()


@pytest.mark.parametrize(("status", "quote", "expected"), [
    ("none_declared", "Имущество не выявлено", "none_declared"),
    ("none_declared", None, "partial"),
    ("none_declared", "—", "partial"),
    ("not_found", None, "not_found"),
    ("unreadable", "Сведения об имуществе", "unreadable"),
])
def test_inventory_status_distinguishes_absence_from_unreadable(status, quote, expected):
    document = model_report()
    document["property_inventory"] = dict(status=status, source=src(1, quote) if quote else None, rows=[])
    report = FinancialManagerReport.model_validate(document)
    fields = service.build_fields(report, 2, load_profile(service.PROFILE_ID))
    assert fields["property_inventory"]["value"]["status"] == expected
    assert ("validation_issue" in fields["property_inventory"]) == (expected != "none_declared")


def test_invalid_source_amount_and_unreadable_cells_are_not_zero():
    document = model_report()
    payment = document["current_payments"]["rows"][-1]
    payment["unpaid_balance"] = fact(source=src(2, "…"), reason="unreadable")
    payment["paid_amount"]["source"]["quote"] = "7 608,38"
    document["property_sales"]["rows"][0]["contract_price"]["source"]["page"] = 3
    document["debtor_inn"] = fact("012345678901", src(1, "ИНН управляющего 012345678902"))
    fields = service.build_fields(FinancialManagerReport.model_validate(document), 2, load_profile(service.PROFILE_ID))
    pay = fields["current_payments"]["value"]
    assert pay["rows"][-1]["null_reasons"] == {
        "expense_category": "not_applicable", "obligation_amount": "not_stated",
        "paid_amount": "unsupported_source", "unpaid_balance": "unreadable",
    }
    assert pay["rows"][-1]["unpaid_balance"] is None and pay["rows"][-1]["paid_amount"] is None
    assert pay["rows"][1]["paid_amount"] == "0.00"
    assert pay["status"] == "partial"
    assert fields["property_sales"]["value"]["rows"][0]["contract_price"] is None
    assert fields["debtor_inn"]["value"] is None


def payment_with_wrong_amounts():
    document = model_report()
    payment = document["current_payments"]["rows"][-1]
    payment["paid_amount"]["source"]["quote"] = "7 608,38"
    payment["unpaid_balance"].update(value=None, reason="unreadable")
    payment["unpaid_balance"]["source"]["quote"] = "…"
    return document


def test_uncertain_money_is_recovered_through_api_without_replacing_other_cells(local_api, monkeypatch):
    client, worker, _, webhook = local_api
    document = payment_with_wrong_amounts()
    table = document["current_payments"]["source"]["table"]
    calls = []

    def gateway(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(parsed=document)
        assert kwargs["task_name"] == "financial_manager_report_money_retry"
        location, targets_json = kwargs["user_content"].split("\n", 1)
        assert location.endswith(": 2.")
        assert len(kwargs["image_parts"]) == 1
        targets = json.loads(targets_json)
        assert {(target["section"], target["field"]) for target in targets} == {
            ("current_payments", "paid_amount"), ("current_payments", "unpaid_balance"),
        }
        answers = {
            "paid_amount": cell("25100.00", "25 100,00", 2, table, "Итого", "Сумма погашения"),
            "unpaid_balance": cell("7608.38", "7 608,38", 2, table, "Итого", "Непогашенный остаток"),
        }
        return SimpleNamespace(parsed={"cells": [
            {"target_id": target["target_id"], "fact": answers[target["field"]]} for target in targets
        ] + [{"target_id": 999, "fact": answers["paid_amount"]}]})

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    response = client.post("/documents/upload", data={
        "source_type": "external", "document_type": service.PROFILE_ID, "idempotency_key": "money-retry",
        "webhook_url": "https://example.invalid/hook",
    }, files={"file": ("report.pdf", pdf_bytes(["native", "native"]), "application/pdf")})
    assert response.status_code == 202
    asyncio.run(worker._process_pipeline_run_impl(response.json()["pipeline_run_id"]))
    result = client.get(f"/documents/{response.json()['document_id']}/result").json()
    assert len(calls) == 2
    assert all(call["models"] == [MODEL] and call["provider"] == "router_ai" for call in calls)
    assert result["fields"] == webhook.await_args.args[1]["fields"]
    assert result["human_review_required"] is False
    payments = result["fields"]["current_payments"]["value"]
    assert payments["status"] == "listed"
    first, second, total = payments["rows"]
    assert first["unpaid_balance"] == second["paid_amount"] == "0.00"
    assert total["paid_amount"] == "25100.00" and total["unpaid_balance"] == "7608.38"
    assert total["sources"]["paid_amount"]["quote"] == "25 100,00"
    assert total["null_reasons"] == {"expense_category": "not_applicable", "obligation_amount": "not_stated"}


@pytest.mark.parametrize("failure", ["wrong_column", "another_page", "duplicated", "unreadable", "invalid_schema", "gateway"])
def test_money_retry_keeps_original_reasons_when_still_unconfirmed(tmp_path, monkeypatch, failure):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "native"]))
    document = payment_with_wrong_amounts()
    table = document["current_payments"]["source"]["table"]
    wrong = cell("7608.38", "7 608,38", 2, table, "Итого", "Непогашенный остаток")

    def gateway(**kwargs):
        if kwargs["task_name"].endswith("_fields"):
            return SimpleNamespace(parsed=document)
        targets = json.loads(kwargs["user_content"].split("\n", 1)[1])
        target_ids = {target["field"]: target["target_id"] for target in targets}
        first = {"target_id": target_ids["paid_amount"], "fact": wrong}
        second = {"target_id": target_ids["unpaid_balance"], "fact": wrong}
        if failure == "gateway":
            return None
        if failure == "invalid_schema":
            return SimpleNamespace(parsed={"cells": "invalid"})
        if failure == "another_page":
            second["fact"] = cell("7608.38", "7 608,38", 1, table, "Итого", "Непогашенный остаток")
        elif failure == "duplicated":
            return SimpleNamespace(parsed={"cells": [first, second, second]})
        elif failure == "unreadable":
            second["fact"] = fact(reason="unreadable")
        else:
            second["fact"] = cell("7608.38", "7 608,38", 2, table, "Итого", "Сумма погашения")
        return SimpleNamespace(parsed={"cells": [first, second]})

    mock = Mock(side_effect=gateway)
    monkeypatch.setattr(service, "call_llm_json_with_fallback", mock)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    assert mock.call_count == 2
    assert fields["current_payments"]["value"]["status"] == "partial"
    row = fields["current_payments"]["value"]["rows"][-1]
    assert row["paid_amount"] is None and row["unpaid_balance"] is None
    assert row["null_reasons"]["paid_amount"] == "unsupported_source"
    assert row["null_reasons"]["unpaid_balance"] == "unreadable"
    assert [issue.field_name for issue in service.field_issues(fields, profile)] == ["current_payments"]


def test_money_retry_uses_context_from_the_same_row_in_all_report_sections(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "native"]))
    document = model_report()
    missing_by_section = {
        "property_inventory": (1, {"manager_valuation": "300000.00", "appraiser_valuation": "290000.00"}),
        "property_sales": (0, {"contract_price": "273113.00", "paid_amount": "270000.00"}),
        "creditor_register": (0, {"included_amount": "54000.00", "paid_amount": "45000.00"}),
        "current_payments": (0, {"obligation_amount": "25100.00"}),
    }
    expected = {}
    for section, (index, amounts) in missing_by_section.items():
        row = document[section]["rows"][index]
        for field, amount in amounts.items():
            source = row[field]["source"] or dict(row["source"], column="Размер обязательства")
            expected[section, field] = fact(amount, dict(source, quote=amount.replace(".", ",")))
            row[field] = fact(reason="ambiguous")

    def gateway(**kwargs):
        if kwargs["task_name"].endswith("_fields"):
            return SimpleNamespace(parsed=document)
        targets = json.loads(kwargs["user_content"].split("\n", 1)[1])
        assert {(target["section"], target["field"]) for target in targets} == set(expected)
        assert len(kwargs["image_parts"]) == 2
        answers = []
        for target in targets:
            result = expected[target["section"], target["field"]]
            assert result["source"]["page"] in target["pages"]
            answers.append({"target_id": target["target_id"], "fact": result})
        return SimpleNamespace(parsed={"cells": answers})

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    for section, (index, amounts) in missing_by_section.items():
        table = fields[section]["value"]
        assert table["status"] == "listed"
        for field, amount in amounts.items():
            assert table["rows"][index][field] == amount


def test_money_without_a_row_anchor_remains_for_review_without_sending_other_pages(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "native"]))
    document = model_report()
    item = document["property_inventory"]["rows"][1]
    item["name"] = fact()
    item["manager_valuation"] = fact(reason="unreadable")
    item["appraiser_valuation"] = fact()
    mock = Mock(return_value=SimpleNamespace(parsed=document))
    monkeypatch.setattr(service, "call_llm_json_with_fallback", mock)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    mock.assert_called_once()
    table = fields["property_inventory"]["value"]
    assert table["status"] == "partial"
    assert table["rows"][1]["manager_valuation"] is None
    assert table["rows"][1]["null_reasons"]["manager_valuation"] == "unreadable"


def test_transcription_failure_does_not_drop_text_page_or_structured_data(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "scan", "scan"]))
    calls = []

    def gateway(**kwargs):
        calls.append(kwargs["task_name"])
        if kwargs["task_name"].endswith("_fields"):
            return SimpleNamespace(parsed=model_report())
        if calls.count("financial_manager_report_transcription") == 1:
            return SimpleNamespace(parsed={"pages": [{"page": 2, "text": "a", "status": "readable"}]})
        return SimpleNamespace(parsed={"pages": [
            {"page": 3, "text": None, "status": "unreadable"}, {"page": 2, "text": "PART", "status": "partial"},
        ]})

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    assert calls.count("financial_manager_report_transcription") == 2
    assert "NATIVE PAGE 1" in fields["_raw_text"] and "[Страница 2]\nPART" in fields["_raw_text"]
    assert "[Страница 3]\n[Текст страницы не получен]" in fields["_raw_text"]
    assert fields["case_number"]["value"] == "А01-42/2026"
    assert fields["report_text_complete"]["value"] is False
    assert [e.field_name for e in service.field_issues(fields, profile)] == ["report_text_complete"]


def test_gateway_failure_leaves_fields_unknown_but_returns_native_text(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native"]))
    gateway = Mock(return_value=None)
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    assert fields["case_number"]["value"] is None and fields["creditor_register"]["value"] is None
    assert fields["report_text_complete"]["value"] is True and "NATIVE PAGE 1" in fields["_raw_text"]
    assert service.field_issues(fields, profile)
    assert gateway.call_count == 2
    assert {call.kwargs["task_name"] for call in gateway.call_args_list} == {"financial_manager_report_fields"}


@pytest.mark.parametrize("initial", ["no_response", "empty_object", "non_object"])
def test_gateway_recovers_required_fields_after_empty_response(tmp_path, monkeypatch, initial):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "native"]))
    empty = {"no_response": None, "empty_object": SimpleNamespace(parsed={}), "non_object": SimpleNamespace(parsed=[])}
    gateway = Mock(side_effect=[empty[initial], SimpleNamespace(parsed=model_report())])
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    assert gateway.call_count == 2
    assert [call.kwargs["task_name"] for call in gateway.call_args_list] == ["financial_manager_report_fields"] * 2
    assert all(call.kwargs["models"] == [MODEL] for call in gateway.call_args_list)
    assert fields["case_number"]["value"] == "А01-42/2026"
    assert fields["current_payments"]["value"]["status"] == "listed"
    assert not service.field_issues(fields, profile)


def test_missing_required_values_recovered_selectively_through_api(local_api, monkeypatch):
    client, worker, _, webhook = local_api
    full = model_report()
    original = model_report()
    original.pop("case_number")
    original.pop("debtor_inn")
    original.pop("inventory_date")
    original["procedure_type"] = fact()
    original["report_date"] = fact("11.19.2026", src(1, "Дата отчёта 19.11.2026"))
    original["property_inventory"] = dict(status="not_found", source=None, rows=[])
    original["creditor_register"] = dict(status="unreadable", source=None, rows=[])
    original["property_sales"] = dict(status="none_declared", source=src(2, "Продаж не было"), rows=[])
    missing = {"case_number", "procedure_type", "report_date", "property_inventory", "creditor_register"}
    calls = []

    def gateway(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(parsed=original)
        assert kwargs["task_name"] == "financial_manager_report_required_retry"
        schema = json.loads(kwargs["system_prompt"].rsplit("\nJSON Schema ответа:\n", 1)[1])
        assert set(schema["properties"]) == missing
        assert kwargs["models"] == [MODEL] and kwargs["provider"] == "router_ai"
        assert len(kwargs["image_parts"]) == 2
        return SimpleNamespace(parsed={
            **{key: full[key] for key in missing},
            "debtor_full_name": fact("Другое имя", src(1, "Другое имя")),
        })

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    response = client.post("/documents/upload", data={
        "source_type": "external", "document_type": service.PROFILE_ID, "idempotency_key": "required-retry",
        "webhook_url": "https://example.invalid/hook",
    }, files={"file": ("report.pdf", pdf_bytes(["native", "native"]), "application/pdf")})
    assert response.status_code == 202
    asyncio.run(worker._process_pipeline_run_impl(response.json()["pipeline_run_id"]))
    result = client.get(f"/documents/{response.json()['document_id']}/result").json()
    assert len(calls) == 2
    assert result["fields"] == webhook.await_args.args[1]["fields"]
    assert result["human_review_required"] is False
    fields = result["fields"]
    assert fields["case_number"]["value"] == "А01-42/2026"
    assert fields["procedure_type"]["value"] == "РИ"
    assert fields["report_date"]["value"] == "2026-11-19"
    assert fields["property_inventory"]["value"]["status"] == "listed"
    assert fields["creditor_register"]["value"]["status"] == "listed"
    assert fields["property_sales"]["value"]["status"] == "none_declared"
    assert fields["debtor_full_name"]["value"] == full["debtor_full_name"]["value"]
    assert fields["debtor_inn"]["value"] is None


@pytest.mark.parametrize("returned", ["complete", "partial", "gateway", "unsupported"])
def test_selective_required_retry_reports_remaining_gaps(tmp_path, monkeypatch, returned):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "native"]))
    original = model_report()
    original["debtor_full_name"] = fact(reason="unreadable")
    original.pop("current_payments")
    original["property_sales"] = dict(status="partial", source=None, rows=[])
    if returned == "complete":
        complete = model_report()
        reply = SimpleNamespace(parsed={
            name: complete[name] for name in ("debtor_full_name", "property_sales", "current_payments")
        })
    elif returned == "partial":
        reply = SimpleNamespace(parsed={"property_sales": model_report()["property_sales"]})
    elif returned == "unsupported":
        reply = SimpleNamespace(parsed={"debtor_full_name": fact("Тестов", src(3, "Тестов"))})
    else:
        reply = None
    gateway = Mock(side_effect=[SimpleNamespace(parsed=original), reply])
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    assert gateway.call_count == 2
    schema = json.loads(gateway.call_args.kwargs["system_prompt"].rsplit("\nJSON Schema ответа:\n", 1)[1])
    assert set(schema["properties"]) == {"debtor_full_name", "property_sales", "current_payments"}
    assert fields["case_number"]["value"] == "А01-42/2026"
    if returned == "complete":
        assert fields["debtor_full_name"]["value"] == "Тестов Тест Тестович"
        assert fields["current_payments"]["value"]["status"] == "listed"
        assert not service.field_issues(fields, profile)
        return
    assert fields["debtor_full_name"]["value"] is None
    assert fields["debtor_full_name"]["reasoning"] == "Фрагмент или ячейка не читается."
    assert fields["current_payments"]["value"] is None
    expected = {"debtor_full_name", "current_payments"}
    if returned != "partial":
        expected.add("property_sales")
    assert {issue.field_name for issue in service.field_issues(fields, profile)} == expected
    assert "NATIVE PAGE 1" in fields["_raw_text"]


def test_blank_page_and_printed_inn_are_preserved(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "blank"]))
    document = model_report()
    document["debtor_inn"] = fact("012345678901", src(1, "ИНН должника 012345678901"))
    gateway = Mock(side_effect=[SimpleNamespace(parsed=document), SimpleNamespace(parsed={
        "pages": [{"page": 2, "text": "", "status": "blank"}],
    })])
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    fields = asyncio.run(service.extract_fields(str(path), load_profile(service.PROFILE_ID)))
    assert fields["report_text_complete"]["value"] is True
    assert fields["_raw_text"].endswith("[Страница 2]\n")
    assert fields["debtor_inn"]["value"] == "012345678901"
    assert fields["debtor_inn"]["evidence"]["page"] == 1


def test_pdf_with_no_readable_pages_is_rejected_before_the_model(tmp_path, monkeypatch):
    path = tmp_path / "protected.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="private", user_pw="private")
    gateway = Mock()
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    with pytest.raises(ValueError, match="доступный для чтения PDF"):
        asyncio.run(service.extract_fields(str(path), load_profile(service.PROFILE_ID)))
    gateway.assert_not_called()


def test_malformed_extraction_response_is_retried_once_with_argon(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native"]))
    gateway = Mock(return_value=SimpleNamespace(parsed={"report_date": "19.11.2026"}))
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    fields = asyncio.run(service.extract_fields(str(path), load_profile(service.PROFILE_ID)))
    assert gateway.call_count == 2
    assert all(call.kwargs["models"] == [MODEL] for call in gateway.call_args_list)
    assert fields["case_number"]["value"] is None and fields["case_number"]["validation_issue"]
    assert fields["report_text_complete"]["value"] is True


def test_profile_with_an_invalid_model_is_rejected_before_processing(monkeypatch):
    profile = dict(load_profile(service.PROFILE_ID), models={"llm_extraction": {
        "provider": "router_ai", "model": " ",
    }})
    gateway = Mock()
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    with pytest.raises(ValueError, match="models.llm_extraction.model"):
        asyncio.run(service.extract_fields("unused.pdf", profile))
    gateway.assert_not_called()


def test_report_uses_configured_model_for_extraction_and_transcription(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "scan"]))
    model = "vendor/report-vision"
    profile = dict(load_profile(service.PROFILE_ID), models={"llm_extraction": {
        "provider": "router_ai", "model": model,
    }})
    gateway = Mock(side_effect=[
        SimpleNamespace(parsed=model_report()),
        SimpleNamespace(parsed={"pages": [{"page": 2, "text": "SCANNED PAGE 2", "status": "readable"}]}),
    ])
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    assert [call.kwargs["models"] for call in gateway.call_args_list] == [[model], [model]]
    assert fields["report_text_complete"]["value"] is True
    assert fields["debtor_full_name"]["value"]


def test_missing_required_value_and_table_source_remain_visible_to_review():
    document = model_report()
    document["report_date"] = fact()
    sale = document["property_sales"]["rows"][0]
    sale["name"]["source"] = src(2, None, table="Реализация имущества")
    sale["contract_price"]["source"]["quote"] = "273.113,00 руб."
    profile = load_profile(service.PROFILE_ID)
    fields = service.build_fields(FinancialManagerReport.model_validate(document), 2, profile)
    date = fields["report_date"]
    assert date["value"] is None and date["validation_issue"] == "В отчёте не указано."
    item = fields["property_sales"]["value"]["rows"][0]
    assert item["name"] is None and item["null_reasons"]["name"] == "unsupported_source"
    assert item["contract_price"] == "273113.00"

    # Older or repaired stored results may lack the explicit per-field issue.
    date.pop("validation_issue")
    issues = {issue.field_name: issue.message for issue in service.field_issues(fields, profile)}
    assert issues["report_date"] == "В отчёте не указано."


def test_transcription_gateway_failure_keeps_failed_page_marker(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "scan"]))
    gateway = Mock(side_effect=[SimpleNamespace(parsed=model_report()), None, None])
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    fields = asyncio.run(service.extract_fields(str(path), load_profile(service.PROFILE_ID)))
    assert gateway.call_count == 3
    assert fields["report_text_complete"]["value"] is False
    assert fields["_raw_text"].endswith("[Страница 2]\n[Текст страницы не получен]")
    assert fields["creditor_register"]["value"]["status"] == "listed"


def test_transcription_recovers_page_after_empty_response(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(["native", "scan"]))
    gateway = Mock(side_effect=[
        SimpleNamespace(parsed=model_report()), None,
        SimpleNamespace(parsed={"pages": [{"page": 2, "text": "SCANNED PAGE 2", "status": "readable"}]}),
    ])
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    profile = load_profile(service.PROFILE_ID)
    fields = asyncio.run(service.extract_fields(str(path), profile))
    assert [call.kwargs["task_name"] for call in gateway.call_args_list] == [
        "financial_manager_report_fields", "financial_manager_report_transcription",
        "financial_manager_report_transcription",
    ]
    assert len(gateway.call_args.kwargs["image_parts"]) == 1
    assert "[Страница 2]\nSCANNED PAGE 2" in fields["_raw_text"]
    assert fields["report_text_complete"]["value"] is True
    assert not service.field_issues(fields, profile)
