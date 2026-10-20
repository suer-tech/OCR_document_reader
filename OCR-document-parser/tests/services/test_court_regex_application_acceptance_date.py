"""Acceptance is an event in this source, not the act or application filing date."""
import pytest
from ocr_platform.services.court_regex_application_acceptance_date import (
    extract_application_acceptance_date_candidates as extract,
)

HEADER = "РЕШЕНИЕ\n10 октября 2026 года. Дело № А40-123/2026.\nЗаявление Иванова Ивана Ивановича (ИНН 123456789012) о банкротстве. УСТАНОВИЛ:\n"


@pytest.mark.parametrize("body", [
    "Определением суда от 12.08.2026 заявление принято к производству.",
    "Определением от 12 августа 2026 года заявление принято судом к производству.",
    "Определением суда от «12» августа 2026 г. заявление принято к рассмотрению.",
    "Определением арбитражного суда 12.08.2026 заявление принято к производству.",
    "Определением суд суда 12.08.2026 заявление принято к производству.",
    "12.08.2026 определением суда заявление принято к производству.",
    "Определением от 12.08.2026 суд принял указанное заявление к производству.",
    "Определением от 12.08.2026 к производству суда было принято заявление должника о банкротстве.",
    "Определением от 12.08.2026 возбуждено производство по делу о банкротстве должника.",
    "Определением от 12.08.2026 суд возбудил производство по делу о несостоятельности должника.",
    "Определением от 12.08.2026 возбуждено производство по настоящему делу.",
    "Определением от 12.08.2026 возбуждено производство по делу, назначено заседание по проверке обоснованности заявления должника.",
    "Определением от 12.08.2026 заявление принято; возбуждено производство по делу о банкротстве.",
    "Определением от 12.08.2026 заявление принято, возбуждено производство по делу.",
    "Определением от 12.08.2026 заявление принято и возбуждено производство по делу.",
    "12.08.2026 заявление принято судом к производству, возбуждено дело о банкротстве.",
    "Поступило заявление должника о банкротстве, которое судом было принято, возбуждено производство по делу № А40-123/2026 о банкротстве должника (определение от 12.08.2026).",
    "Определением от 12.08.2026 заявление Иванова Ивана Ивановича (дата рождения: 01.02.1990, гор. Москва, ул. Ленина, д. 5; ИНН 123456789012) о банкротстве принято к производству, возбуждено дело № А40-123/26.",
])
def test_literal_acceptance(body):
    text = HEADER + body
    result = extract(text)
    assert result.selected and result.selected.value == "12.08.2026"
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]
        assert candidate.start <= candidate.date_start < candidate.date_end <= candidate.end
        assert candidate.requires_verification is True
        assert not hasattr(candidate, "confidence")


@pytest.mark.parametrize("body", [
    "12.08.2026 поступило заявление о банкротстве. Решением от 10.10.2026 должник признан банкротом.",
    "Определением суда от 12.08.2026 заявление оставлено без движения до 15.09.2026.",
    "Определением от 12.08.2026 заявление возвращено. Заявление принято к производству.",
    "Определением от 12.08.2026 назначено судебное заседание на 13.09.2026. Заявление принято к производству.",
    "Определением от 12.08.2026 принято к производству заявление о включении требований в реестр кредиторов.",
    "Определением от 12.08.2026 заявление об оспаривании сделки принято к производству.",
    "Определением суда заявление принято к производству. 12.08.2026 состоялось заседание.",
    "Заявление о банкротстве принимается судом при условиях, установленных законом от 26.10.2002.",
    "Определением суда в соответствии с законом от 12.08.2026 заявление принято к производству.",
    "Определением от 31.02.2026 заявление принято к производству.",
    "Определением от 12.08.206 заявление принято к производству.",
    "12.08.2026 заявление подано, а позднее принято к производству, возбуждено дело о банкротстве.",
    "Определением от 12.08.2026 заявление другого кредитора о банкротстве принято к производству.",
])
def test_no_substituted_date(body):
    assert extract(HEADER + body).selected is None


def test_competing_debtor_and_creditor_applications_abstain():
    text = HEADER + ("Определением от 10.04.2026 заявление ИФНС о банкротстве принято к производству. "
                     "Определением от 12.08.2026 заявление должника принято к производству в качестве заявления о вступлении.")
    result = extract(text)
    assert result.selected is None
    assert result.problem == "competing_acceptance_dates"
    assert len(result.candidates) == 2


def test_another_debtor_rejected():
    result = extract(HEADER + "Определением от 12.08.2026 принято к производству заявление Петрова (ИНН 987654321098) о банкротстве.")
    assert result.selected is None
    assert result.problem == "different_debtor_inn"


def test_another_case_rejected():
    result = extract(HEADER + "Определением от 12.08.2026 заявление принято к производству по делу № А40-999/26.")
    assert result.selected is None
    assert result.problem == "different_case_number"


def test_repeated_same_date_supported():
    body = "Определением от 12.08.2026 заявление принято к производству. "
    assert extract(HEADER + body*2).selected.value == "12.08.2026"


def test_following_sentence_is_not_part_of_evidence():
    body = "Определением от 12.08.2026 заявление принято к производству. Исследовав документы, суд установил долг."
    assert extract(HEADER + body).selected.quote.endswith("к производству.")


def test_isolated_creditor_history_requires_resolution():
    result = extract(HEADER + "Определением от 12.08.2026 заявление ПАО Банк о банкротстве принято к производству.")
    assert result.selected is None
    assert result.problem == "creditor_application_requires_resolution"


@pytest.mark.parametrize("change", [str.lower, lambda s: s.replace(" ", "\u00a0"), lambda s: s.replace("\n", "\r\n"), lambda s: "Дата публикации 01.01.2020\n" + s])
def test_source_offsets_survive_text_changes(change):
    text = change(HEADER + "Определением от 12 августа 2026 года заявление принято к производству.")
    candidate = extract(text).selected
    assert candidate.value == "12.08.2026"
    assert candidate.quote == text[candidate.start:candidate.end]
