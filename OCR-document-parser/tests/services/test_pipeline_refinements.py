import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from ocr_platform.services.extraction_agent import (
    run_agent_extraction,
    ClaimsAmountResult,
    CreditorResult,
    CreditorInnResult,
    CompanyNameResult,
    CompanyComparisonResult,
)
from ocr_platform.services import extraction_agent

class MockPart:
    def __init__(self, tool_name):
        self.tool_name = tool_name

class MockMessage:
    def __init__(self, parts):
        self.parts = parts

def make_mock_result(data, tool_called=True):
    mock_res = MagicMock()
    mock_res.data = data
    if tool_called:
        mock_res.all_messages.return_value = [MockMessage([MockPart("search_creditor_name")])]
    else:
        mock_res.all_messages.return_value = []
    return mock_res

@pytest.mark.asyncio
async def test_claims_amount_calculation_in_code():
    # Test multiple commitments: sums all amounts
    mock_run = AsyncMock()
    mock_run.return_value = make_mock_result(ClaimsAmountResult(
        commitments_count=2,
        amounts=[150000.50, 75000.25],
        confidence=0.9,
        reasoning="Найдено 2 кредитных договора"
    ))

    with patch.object(extraction_agent.agent_claims_amount, 'run', mock_run):
        fields_config = {
            "claims_amount": {
                "extraction_method": "llm_claims_amount",
                "prompt_instruction": "Extract sums"
            }
        }
        res = await run_agent_extraction("test text", fields_config)
        assert res["claims_amount"]["value"] == "225000.75"  # 150000.50 + 75000.25

    # Test single commitment: returns the single amount
    mock_run.return_value = make_mock_result(ClaimsAmountResult(
        commitments_count=1,
        amounts=[150000.50],
        confidence=0.9,
        reasoning="Найдено 1 обязательство"
    ))
    with patch.object(extraction_agent.agent_claims_amount, 'run', mock_run):
        res = await run_agent_extraction("test text", fields_config)
        assert res["claims_amount"]["value"] == "150000.50"

    # Test invalid format (null fields) returns None
    mock_run.return_value = make_mock_result(ClaimsAmountResult(
        commitments_count=None,
        amounts=None,
        confidence=0.0,
        reasoning="Не удалось определить"
    ))
    with patch.object(extraction_agent.agent_claims_amount, 'run', mock_run):
        res = await run_agent_extraction("test text", fields_config)
        assert res["claims_amount"]["value"] is None


@pytest.mark.asyncio
async def test_creditor_format_validation_and_retry():
    mock_run = AsyncMock()
    # 1st attempt: raise ValueError to simulate validation exception
    # 2nd attempt: return valid CreditorResult
    mock_run.side_effect = [
        ValueError("Invalid structure format from LLM"),
        make_mock_result(CreditorResult(
            creditor_name="ООО Ромашка",
            creditor_name_web=None,
            creditor_final="ООО Ромашка",
            confidence=0.9,
            reasoning="second attempt"
        ))
    ]

    with patch.object(extraction_agent.agent_creditor, 'run', mock_run):
        fields_config = {
            "creditor": {
                "extraction_method": "llm",
                "prompt_instruction": "Extract creditor"
            }
        }
        res = await run_agent_extraction("test text", fields_config)
        
        # Check that it extracted the creditor name from the second attempt
        assert res["creditor"]["value"] == "ООО Ромашка"
        assert res["creditor"]["confidence"] == 0.9
        assert mock_run.call_count == 2


