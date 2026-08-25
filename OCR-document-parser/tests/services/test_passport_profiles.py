import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ocr_platform.orchestration.router import load_profile, resolve_profile
from ocr_platform.services.extraction_agent import (
    GenericFieldResult,
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


def test_passport_registration_street_contains_only_street_name():
    street = load_profile("passport_registration")["fields"]["street"]

    assert street["label_ru"] == "Улица"
    assert "ТОЛЬКО тип и название улицы" in street["prompt_instruction"]
    assert "Не включай номер дома" in street["prompt_instruction"]


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


@pytest.mark.asyncio
async def test_passport_registration_forces_postal_index_tool_when_address_has_no_index():
    address = "г. Москва, ул. Тверская, д. 1, кв. 10"
    combined_run = AsyncMock(
        return_value=make_mock_result(
            PassportRegistrationResult(
                registration_address=address,
                registration_address_confidence=0.95,
                registration_address_reasoning="Found address",
                has_text_distortions=False,
            )
        )
    )
    generic_run = AsyncMock(
        return_value=make_mock_result(
            GenericFieldResult(
                value="999999",
                confidence=0.8,
                reasoning="Model guessed an index without calling the tool",
            )
        )
    )
    fields_config = {
        "registration_address": {
            "extraction_method": "llm",
            "prompt_instruction": "Адрес регистрации",
        },
        "post_index": {
            "extraction_method": "llm",
            "prompt_instruction": "Почтовый индекс",
        },
    }

    with (
        patch.object(
            extraction_agent.agent_passport_registration_combined,
            "run",
            combined_run,
        ),
        patch.object(extraction_agent.agent_generic, "run", generic_run),
        patch.object(
            extraction_agent,
            "search_postal_index_by_address",
            return_value="Found postal index: 123456",
        ) as postal_index_tool,
    ):
        res = await run_agent_extraction(
            "Зарегистрирован: г. Москва, ул. Тверская, д. 1, кв. 10",
            fields_config,
            profile_id="passport_registration",
            profile_config={"models": {"llm_extraction": {"model": "test-model"}}},
        )

    postal_index_tool.assert_called_once_with(None, address)
    assert res["post_index"] == {
        "value": "123456",
        "confidence": 0.9,
        "reasoning": (
            "Forced postal-index search by registration address "
            f"'{address}' returned 123456."
        ),
        "source": "tool_fallback",
    }


@pytest.mark.asyncio
async def test_passport_address_parts_use_only_combined_registration_address():
    address = (
        "Республика Башкортостан, г. Салават, ул. Чапаева, дом 17А, кв. 3"
    )
    combined_run = AsyncMock(
        return_value=make_mock_result(
            PassportRegistrationResult(
                registration_address=address,
                registration_address_confidence=0.95,
                registration_address_reasoning="Found current registration address",
                has_text_distortions=False,
            )
        )
    )
    generic_run = AsyncMock(
        side_effect=[
            make_mock_result(
                GenericFieldResult(
                    value="Республика Башкортостан",
                    confidence=0.9,
                    reasoning="Derived from registration_address",
                )
            ),
            make_mock_result(
                GenericFieldResult(
                    value="г. Салават",
                    confidence=0.9,
                    reasoning="Derived from registration_address",
                )
            ),
            make_mock_result(
                GenericFieldResult(
                    value="ул. Чапаева",
                    confidence=0.9,
                    reasoning="Derived from registration_address",
                )
            ),
        ]
    )
    fields_config = {
        "registration_address": {
            "extraction_method": "llm",
            "prompt_instruction": "Адрес регистрации",
        },
        "region": {"extraction_method": "llm", "prompt_instruction": "Регион"},
        "city": {"extraction_method": "llm", "prompt_instruction": "Город"},
        "street": {"extraction_method": "llm", "prompt_instruction": "Улица"},
    }
    raw_text_with_previous_address = (
        "Предыдущая регистрация: ул. Летчиков, д. 6. "
        "Текущая регистрация: ул. Чапаева, д. 17А."
    )

    with (
        patch.object(
            extraction_agent.agent_passport_registration_combined,
            "run",
            combined_run,
        ),
        patch.object(extraction_agent.agent_generic, "run", generic_run),
    ):
        res = await run_agent_extraction(
            raw_text_with_previous_address,
            fields_config,
            profile_id="passport_registration",
            profile_config={"models": {"llm_extraction": {"model": "test-model"}}},
        )

    assert res["street"]["value"] == "ул. Чапаева"
    assert generic_run.await_count == 3
    for call in generic_run.await_args_list:
        assert call.kwargs["deps"] == address
        assert address in call.args[0]
        assert "Летчиков" not in call.args[0]
