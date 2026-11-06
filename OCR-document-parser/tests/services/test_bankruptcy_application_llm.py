from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import bankruptcy_application_llm as llm
from ocr_platform.services.document_intel_service import simple_extract_fields
from ocr_platform.services.validation_service import profile_field_issues, review_requirement

HEADER = ("Должник: Тестов Иван Иванович, 01.02.1990 г.р., место рождения: г. Тест. "
          "Паспорт: 01 02 001234, выдан МВД по Тестовой области, дата выдачи 03.04.2010, "
          "код подразделения 001-002, адрес регистрации: 123456, Тестовая область, "
          "гор. Тестовый, туп. Промывочный, д. 1, кв. 2.")
TEXT = "Арбитражный суд.\n" + HEADER + "\nАдрес для почтовой корреспонденции: 999999, г. Чужой.\nЗаявление\nТело документа."
VALUES = {"passport_series": "0102", "passport_number": "001234", "last_name": "Тестов",
          "first_name": "Иван", "patronymic": "Иванович", "gender": "Мужской",
          "birth_date": "01.02.1990", "birth_place": "Г. ТЕСТ", "issue_date": "03.04.2010",
          "department_code": "001-002", "issued_by": "МВД ПО ТЕСТОВОЙ ОБЛАСТИ",
          "registration_address": "123456, Тестовая область, гор. Тестовый, туп. Промывочный, д. 1, кв. 2",
          "post_index": "123456", "region": "Тестовая область", "city": "гор. Тестовый",
          "street": "туп. Промывочный"}


def answer(call, *, values=None, header=HEADER, kind="russian"):
    values = {**VALUES, **(values or {})}
    result = {}
    for name in properties(call):
        if name == "debtor_header":
            result[name] = header
        elif name == "passport_kind":
            result[name] = {"value": kind, "evidence": header}
        else:
            result[name] = {"value": values[name], "confidence": 0.93, "evidence": header if values[name] else "",
                            "reasoning": "По исходной шапке."}
    return SimpleNamespace(parsed=result, winner_model="google/gemini-4-argon")


def properties(call):
    return set(call["response_schema"]["json_schema"]["schema"]["properties"])


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("inf"), float("nan"), True, "0.95", None])
def test_confidence_rejects_invalid_model_scores(confidence):
    with pytest.raises(ValidationError):
        llm.FieldEvidence(value="Тест", confidence=confidence, evidence="Тест", reasoning="Цитата.")


def test_confidence_is_required_in_model_response():
    with pytest.raises(ValidationError):
        llm.FieldEvidence(value=None, evidence="", reasoning="Отсутствует.")


@pytest.mark.asyncio
async def test_per_field_scores_including_zero_and_confirmed_absence_do_not_trigger_retries(monkeypatch):
    calls = []
    scores = {name: i / (len(VALUES)-1) for i, name in enumerate(VALUES)}
    scores["post_index"] = 1.0
    def provider(**kwargs):
        calls.append(kwargs)
        result = answer(kwargs, values={"post_index": None})
        for name, score in scores.items():
            result.parsed[name]["confidence"] = score
        return result
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT, load_profile("bankruptcy_application"))
    assert len(calls) == 1
    assert {n: f["confidence"] for n, f in fields.items()} == scores
    assert fields["passport_series"]["confidence"] == 0
    assert fields["post_index"]["value"] is None and fields["post_index"]["confidence"] == 1
    assert all(f["model_confidence"] == scores[n] for n, f in fields.items())


@pytest.mark.asyncio
async def test_header_quote_accepts_space_in_place_of_hyphenated_line_break(monkeypatch):
    source_header = HEADER.replace("Тестовая область,", "Тестовая область, р-\nн. Тестовый,")
    quote = source_header.replace("\n", " ")
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return answer(kwargs, header=quote, values={"registration_address": VALUES["registration_address"].replace(
            "Тестовая область,", "Тестовая область, р-н. Тестовый,")})
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT.replace(HEADER, source_header), load_profile("bankruptcy_application"))
    assert len(calls) == 1
    assert fields["last_name"]["value"] == "Тестов"
    assert "р-н. Тестовый" in fields["registration_address"]["value"]
    with pytest.raises(ValueError):
        llm._validated_header(quote.replace("Тестов Иван", "Чужой Иван"), source_header)


@pytest.mark.asyncio
async def test_dispatch_uses_only_argon_and_never_local_parser(monkeypatch):
    from ocr_platform.services import bankruptcy_application
    monkeypatch.setattr(bankruptcy_application, "extract_fields", lambda *a: pytest.fail("Local fallback forbidden"))
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return answer(kwargs)
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await simple_extract_fields(TEXT, load_profile("bankruptcy_application"), "bankruptcy_application", "run", "doc")
    assert len(calls) == 1 and calls[0]["models"] == ["google/gemini-4-argon"]
    assert calls[0]["document_id"] == "doc" and calls[0]["pipeline_run_id"] == "run"
    assert {name: info["value"] for name, info in fields.items()} == VALUES
    assert all(info["source"] == "bankruptcy_llm" and info["confidence"] == 0.93 for info in fields.values())
    assert fields["passport_number"]["passport_kind"] == "russian"


