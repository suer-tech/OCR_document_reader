import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pymupdf
import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ocr_platform.orchestration import router, run_processor
from ocr_platform.services import document_intel_service, passport_vision_service as vision
from ocr_platform.storage import models


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "passport.png"
    Image.new("RGB", (80, 120), "white").save(path)
    return path


def answer(profile, **values):
    return {**dict.fromkeys(profile["fields"]), "raw_text": "ТЕСТОВ 03 06 001234", **values}


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(vision, "get_settings", lambda: SimpleNamespace(
        router_ai_api_key="test-key", router_ai_base_url="https://router.invalid/api/v1"))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock()
    monkeypatch.setattr(vision.httpx, "AsyncClient", MagicMock(return_value=client))

    def respond(data, *, status=200, finish_reason="stop"):
        client.post.return_value = httpx.Response(status, request=httpx.Request("POST", "https://router.invalid"),
            json={"choices": [{"finish_reason": finish_reason,
                               "message": {"content": data if isinstance(data, str) else json.dumps(data)}}]})
    return client, respond


@pytest.mark.asyncio
async def test_direct_image_single_request_bypasses_text_agent(picture, transport, monkeypatch):
    profile = router.load_profile("passport_main")
    client, respond = transport
    respond(answer(profile, passport_series="03 06", passport_number="001234", department_code="001002"))
    from ocr_platform.services import extraction_agent
    old_agent = AsyncMock(side_effect=AssertionError("Text agent must not run"))
    monkeypatch.setattr(extraction_agent, "run_agent_extraction", old_agent)
    fields = await document_intel_service.simple_extract_fields(
        "STALE OCR MUST NOT BE USED", profile, "passport_main", "run", "doc", str(picture))
    assert fields["passport_series"]["value"] == "0306"
    assert fields["passport_number"]["value"] == "001234"
    assert fields["department_code"]["value"] == "001-002"
    assert fields["passport_series"]["confidence"] is None
    assert fields["_raw_text"] == "ТЕСТОВ 03 06 001234"
    old_agent.assert_not_awaited()
    client.post.assert_awaited_once()
    request = client.post.await_args.kwargs["json"]
    assert request["model"] == "google/gemini-3-flash-preview"
    assert request["reasoning_effort"] == "low"
    assert request["temperature"] == 0.0
    content = request["messages"][0]["content"]
    assert len(content) == 2
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "STALE OCR" not in content[0]["text"]


