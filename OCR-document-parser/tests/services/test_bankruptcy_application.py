import asyncio
import base64
from contextlib import nullcontext
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ocr_platform.orchestration.router import load_profile, resolve_profile
from ocr_platform.services import bankruptcy_application as app
from ocr_platform.services.document_content_validation import validate_document_content
from ocr_platform.services.docx_service import extract_docx_text
from ocr_platform.services.ocr_service import extract_text_at_ingest
from ocr_platform.services.validation_service import profile_field_issues, review_requirement, validate_fields


HEADER = """В Арбитражный суд Тестовой области
123000, г. Судебный, ул. Судебная, д. 99
Должник: Тестова (Прежняя) Анна Ивановна, 01.02.1990 г.р.
Паспорт: 01 02 001234, выдан Отделом № 3 МВД Тестовой области,
дата выдачи 03.04.2010, код подразделения 001-002
Место рождения: гор. Родной Тестовой области
Место жительство по регистрации: 123456, Тестовая область, г. Тестовый,
ул. 8 Марта, д. 3, кв. 4
Фактическое проживание: 999999, г. Другой, ул. Чужая, д. 90
В лице Представителя: Иванов Иван Иванович, паспорт 9911 999999
Кредиторы: ООО Кредитор, 654321, г. Чужой
Заявление должника о признании его несостоятельным (банкротом)
Я, Тестова Анна Ивановна, паспорт 9999 888888, адрес регистрации: г. Ошибка.
"""


def extract(text=HEADER):
    return app.extract_fields(text, load_profile(app.PROFILE_ID))


def vals(fields):
    return {k: v["value"] for k, v in fields.items()}


def docx_bytes(text=HEADER, *, xml=None):
    from xml.sax.saxutils import escape
    if xml is None:
        paragraphs = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in text.splitlines())
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               f'<w:body>{paragraphs}</w:body></w:document>')
    buf = io.BytesIO()
    with ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_profile_matches_passport_union_and_uses_argon():
    profile = load_profile(app.PROFILE_ID)
    assert set(profile["fields"]) == (set(load_profile("passport_main")["fields"]) |
                                     set(load_profile("passport_registration")["fields"]))
    assert resolve_profile("external", app.PROFILE_ID).profile_id == app.PROFILE_ID
    assert profile["models"]["ocr"] == {"pdf_text_engine": "pymupdf", "direct_vision_fallback": True}
    assert profile["extraction_mode"] == "llm_header"
    assert profile["models"]["llm_extraction"]["model"] == "google/gemini-4-argon"


def test_header_only_and_address_provenance():
    fields = extract()
    assert vals(fields) == {
        "passport_series": "0102", "passport_number": "001234", "last_name": "Тестова",
        "first_name": "Анна", "patronymic": "Ивановна", "gender": "Женский",
        "birth_date": "01.02.1990", "birth_place": "ГОР. РОДНОЙ ТЕСТОВОЙ ОБЛАСТИ",
        "issue_date": "03.04.2010", "department_code": "001-002",
        "issued_by": "ОТДЕЛОМ № 3 МВД ТЕСТОВОЙ ОБЛАСТИ",
        "registration_address": "123456, Тестовая область, г. Тестовый, ул. 8 Марта, д. 3, кв. 4",
        "post_index": "123456", "region": "Тестовая область", "city": "г. Тестовый", "street": "ул. 8 Марта",
    }
    assert fields["gender"]["source"] == "fio_inference"
    assert "Прежняя" in fields["last_name"]["reasoning"]
    assert all(x["confidence"] is None for x in fields.values())
    assert validate_fields(fields, app.PROFILE_ID, load_profile(app.PROFILE_ID)) == ("ok", [])


@pytest.mark.parametrize("number", ["402371687", "AB-0123456789"])
def test_foreign_passport_is_not_truncated_or_split(number):
    text = HEADER.replace("Паспорт: 01 02 001234", "Иностранный паспорт: " + number)
    text = text.replace(", код подразделения 001-002", "")
    fields = extract(text)
    assert fields["passport_number"]["value"] == number
    assert fields["passport_series"]["value"] is None
    assert fields["department_code"]["value"] is None
    assert profile_field_issues(fields, app.PROFILE_ID) == []


