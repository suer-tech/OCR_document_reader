"""Only explicit current-procedure months are candidates for confirmation."""
from unittest.mock import AsyncMock

import pytest

from ocr_platform.services.court_decision_additional import (
    CourtAdditionalLLMResult, additional_fields_result,
)
from ocr_platform.services.court_procedure_duration import (
    extract_procedure_duration, procedure_duration_field,
)
from ocr_platform.services.validation_service import court_field_issues, review_requirement


@pytest.mark.parametrize("term,value", [
    ("шесть", 6), ("6", 6), ("6 (шесть)", 6), ("шесть (6)", 6),
    ("четыре", 4), ("4", 4), ("пять", 5), ("1", 1), ("двенадцать", 12),
])
def test_explicitly_introduced_duration_has_a_literal_span(term, value):
    text = f"РЕШИЛ: Ввести процедуру реализации имущества сроком на {term} месяцев."
    result = extract_procedure_duration(text)
    assert result.selected.value == value
    assert type(result.selected.value) is int
    assert result.selected.quote == text[result.selected.start:result.selected.end]


@pytest.mark.parametrize("heading", ["РЕШИЛ:", "О П Р Е Д Е Л И Л:", "Р Е Ш И Л\n", "ОПРЕДЕЛИЛ\n", "Р Е Ш И Л "])
def test_spaced_or_colonless_headings(heading):
    assert extract_procedure_duration(heading + "Ввести процедуру на шесть месяцев.").selected.value == 6


@pytest.mark.parametrize("order", [
    "Ввести процедуру на шесть месяцев.",
    "Открыть процедуру сроком шесть месяцев.",
    "Ввести процедуру реализации имущества сроком до шести месяцев.",
    "Продлить процедуру реализации имущества на шесть месяцев.",
    "Процедуру реструктуризации долгов продлить на шесть месяцев.",
])
def test_assignment_and_word_order_variants(order):
    assert extract_procedure_duration("РЕШИЛ: " + order).selected.value == 6


@pytest.mark.parametrize("text", [
    "РЕШИЛ: Ввести процедуру до 20.01.2027.",
    "23 сентября 2026 года. РЕШИЛ: Ввести процедуру до 20.01.2027.",
    "Реализация имущества вводится на срок не более чем шесть месяцев. РЕШИЛ: Ввести процедуру.",
    "РЕШИЛ: Ввести процедуру. При отсутствии ходатайства срок процедуры считается продленным на шесть месяцев.",
    "РЕШИЛ: Ввести процедуру. В случае отсутствия отчета продлить процедуру на шесть месяцев.",
    "РЕШИЛ: Ввести процедуру. Финансовому управляющему в срок на шесть месяцев представить отчёт.",
    "РЕШИЛ: Ввести процедуру. Срок обжалования установлен на один месяц.",
    "РЕШИЛ: Ввести процедуру. Приём требований кредиторов установлен на два месяца.",
    "РЕШИЛ: Установить вознаграждение управляющему на шесть месяцев.",
    "Ранее суд вводит процедуру на шесть месяцев. РЕШИЛ: Открыть процедуру до 20.01.2027.",
])
def test_dates_defaults_other_deadlines_and_future_renewals_do_not_supply_months(text):
    result = extract_procedure_duration(text)
    assert result.selected is None
    assert result.problem == "not_found"


@pytest.mark.parametrize("orders", [
    "Ввести процедуру на 6 (пять) месяцев.",
    "Ввести процедуру на 0 месяцев.",
    "Ввести процедуру на шесть месяцев. Продлить процедуру на пять месяцев.",
])
def test_conflicting_or_invalid_durations_require_review(orders):
    field = procedure_duration_field("РЕШИЛ: " + orders)
    assert field["value"] is None
    assert field["validation_issue"]


def test_current_reasoning_can_supply_an_omitted_duration():
    text = ("УСТАНОВИЛ: Арбитражный суд, удовлетворяя ходатайство, признает гражданина банкротом "
            "и вводит процедуру реализации имущества сроком на 6 месяцев. "
            "РЕШИЛ: Признать гражданина банкротом и ввести процедуру реализации имущества.")
    assert extract_procedure_duration(text).selected.value == 6


def test_operative_duration_wins_over_history():
    text = "Ранее суд вводит процедуру на шесть месяцев. РЕШИЛ: Ввести процедуру на четыре месяца."
    assert extract_procedure_duration(text).selected.value == 4


def test_standalone_order_can_be_read_without_a_full_header():
    assert extract_procedure_duration("Ввести процедуру на шесть месяцев.").selected.value == 6


def test_regex_candidate_is_not_automatically_confirmed():
    field = procedure_duration_field("РЕШИЛ: Ввести процедуру на шесть месяцев.")
    assert field["value"] == 6
    assert field["confidence"] == 0
    assert review_requirement(1.0, court_field_issues({"procedure_duration_months": field})) == (True, "court_field_requires_review")


def test_llm_schema_does_not_request_the_regex_field():
    assert "procedure_duration_months" not in CourtAdditionalLLMResult.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_duration_only_does_not_call_an_llm(monkeypatch):
    from ocr_platform.services import extraction_agent as agent
    run = AsyncMock(side_effect=AssertionError("Regex must not call an LLM"))
    monkeypatch.setattr(agent.agent_court_additional, "run", run)
    monkeypatch.setattr(agent, "_get_lf_client", lambda: None)
    fields = await agent._run_agent_extraction_impl(
        "РЕШИЛ: Ввести процедуру на шесть месяцев.",
        {"procedure_duration_months": {"extraction_method": "regex"}},
        profile_id="court_decision_ru",
    )
    assert fields["procedure_duration_months"]["value"] == 6
    run.assert_not_awaited()


def test_other_additional_fields_accept_the_schema_without_duration():
    text = "РЕШИЛ: Ввести процедуру на шесть месяцев."
    data = CourtAdditionalLLMResult.model_validate({
        name: dict(value="explicit_duration" if name == "procedure_end_date_source" else None,
                   reasoning="Из документа", confidence=0.9, ambiguous=False,
                   evidence=text if name == "procedure_end_date_source" else None)
        for name in CourtAdditionalLLMResult.model_fields
    })
    fields = additional_fields_result(data, text, duration_field=procedure_duration_field(text))
    assert fields["procedure_duration_months"]["value"] == 6
    assert fields["procedure_end_date_source"]["value"] == "explicit_duration"
