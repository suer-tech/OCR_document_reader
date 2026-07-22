import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ocr_platform.orchestration.router import resolve_profile
from ocr_platform.services.extraction_agent import (
    run_agent_extraction,
    PassportMainResult,
    PassportRegistrationResult,
)
from ocr_platform.services import extraction_agent


class MockPart:
    def __init__(self, tool_name):
        self.tool_name = tool_name


class MockMessage:
    def __init__(self, parts):
        self.parts = parts


def make_mock_result(data):
    mock_res = MagicMock()
    mock_res.data = data
    mock_res.all_messages.return_value = []
    return mock_res


def test_resolve_profile_passport_main():
    res = resolve_profile(
        source_type="crm",
        requested_document_type="passport_main",
    )
    assert res.profile_id == "passport_main"
    assert res.document_type == "passport_main"


def test_resolve_profile_passport_registration():
    res = resolve_profile(
        source_type="crm",
        requested_document_type="passport_registration",
    )
    assert res.profile_id == "passport_registration"
    assert res.document_type == "passport_registration"


def test_resolve_profile_passport_legacy_default():
    res = resolve_profile(
        source_type="crm",
        requested_document_type="passport",
    )
    assert res.profile_id == "passport_main"
    assert res.document_type == "passport_main"


def test_resolve_profile_passport_legacy_registration():
    res = resolve_profile(
        source_type="crm",
        requested_document_type="passport",
        page_type="registration",
    )
    assert res.profile_id == "passport_registration"
    assert res.document_type == "passport_registration"


@pytest.mark.asyncio
async def test_passport_main_extraction():
    mock_run = AsyncMock()
    mock_run.return_value = make_mock_result(
        PassportMainResult(
            passport_series="4510",
            passport_series_confidence=0.95,
            passport_series_reasoning="Found 4510",
            passport_number="123456",
            passport_number_confidence=0.95,
            passport_number_reasoning="Found 123456",
            last_name="Иванов",
            last_name_confidence=0.9,
            last_name_reasoning="Found surname",
            first_name="Иван",
            first_name_confidence=0.9,
            first_name_reasoning="Found name",
            patronymic="Иванович",
            patronymic_confidence=0.9,
            patronymic_reasoning="Found patronymic",
            gender="Мужской",
            gender_confidence=0.9,
            gender_reasoning="Found gender",
            birth_date="01.01.1990",
            birth_date_confidence=0.9,
            birth_date_reasoning="Found birth date",
            birth_place="г. Москва",
            birth_place_confidence=0.9,
            birth_place_reasoning="Found birth place",
            issue_date="10.05.2010",
            issue_date_confidence=0.9,
            issue_date_reasoning="Found issue date",
            department_code="770-001",
            department_code_confidence=0.9,
            department_code_reasoning="Found dept code",
            issued_by="ТП №1 ОУФМС",
            issued_by_confidence=0.9,
            issued_by_reasoning="Found issued by",
            has_text_distortions=False,
        )
    )

    fields_config = {
        "passport_series": {"extraction_method": "llm", "prompt_instruction": "Серия"},
        "passport_number": {"extraction_method": "llm", "prompt_instruction": "Номер"},
        "last_name": {"extraction_method": "llm", "prompt_instruction": "Фамилия"},
    }

    with patch.object(extraction_agent.agent_passport_main_combined, "run", mock_run):
        res = await run_agent_extraction(
            "Паспорт 4510 123456 Иванов Иван Иванович",
            fields_config,
            profile_id="passport_main",
            profile_config={"models": {"llm_extraction": {"model": "test-model"}}},
        )
        assert res["passport_series"]["value"] == "4510"
        assert res["passport_number"]["value"] == "123456"
        assert res["last_name"]["value"] == "Иванов"
        assert res["passport_series"]["source"] == "passport_main_combined"


@pytest.mark.asyncio
async def test_passport_registration_extraction():
    mock_run = AsyncMock()
    mock_run.return_value = make_mock_result(
        PassportRegistrationResult(
            registration_address="г. Москва, ул. Тверская, д. 1, кв. 10",
            registration_address_confidence=0.95,
            registration_address_reasoning="Found address",
            has_text_distortions=False,
        )
    )

    fields_config = {
        "registration_address": {
            "extraction_method": "llm",
            "prompt_instruction": "Адрес регистрации",
        },
    }

    with patch.object(
        extraction_agent.agent_passport_registration_combined, "run", mock_run
    ):
        res = await run_agent_extraction(
            "Зарегистрирован: г. Москва, ул. Тверская, д. 1, кв. 10",
            fields_config,
            profile_id="passport_registration",
            profile_config={"models": {"llm_extraction": {"model": "test-model"}}},
        )
        assert (
            res["registration_address"]["value"]
            == "г. Москва, ул. Тверская, д. 1, кв. 10"
        )
        assert res["registration_address"]["source"] == "passport_registration_combined"