@pytest.mark.parametrize("text,missing", [
    (HEADER.replace("дата выдачи 03.04.2010, ", ""), "issue_date"),
    (HEADER.replace("03.04.2010", "31.02.2010"), "issue_date"),
    (HEADER.replace("01.02.1990", "31.02.1990"), "birth_date"),
    (HEADER.replace("Место жительство по регистрации:", "Адрес для почтовой корреспонденции:"), "registration_address"),
    (HEADER.replace("Паспорт: 01 02 001234", "Паспорт: 01020012345"), "passport_number"),
])
def test_missing_or_invalid_fields_require_review_and_never_use_body(text, missing):
    fields = extract(text)
    assert fields[missing]["value"] is None
    issues = profile_field_issues(fields, app.PROFILE_ID)
    assert missing in {x.field_name for x in issues}
    assert review_requirement(1.0, issues) == (True, app.REVIEW_CODE)


@pytest.mark.parametrize("text", [
    HEADER.split("Заявление должника")[1],
    HEADER.replace("Должник:", "Представитель:"),
    HEADER.replace("Должник:", "Должник: Должник:"),
])
def test_no_unique_debtor_header_abstains(text):
    assert all(v is None for v in vals(extract(text)).values())
    assert review_requirement(1.0, profile_field_issues(extract(text), app.PROFILE_ID))[0]


@pytest.mark.parametrize("first,patr,gender", [
    ("Анна", "Ивановна", "Женский"), ("Иван", "Иванович", "Мужской"),
    ("Муроджон", "Махмуджонович", "Мужской"), ("Анна", "Иванович", None),
    ("Саша", None, None), (None, None, None),
])
def test_gender_is_inferred_conservatively(first, patr, gender):
    assert app._gender(first, patr) == gender


def test_address_ignores_rural_municipality_and_keeps_numeric_street():
    text = HEADER.replace("г. Тестовый,", "с.п. Тестовый сельсовет, д. Деревня,")
    assert extract(text)["city"]["value"] == "д. Деревня"
    assert extract(text)["street"]["value"] == "ул. 8 Марта"
    text = text.replace("123456, Тестовая область", "Тестовая область")
    assert extract(text)["post_index"]["value"] is None


@pytest.mark.parametrize("city,street", [
    ("гор. Тестовый", "туп. Промывочный"),
    ("гор.Тестовый", "тупик Промывочный"),
])
def test_address_supports_city_and_dead_end_abbreviations(city, street):
    text = HEADER.replace("г. Тестовый", city).replace("ул. 8 Марта", street)
    fields = extract(text)
    assert fields["city"]["value"] == city
    assert fields["street"]["value"] == street
    assert fields["registration_address"]["value"].endswith(street + ", д. 3, кв. 4")


@pytest.mark.parametrize("config,source,expected", [
    (None, "pdfplumber", "РеспубликаТатарстан"),
    (load_profile(app.PROFILE_ID)["models"]["ocr"], "pymupdf", "Республика Татарстан"),
])
def test_profile_pdf_reader_preserves_spaces_without_ocr(monkeypatch, tmp_path, config, source, expected):
    from ocr_platform.services import ocr_service
    path = tmp_path / "input.pdf"
    import pymupdf
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Native text layer")
        pdf.save(path)
    readers = []
    def plumber(_):
        readers.append("pdfplumber")
        return "РеспубликаТатарстан"
    def mupdf(_):
        readers.append("pymupdf")
        return "Республика Татарстан"
    monkeypatch.setattr(ocr_service, "extract_text_from_pdf", plumber)
    monkeypatch.setattr(ocr_service, "extract_text_with_pymupdf", mupdf)
    monkeypatch.setattr(ocr_service, "run_ocr_with_engine", lambda *a, **k: pytest.fail("Text layer must bypass OCR"))
    assert extract_text_at_ingest(str(path), "pdf", ocr_config=config) == (expected, False, None, source)
    assert readers == [source]