@pytest.mark.asyncio
async def test_only_required_empty_fields_get_three_retries(monkeypatch):
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return answer(kwargs, values={"issue_date": None, "patronymic": None, "post_index": None})
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT, load_profile("bankruptcy_application"))
    assert len(calls) == 4
    assert all(properties(call) == {"issue_date"} for call in calls[1:])
    assert all(call["user_content"] == HEADER for call in calls[1:])
    assert fields["issue_date"]["value"] is None and fields["issue_date"]["empty_field_retries"] == 3
    assert "empty_field_retries" not in fields["patronymic"]
    assert fields["passport_number"]["value"] == "001234"
    assert review_requirement(1.0, profile_field_issues(fields, "bankruptcy_application"))[0]


@pytest.mark.asyncio
async def test_retry_fills_only_missing_and_keeps_previous_values(monkeypatch):
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        result = answer(kwargs, values={"issue_date": None} if len(calls) == 1 else {})
        for name in properties(kwargs) & VALUES.keys():
            result.parsed[name]["confidence"] = 0.24 if len(calls) == 1 else 0.87
        return result
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT, load_profile("bankruptcy_application"))
    assert len(calls) == 2 and properties(calls[1]) == {"issue_date"}
    assert fields["issue_date"]["value"] == "03.04.2010"
    assert fields["passport_number"]["value"] == "001234"
    assert fields["passport_number"]["confidence"] == 0.24
    assert fields["issue_date"]["confidence"] == fields["issue_date"]["model_confidence"] == 0.87


@pytest.mark.asyncio
async def test_foreign_number_and_null_series_do_not_cause_retries(monkeypatch):
    header = HEADER.replace("Паспорт: 01 02 001234", "Иностранный паспорт: AB-0123456789")
    text = TEXT.replace(HEADER, header)
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return answer(kwargs, header=header, kind="foreign",
                      values={"passport_series": None, "passport_number": "AB-0123456789", "department_code": None})
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(text, load_profile("bankruptcy_application"))
    assert len(calls) == 1
    assert fields["passport_series"]["value"] is None
    assert fields["passport_number"]["value"] == "AB-0123456789"
    assert profile_field_issues(fields, "bankruptcy_application") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "wrong_header", "fabricated_date"])
async def test_bad_model_output_is_reviewed_without_local_fallback(monkeypatch, failure):
    from ocr_platform.services import bankruptcy_application
    monkeypatch.setattr(bankruptcy_application, "extract_fields", lambda *a: pytest.fail("Local fallback forbidden"))
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        if failure == "empty":
            return None
        if failure == "wrong_header":
            return answer(kwargs, header="Кредитор: ООО Чужой.")
        return answer(kwargs, values={"issue_date": "03.04.2011"})
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT, load_profile("bankruptcy_application"))
    assert len(calls) == 4
    assert fields["issue_date"]["value"] is None
    assert review_requirement(1.0, profile_field_issues(fields, "bankruptcy_application"))[0]


@pytest.mark.asyncio
async def test_optional_address_from_correspondence_is_rejected_without_retry(monkeypatch):
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        response = answer(kwargs)
        response.parsed["post_index"] = {"value": "999999", "evidence": "Адрес для почтовой корреспонденции: 999999, г. Чужой.",
                                         "confidence": 0.99, "reasoning": "Ошибочная роль адреса."}
        return response
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT, load_profile("bankruptcy_application"))
    assert len(calls) == 1 and fields["post_index"]["value"] is None
    assert fields["post_index"]["validation_issue"]
    assert fields["post_index"]["confidence"] is None
    assert fields["post_index"]["model_confidence"] == 0.99


@pytest.mark.asyncio
async def test_retry_cannot_rewrite_filled_fields(monkeypatch):
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        response = answer(kwargs, values={"issue_date": None} if len(calls) == 1 else {})
        if len(calls) > 1:
            response.parsed["passport_number"] = deepcopy(response.parsed["issue_date"])
        return response
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT, load_profile("bankruptcy_application"))
    assert len(calls) == 4
    assert fields["passport_number"]["value"] == "001234"
    assert fields["issue_date"]["value"] is None


@pytest.mark.asyncio
async def test_vision_retries_keep_original_images_and_only_required_fields(monkeypatch):
    images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,test"}}]
    render_calls = []
    def render(path):
        render_calls.append(path)
        return images
    monkeypatch.setattr(llm, "_document_images", render)
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return answer(kwargs, values={"issue_date": None, "post_index": None})
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields("", load_profile("bankruptcy_application"), storage_path="scan.pdf")
    assert render_calls == ["scan.pdf"]
    assert len(calls) == 4 and all(c["image_parts"] is images for c in calls)
    assert all(properties(c) == {"issue_date"} for c in calls[1:])
    assert fields["passport_number"]["value"] == "001234"
    assert fields["passport_number"]["input_mode"] == "vision"
    assert fields["passport_number"]["validation_issue"]
    assert fields["_raw_text"] == HEADER
    assert "empty_field_retries" not in fields["post_index"]


@pytest.mark.asyncio
async def test_text_mode_never_renders_or_attaches_images(monkeypatch):
    monkeypatch.setattr(llm, "_document_images", lambda *a: pytest.fail("Native text must not be rendered"))
    def provider(**kwargs):
        assert kwargs["image_parts"] is None
        return answer(kwargs)
    monkeypatch.setattr(llm, "call_llm_json_with_fallback", provider)
    fields = await llm.extract_fields(TEXT, load_profile("bankruptcy_application"), storage_path="source.pdf")
    assert fields["passport_number"]["input_mode"] == "text" and "_raw_text" not in fields