@pytest.mark.asyncio
async def test_creditor_verification_by_inn():
    # Setup fields config with both creditor_inn and creditor
    fields_config = {
        "creditor": {
            "extraction_method": "llm",
            "prompt_instruction": "Extract creditor"
        },
        "creditor_inn": {
            "extraction_method": "llm_with_tools",
            "prompt_instruction": "Extract INN"
        }
    }

    # Mock creditor_inn extraction to return INN 7730233723
    # Mock creditor extraction to return "ООО Ромашка"
    mock_agent_run = AsyncMock()
    mock_agent_run.side_effect = [
        make_mock_result(CreditorInnResult(INN="7730233723", confidence=0.95, reasoning="inn found")),
        make_mock_result(CreditorResult(
            creditor_name="ООО Ромашка",
            creditor_name_web=None,
            creditor_final="ООО Ромашка",
            confidence=0.9,
            reasoning="extracted creditor"
        )),
    ]

    mock_search = patch("ocr_platform.services.extraction_agent._search_by_inn", return_value="Registry Info: ООО Ромашка, ИНН 7730233723")
    mock_web_name_run = AsyncMock()
    mock_web_name_run.return_value = make_mock_result(CompanyNameResult(company_name="ООО Ромашка", reasoning="Found name"))
    mock_comp_run = AsyncMock()
    mock_comp_run.return_value = make_mock_result(CompanyComparisonResult(
        is_same=True,
        difference_type="exact",
        reasoning="Exactly identical"
    ))

    with patch.object(extraction_agent.agent_creditor_inn, 'run', mock_agent_run), \
         patch.object(extraction_agent.agent_creditor, 'run', mock_agent_run), \
         mock_search, \
         patch.object(extraction_agent.company_name_extraction_agent, 'run', mock_web_name_run), \
         patch.object(extraction_agent.company_comparison_agent, 'run', mock_comp_run):
         
        res = await run_agent_extraction("test text", fields_config)
        assert res["creditor_inn"]["value"] == "7730233723"
        assert res["creditor"]["value"] == "ООО Ромашка"
        assert res["creditor"]["confidence"] == 0.9

    # Test minor mismatch -> corrects name and confidence to 0.5
    mock_agent_run.side_effect = [
        make_mock_result(CreditorInnResult(INN="7730233723", confidence=0.95, reasoning="inn found")),
        make_mock_result(CreditorResult(
            creditor_name="ООО Ромашк",
            creditor_name_web=None,
            creditor_final="ООО Ромашк",
            confidence=0.9,
            reasoning="typo in ocr"
        )),
    ]
    mock_web_name_run.return_value = make_mock_result(CompanyNameResult(company_name="ООО Ромашка", reasoning="Registry name"))
    mock_comp_run.return_value = make_mock_result(CompanyComparisonResult(
        is_same=True,
        difference_type="minor",
        reasoning="Minor OCR typo"
    ))

    with patch.object(extraction_agent.agent_creditor_inn, 'run', mock_agent_run), \
         patch.object(extraction_agent.agent_creditor, 'run', mock_agent_run), \
         mock_search, \
         patch.object(extraction_agent.company_name_extraction_agent, 'run', mock_web_name_run), \
         patch.object(extraction_agent.company_comparison_agent, 'run', mock_comp_run):
         
        res = await run_agent_extraction("test text", fields_config)
        assert res["creditor"]["value"] == "ООО Ромашка"
        assert res["creditor"]["confidence"] == 0.5

    # Test critical mismatch -> retries 3 times, returns "Ошибка распознавания"
    mock_agent_run.side_effect = [
        make_mock_result(CreditorInnResult(INN="7730233723", confidence=0.95, reasoning="inn found")),
        make_mock_result(CreditorResult(
            creditor_name="ПАО Сбербанк",
            creditor_name_web=None,
            creditor_final="ПАО Сбербанк",
            confidence=0.9,
            reasoning="critical error in ocr"
        )),
        make_mock_result(CreditorResult(
            creditor_name="ПАО Сбербанк",
            creditor_name_web=None,
            creditor_final="ПАО Сбербанк",
            confidence=0.9,
            reasoning="critical error in ocr"
        )),
        make_mock_result(CreditorResult(
            creditor_name="ПАО Сбербанк",
            creditor_name_web=None,
            creditor_final="ПАО Сбербанк",
            confidence=0.9,
            reasoning="critical error in ocr"
        )),
    ]
    mock_web_name_run.return_value = make_mock_result(CompanyNameResult(company_name="ООО Ромашка", reasoning="Registry name"))
    mock_comp_run.return_value = make_mock_result(CompanyComparisonResult(
        is_same=False,
        difference_type="critical",
        reasoning="Completely different entities"
    ))

    with patch.object(extraction_agent.agent_creditor_inn, 'run', mock_agent_run), \
         patch.object(extraction_agent.agent_creditor, 'run', mock_agent_run), \
         mock_search, \
         patch.object(extraction_agent.company_name_extraction_agent, 'run', mock_web_name_run), \
         patch.object(extraction_agent.company_comparison_agent, 'run', mock_comp_run):
         
        res = await run_agent_extraction("test text", fields_config)
        assert res["creditor"]["value"] == "Ошибка распознавания"
        assert res["creditor"]["confidence"] == 0.0


@pytest.mark.asyncio
async def test_rtk_creditor_uses_company_name_schema_for_inn_web_search():
    fields_config = {
        "creditor": {
            "extraction_method": "llm_with_tools",
            "prompt_instruction": "Extract creditor",
            "prompt_instruction_inn_web_search": (
                "Find company by INN {inn} from search results only.\n"
                "Search results:\n{web_search_text}\n"
                "Return CompanyNameResult with company_name and reasoning."
            ),
        },
        "creditor_inn": {
            "extraction_method": "llm_with_tools",
            "prompt_instruction": "Extract INN",
        },
    }

    mock_inn_run = AsyncMock(
        return_value=make_mock_result(
            CreditorInnResult(INN="7730233723", confidence=0.95, reasoning="inn found")
        )
    )
    mock_company_name_run = AsyncMock(
        return_value=make_mock_result(
            CompanyNameResult(company_name="OOO Romashka", reasoning="registry name")
        )
    )
    mock_creditor_run = AsyncMock()

    with patch.object(extraction_agent.agent_creditor_inn, "run", mock_inn_run), \
         patch.object(extraction_agent.company_name_extraction_agent, "run", mock_company_name_run), \
         patch.object(extraction_agent.agent_creditor, "run", mock_creditor_run), \
         patch("ocr_platform.services.extraction_agent._search_by_inn", return_value="Registry Info: OOO Romashka"):

        res = await run_agent_extraction("test text", fields_config, profile_id="rtk")

    assert res["creditor_inn"]["value"] == "7730233723"
    assert res["creditor"]["value"] == "OOO Romashka"
    assert res["creditor"]["confidence"] == 0.9
    mock_company_name_run.assert_awaited_once()
    mock_creditor_run.assert_not_awaited()