@pytest.mark.parametrize("fallback_text", ["Fallback text", ""])
def test_preferred_pdf_reader_falls_back_once_before_ocr(monkeypatch, tmp_path, fallback_text):
    from ocr_platform.services import ocr_service
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-mock")
    readers = []
    def mupdf(_):
        readers.append("pymupdf")
        return ""
    def plumber(_):
        readers.append("pdfplumber")
        return fallback_text
    def ocr(*args):
        readers.append("ocr")
        return "OCR text", "router_ai"
    monkeypatch.setattr(ocr_service, "extract_text_from_pdf", plumber)
    monkeypatch.setattr(ocr_service, "extract_text_with_pymupdf", mupdf)
    monkeypatch.setattr(ocr_service, "run_ocr_with_engine", ocr)
    text, used_ocr, _, source = extract_text_at_ingest(str(path), "pdf", ocr_config={"pdf_text_engine": "pymupdf"})
    assert readers == (["pymupdf", "pdfplumber"] if fallback_text else ["pymupdf", "pdfplumber", "ocr"])
    assert text == (fallback_text or "OCR text")
    assert used_ocr is (not bool(fallback_text))
    assert source == ("pdfplumber" if fallback_text else "router_ai")


def test_issuance_without_date_label_and_with_numbered_authority():
    text = HEADER.replace("дата выдачи 03.04.2010", "03.04.2010")
    assert extract(text)["issue_date"]["value"] == "03.04.2010"
    text = text.replace("03.04.2010, ", "").replace("ул. 8 Марта", "ул. 01.01.2000")
    assert extract(text)["issue_date"]["value"] is None


def test_conflicting_dates_or_passports_force_review():
    for text, field in [
        (HEADER.replace("Место рождения:", "дата выдачи 05.06.2011. Место рождения:"), "issue_date"),
        (HEADER.replace("Место рождения:", "Паспорт: 1122 222333, выдан ОМВД. Место рождения:"), "passport_number"),
    ]:
        assert field in {i.field_name for i in profile_field_issues(extract(text), app.PROFILE_ID)}


def test_separator_dashes_and_optional_birthplace_colon_do_not_drop_fields():
    text = HEADER.replace("Анна Ивановна", "Анна — Ивановна").replace("001234, выдан", "001234, — выдан")
    text = text.replace("Место рождения: гор.", "Место рождениягор.")
    fields = extract(text)
    assert fields["patronymic"]["value"] == "Ивановна"
    assert fields["passport_number"]["value"] == "001234"
    assert fields["birth_place"]["value"] == "ГОР. РОДНОЙ ТЕСТОВОЙ ОБЛАСТИ"