@pytest.mark.asyncio
async def test_all_pdf_pages_go_in_one_request(tmp_path, transport):
    path = tmp_path / "passport.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page(width=100, height=140)
        pdf.new_page(width=100, height=140)
        pdf.save(path)
    client, respond = transport
    profile = router.load_profile("passport_main")
    respond(answer(profile))
    await vision.extract_passport_fields(str(path), profile)
    client.post.assert_awaited_once()
    parts = client.post.await_args.kwargs["json"]["messages"][0]["content"]
    assert len([p for p in parts if p["type"] == "image_url"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["not JSON", {"raw_text": "missing fields"}])
async def test_bad_output_never_retries(picture, transport, bad):
    client, respond = transport
    respond(bad)
    with pytest.raises(ValueError):
        await vision.extract_passport_fields(str(picture), router.load_profile("passport_main"))
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_truncated_response_never_retries(picture, transport):
    client, respond = transport
    profile = router.load_profile("passport_main")
    respond(answer(profile), finish_reason="length")
    with pytest.raises(ValueError, match="did not finish"):
        await vision.extract_passport_fields(str(picture), profile)
    client.post.assert_awaited_once()


@pytest.mark.parametrize("value", [306, True, {"value": "0306"}])
def test_identifier_must_be_string(value):
    profile = router.load_profile("passport_main")
    with pytest.raises(ValueError, match="must be a string"):
        vision._parse_fields(json.dumps(answer(profile, passport_series=value)), profile)


def test_invalid_digits_not_guessed():
    profile = router.load_profile("passport_main")
    result = vision._parse_fields(json.dumps(answer(profile, passport_series="О306")), profile)
    assert result["passport_series"]["value"] is None


@pytest.mark.parametrize("name,value,expected", [
    ("issued_by", "  Отделом\n УФМС\tРоссии № №2 по\u00a0Башкирской АССР ",
     "ОТДЕЛОМ УФМС РОССИИ №2 ПО БАШКИРСКОЙ АССР"),
    ("birth_place", " г. Орёл\nОрловская\tобл. ", "Г. ОРЁЛ ОРЛОВСКАЯ ОБЛ."),
    ("birth_place", "с. Ивановка , Башкирская АССР", "С. ИВАНОВКА, БАШКИРСКАЯ АССР"),
    ("registration_address", " г. Орёл\nул. Лесная д. 03 к. 2 кв. 01 ",
     "г. Орёл, ул. Лесная, д. 03, к. 2, кв. 01"),
    ("registration_address", "СНТ Берёзка, д. Ивановка, ул. 8 Марта, д. 1/3, литера А, ком. 136, 137",
     "СНТ Берёзка, д. Ивановка, ул. 8 Марта, д. 1/3, литера А, ком. 136, 137"),
    ("registration_address", "123456, г. Тест,, ул. Мира д. 1А стр. 2 помещ. 03",
     "123456, г. Тест, ул. Мира, д. 1А, стр. 2, помещ. 03"),
    ("registration_address", "г. Москва, 4-й проезд, дом 9/2", "г. Москва, 4-й проезд, дом 9/2"),
    ("registration_address", "Московская\nобласть, ул. Лесная\nПоляна, д. 3",
     "Московская область, ул. Лесная Поляна, д. 3"),
    ("birth_place", " \n ", None),
])
def test_passport_presentation_is_conservative_and_idempotent(name, value, expected):
    actual = vision._normalize_passport_value(name, value)
    assert actual == expected
    if actual:
        assert vision._normalize_passport_value(name, actual) == actual


def test_normalization_preserves_schema_raw_text_and_other_fields():
    profile = router.load_profile("passport_main")
    data = answer(profile, last_name="Михалёва", passport_series="0306",
                  issued_by="Отделом\nУФМС", birth_place="г. Орёл\nРСФСР")
    result = vision._parse_fields(json.dumps(data), profile)
    assert set(result) == set(profile["fields"]) | {"_raw_text"}
    assert result["_raw_text"] == data["raw_text"]
    assert result["last_name"]["value"] == "Михалёва"
    assert result["passport_series"]["value"] == "0306"
    assert result["issued_by"]["value"] == "ОТДЕЛОМ УФМС"
    assert result["birth_place"]["value"] == "Г. ОРЁЛ РСФСР"


@pytest.mark.parametrize("name,value,expected", [
    ("last_name", "  МИХАЛЁВА  ", "Михалёва"),
    ("last_name", "САЛТЫКОВ-ЩЕДРИН", "Салтыков-Щедрин"),
    ("first_name", "АННА\t МАРИЯ", "Анна Мария"),
    ("last_name", "Д'АРТАНЬЯН", "Д'Артаньян"),
    ("patronymic", "  ", None),
    ("patronymic", "РАХМАН ОГЛЫ", "Рахман оглы"),
    ("patronymic", "ЛЕЙЛА КЫЗЫ", "Лейла кызы"),
    ("birth_date", "29.02.2000", "29.02.2000"),
    ("birth_date", "29.02.1900", None),
    ("birth_date", "31.04.1980", None),
    ("birth_date", "1.02.1980", None),
    ("issue_date", "15.08.201", None),
    ("issue_date", "2019-08-15", None),
    ("issue_date", " 01.02.2019 ", "01.02.2019"),
    ("gender", "ЖЕН.", "Женский"),
    ("gender", " мужской ", "Мужской"),
    ("gender", "М", "Мужской"),
    ("gender", "не уверен, мужской", None),
    ("gender", "неизвестен", None),
    ("street", " ул.\n8 Марта ", "ул. 8 Марта"),
    ("street", "улица 1905 года", "улица 1905 года"),
    ("street", "4-й проезд", "4-й проезд"),
    ("street", "ул. Декабристов", "ул. Декабристов"),
    ("street", "ул. Мира, д. 03", None),
    ("street", "ул. Мира дом № 3А", None),
    ("street", "ул. Мира, корп. 2", None),
    ("street", "ул. Мира, кв: 3", None),
    ("street", "ул. Мира, кв. 3", None),
    ("street", "ул. Мира, 123456", None),
])
def test_passport_field_formats(name, value, expected):
    assert vision._normalize_passport_value(name, value) == expected


def test_rejected_values_keep_contract_and_raw_text():
    profile = router.load_profile("passport_main")
    data = answer(profile, birth_date="31.02.1980", gender="неизвестно", first_name="НАТАЛЬЯ")
    fields = vision._parse_fields(json.dumps(data), profile)
    assert fields["first_name"]["value"] == "Наталья"
    for name in ["birth_date", "gender"]:
        assert fields[name]["value"] is None
        assert "отклонено проверкой формата" in fields[name]["reasoning"]
        assert set(fields[name]) == {"value", "confidence", "reasoning", "source"}
    assert fields["_raw_text"] == data["raw_text"]
    registration = router.load_profile("passport_registration")
    address = "г. Тест, ул. 8 Марта, д. 3, кв. 4"
    fields = vision._parse_fields(json.dumps(answer(registration, registration_address=address,
                                                  street="ул. 8 Марта, д. 3")), registration)
    assert fields["street"]["value"] is None
    assert fields["registration_address"]["value"] == address


@pytest.mark.asyncio
@pytest.mark.parametrize("address,expected,lookup_count", [
    ("г. Тест, ул. Тестовая, д. 1", "123456", 1),
    ("654321, г. Тест, ул. Тестовая, д. 1", "654321", 0),
])
async def test_registration_verifies_index_without_another_model_request(
    picture, transport, monkeypatch, address, expected, lookup_count
):
    client, respond = transport
    profile = router.load_profile("passport_registration")
    respond(answer(profile, registration_address=address, post_index="999999"))
    lookup = MagicMock(return_value="Found postal index: 123456")
    monkeypatch.setattr(vision, "search_postal_index_by_address", lookup)
    fields = await vision.extract_passport_fields(str(picture), profile)
    assert fields["post_index"]["value"] == expected
    assert lookup.call_count == lookup_count
    client.post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("page_type,profile_id", [(None,"passport_main"),("registration","passport_registration")])
@pytest.mark.parametrize("response_kind", ["success", "invalid", "empty"])
async def test_worker_skips_ocr_and_cached_text_and_saves_direct_results(
    picture, transport, monkeypatch, page_type, profile_id, response_kind
):
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)

    @contextmanager
    def get_session():
        with Session(engine) as session:
            yield session

    with get_session() as session:
        session.add(models.Document(id="doc", source_type="crm", document_type="passport"))
        session.add(models.DocumentFile(document_id="doc", storage_path=str(picture), file_type="image"))
        session.add(models.PipelineRun(id="run", document_id="doc", profile_id="passport", status="queued", page_type=page_type,
                                       webhook_url="https://callback.invalid"))
        session.add(models.TextVersion(document_id="doc", pipeline_run_id="run", text="STALE OCR MUST NOT BE USED"))
        session.commit()
    monkeypatch.setattr(run_processor.repository, "get_session", get_session)
    monkeypatch.setattr(run_processor, "get_pipeline_tracer", MagicMock(return_value=MagicMock()))
    webhook = AsyncMock()
    monkeypatch.setattr(run_processor, "_trigger_webhook_safely", webhook)
    monkeypatch.setattr(run_processor, "get_settings", lambda: SimpleNamespace(ocr_engine="router_ai"))
    monkeypatch.setattr(run_processor, "mlflow_run", lambda *args: nullcontext())
    for name in ["mlflow_set_tag","mlflow_log_param","mlflow_log_metric","mlflow_log_text"]:
        monkeypatch.setattr(run_processor, name, MagicMock())
    ocr = MagicMock(side_effect=AssertionError("Separate OCR must never run"))
    monkeypatch.setattr(run_processor.ocr_service, "extract_text_at_ingest", ocr)
    monkeypatch.setattr(vision, "search_postal_index_by_address", MagicMock(return_value=""))
    profile = router.load_profile(profile_id)
    client, respond = transport
    expected_text = "" if response_kind == "empty" else "ТЕСТОВ 03 06 001234"
    respond("broken JSON" if response_kind == "invalid" else answer(profile, raw_text=expected_text))
    if response_kind == "invalid":
        with pytest.raises(ValueError, match="invalid JSON"):
            await run_processor._process_pipeline_run_impl("run")
        ocr.assert_not_called()
        client.post.assert_awaited_once()
        assert webhook.await_args.args[1]["status"] == "failed"
        with get_session() as session:
            assert session.get(models.PipelineRun, "run").status == "failed"
            assert session.scalar(select(models.StructuredVersion)) is None
        engine.dispose()
        return
    await run_processor._process_pipeline_run_impl("run")
    ocr.assert_not_called()
    client.post.assert_awaited_once()
    webhook.assert_awaited_once()
    assert webhook.await_args.args[1]["raw_text"] == expected_text
    assert webhook.await_args.args[1]["status"] == "done"
    assert "_raw_text" not in webhook.await_args.args[1]["fields"]
    with get_session() as session:
        run = session.get(models.PipelineRun, "run")
        assert run.status == "done"
        assert run.profile_id == profile_id
        stored = session.scalar(select(models.TextVersion).where(models.TextVersion.pipeline_run_id == "run"))
        assert stored.text == expected_text
        fields = session.scalar(select(models.StructuredVersion)).data
        assert "_raw_text" not in fields
        assert fields[next(iter(profile["fields"]))]["source"] in {"passport_vision","registration_address"}
        quality = session.scalar(select(models.QualityScore))
        assert quality.technical_score == (0.8 if expected_text else 0.2)
    engine.dispose()


@pytest.mark.asyncio
async def test_other_profiles_still_use_text_extraction(monkeypatch):
    from ocr_platform.services import extraction_agent
    old_agent = AsyncMock(return_value={"result": {"value": "ok"}})
    monkeypatch.setattr(extraction_agent, "run_agent_extraction", old_agent)
    profile = {"profile_id": "court_decision_ru", "fields": {"result": {}}}
    result = await document_intel_service.simple_extract_fields("original text", profile,
        "court_decision_ru", "run", "doc")
    assert result["result"]["value"] == "ok"
    old_agent.assert_awaited_once()
    assert old_agent.await_args.args[0] == "original text"