def test_docx_preserves_order_and_omits_deleted_text(tmp_path):
    content = docx_bytes(xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body><w:p><w:r><w:t>Первый</w:t></w:r><w:del><w:r><w:delText>Старый</w:delText></w:r></w:del></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Таблица</w:t><w:tab/><w:t>Ячейка</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    <w:moveFrom><w:p><w:r><w:t>Удалённый абзац</w:t></w:r></w:p></w:moveFrom>
    <w:p><w:r><w:t>Последний</w:t></w:r></w:p></w:body></w:document>''')
    path = tmp_path / "input.docx"
    path.write_bytes(content)
    assert extract_docx_text(content) == "Первый\nТаблица Ячейка\nПоследний"
    assert extract_text_at_ingest(str(path), "docx") == ("Первый\nТаблица Ячейка\nПоследний", False, None, "docx")


@pytest.mark.parametrize("content", [b"not a zip", docx_bytes(""), docx_bytes(xml="<wrong/>")])
def test_invalid_or_image_only_docx_fails_explicitly(content):
    with pytest.raises(ValueError):
        validate_document_content(content, "docx")


def test_docx_expansion_limit(monkeypatch):
    from ocr_platform.services import docx_service
    monkeypatch.setattr(docx_service, "MAX_EXPANDED_BYTES", 20)
    with pytest.raises(ValueError, match="size limit"):
        extract_docx_text(docx_bytes())


@pytest.fixture
def local_api(monkeypatch, tmp_path):
    from ocr_platform.orchestration import run_processor as worker
    from ocr_platform.storage import models, repository, file_storage
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(file_storage, "_ensure_storage_dir", lambda: tmp_path)
    from ocr_platform.api import main

    monkeypatch.setattr(main, "publish_ingest_job", MagicMock())
    tracer = SimpleNamespace(span=lambda *a, **k: nullcontext(), start_trace=lambda *a, **k: None,
                             update_trace=lambda *a, **k: None, score=lambda *a, **k: None,
                             flush=lambda: None)
    monkeypatch.setattr(worker, "get_pipeline_tracer", lambda: tracer)
    monkeypatch.setattr(worker, "mlflow_run", lambda *a: nullcontext())
    for name in ["mlflow_set_tag", "mlflow_log_param", "mlflow_log_metric", "mlflow_log_text"]:
        monkeypatch.setattr(worker, name, MagicMock())
    monkeypatch.setattr(worker, "_trigger_webhook_safely", AsyncMock())
    from ocr_platform.services import bankruptcy_application_llm as llm
    def fake_gateway(**kwargs):
        # Offline provider fixture, independent of the production LLM parser.
        # The old local parser is only a fixture factory for these storage tests.
        text = HEADER if kwargs.get("image_parts") else kwargs["user_content"]
        profile = load_profile(app.PROFILE_ID)
        values = app.extract_fields(text, profile)
        header = "Должник: " + app._header(text)
        properties = kwargs["response_schema"]["json_schema"]["schema"]["properties"]
        answer = {}
        for name in properties:
            if name == "debtor_header":
                answer[name] = header
            elif name == "passport_kind":
                answer[name] = {"value": values["passport_number"]["passport_kind"], "evidence": header}
            else:
                answer[name] = {"value": values[name]["value"], "confidence": 0.92,
                                "evidence": header if values[name]["value"] else "",
                                "reasoning": "Синтетический ответ провайдера для проверки хранения."}
        return SimpleNamespace(parsed=answer, winner_model="google/gemini-4-argon")
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", fake_gateway)
    with TestClient(main.create_app()) as client:
        yield client, worker, sessions
    engine.dispose()


@pytest.mark.parametrize("route", ["upload", "ingest"])
@pytest.mark.parametrize("case", ["russian", "foreign", "missing_date"])
def test_docx_load_worker_storage_and_result(local_api, route, case):
    client, worker, sessions = local_api
    from ocr_platform.storage import models
    text = HEADER
    if case == "foreign":
        text = text.replace("Паспорт: 01 02 001234", "Иностранный паспорт: 402371687").replace(", код подразделения 001-002", "")
    elif case == "missing_date":
        text = text.replace("дата выдачи 03.04.2010, ", "")
    content = docx_bytes(text)
    if route == "upload":
        response = client.post("/documents/upload", data={"source_type": "external", "document_type": app.PROFILE_ID,
                               "idempotency_key": "test-docx"},
                               files={"file": ("sample.docx", content, "application/octet-stream")})
    else:
        response = client.post("/documents/ingest", json={"source_type": "external", "document_type": app.PROFILE_ID,
            "content_type": "docx", "content_base64": base64.b64encode(content).decode()})
    assert response.status_code == 202, response.text
    ids = response.json()
    asyncio.run(worker._process_pipeline_run_impl(ids["pipeline_run_id"]))
    response = client.get(f'/documents/{ids["document_id"]}/result')
    assert response.status_code == 200
    data = response.json()
    assert data["fields"]["passport_number"]["value"] == ("402371687" if case == "foreign" else "001234")
    assert data["fields"]["registration_address"]["value"].startswith("123456")
    assert data["fields"]["passport_number"]["source"] == "bankruptcy_llm"
    assert data["fields"]["passport_number"]["extraction_method"] == "llm"
    assert all(data["fields"][name]["confidence"] == 0.92 for name in load_profile(app.PROFILE_ID)["fields"])
    assert data["validation_status"] == ("errors" if case == "missing_date" else "ok")
    assert data["human_review_required"] is (case == "missing_date")
    with sessions() as session:
        stored = session.query(models.DocumentFile).one()
        assert stored.file_type == "docx" and Path(stored.storage_path).suffix == ".docx"
        assert Path(stored.storage_path).read_bytes() == content
        saved = session.query(models.StructuredVersion).one().data
        assert saved["passport_number"]["model"] == "google/gemini-4-argon"
        assert saved["passport_number"]["evidence"]
        assert saved["passport_number"]["confidence"] == saved["passport_number"]["model_confidence"] == 0.92


def test_upload_rejects_invalid_docx_before_queue(local_api):
    client, worker, sessions = local_api
    response = client.post("/documents/upload", data={"source_type": "external", "document_type": app.PROFILE_ID,
                           "idempotency_key": "test-bad-docx"},
                           files={"file": ("bad.docx", b"not docx", "application/octet-stream")})
    assert response.status_code == 400


@pytest.mark.parametrize("content_type", ["pdf", "image", "mixed_pdf"])
def test_scan_routes_directly_to_argon_and_ignores_cached_text(local_api, monkeypatch, content_type):
    client, worker, sessions = local_api
    expected_pages = 2 if content_type == "mixed_pdf" else 1
    from ocr_platform.storage import models
    import pymupdf
    if content_type in {"pdf", "mixed_pdf"}:
        with pymupdf.open() as pdf:
            pdf.new_page()
            if content_type == "mixed_pdf":
                pdf.new_page().insert_text((72, 72), "Some pages have text")
            content = pdf.tobytes()
        content_type = "pdf"
    else:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
        content = buf.getvalue()
    from ocr_platform.services import router_ai_ocr_service
    gemini = MagicMock(return_value=HEADER)
    monkeypatch.setattr(router_ai_ocr_service, "run_router_ai_ocr", gemini)
    tesseract = MagicMock(side_effect=AssertionError("Tesseract must not run when Gemini succeeds"))
    monkeypatch.setattr(worker.ocr_service, "run_ocr", tesseract)
    from ocr_platform.services import bankruptcy_application_llm as llm
    argon = MagicMock(wraps=llm.call_llm_json_with_fallback)
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", argon)
    response = client.post("/documents/ingest", json={"source_type": "external", "document_type": app.PROFILE_ID,
        "content_type": content_type, "content_base64": base64.b64encode(content).decode(),
        "webhook_url": "https://callback.invalid"})
    assert response.status_code == 202
    ids = response.json()
    with sessions() as session:
        session.add(models.TextVersion(document_id=ids['document_id'], pipeline_run_id=ids['pipeline_run_id'], text=HEADER))
        session.commit()
    asyncio.run(worker._process_pipeline_run_impl(ids["pipeline_run_id"]))
    gemini.assert_not_called()
    tesseract.assert_not_called()
    argon.assert_called_once()
    assert argon.call_args.kwargs["models"] == ["google/gemini-4-argon"]
    assert len(argon.call_args.kwargs["image_parts"]) == expected_pages
    with sessions() as session:
        assert session.get(models.PipelineRun, ids['pipeline_run_id']).status == "done"
        assert session.query(models.StructuredVersion).count() == 1
        saved = session.query(models.StructuredVersion).one().data
        assert saved["passport_number"]["input_mode"] == "vision"
        assert saved["passport_number"]["prompt_version"] == llm.VISION_PROMPT_VERSION
        assert session.query(models.TextVersion).one().text.startswith("Должник:")
    result = client.get(f'/documents/{ids["document_id"]}/result').json()
    assert result["human_review_reason"] == app.REVIEW_CODE
    assert worker._trigger_webhook_safely.await_args.args[1]["status"] == "done"
