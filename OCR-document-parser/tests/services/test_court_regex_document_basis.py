"""Current act evidence, ambiguity and literal offsets for document_basis."""
import pytest

from ocr_platform.services.court_regex_document_basis import extract_document_basis_candidates


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("АРБИТРАЖНЫЙ СУД\nРЕШЕНИЕ\nУСТАНОВИЛ:\nТекст\nРЕШИЛ:\nПризнать.", "Решение"),
        ("ОПРЕДЕЛЕНИЕ\nУСТАНОВИЛ:\nТекст\nОПРЕДЕЛИЛ:\nУтвердить.", "Определение"),
        ("# **Р Е Ш Е Н И Е**\nтекст\n**Р Е Ш И Л:** Признать.", "Решение"),
        ("## __О П Р Е Д Е\nЛ Е Н И Е__\nтекст\nО П Р Е Д Е\nЛ И Л:\nВвести.", "Определение"),
        ("Решение\nИменем Российской Федерации", "Решение"),
        ("Текст.\nРЕШИЛ:\nПризнать.", "Решение"),
        ("Текст\nОПРЕДЕЛИЛ\nУтвердить.", "Определение"),
        ("Руководствуясь статьями закона, арбитражный суд решил: признать.", "Решение"),
        ("Решением суда от 01.01.2020 дело завершено.\nОПРЕДЕЛЕНИЕ\nОПРЕДЕЛИЛ:\nВвести.", "Определение"),
        ("Определением суда от 01.01.2020 заявление принято.\nРЕШЕНИЕ\nРЕШИЛ:\nПризнать.", "Решение"),
        ("РЕШЕНИЕ\nУСТАНОВИЛ\nЦитируется старый акт:\nОПРЕДЕЛЕНИЕ\nРЕШИЛ:\nПризнать.", "Решение"),
    ],
)
def test_current_markers(text, expected):
    result = extract_document_basis_candidates(text)
    assert result.problem is None
    assert result.selected is not None
    assert result.selected.value == expected
    assert result.selected in result.candidates
    for candidate in result.candidates:
        assert text[candidate.start:candidate.end] == candidate.quote
        assert candidate.requires_confirmation is True


@pytest.mark.parametrize(
    "text",
    [
        "РЕШЕНИЕ\nУСТАНОВИЛ:\nТекст\nОПРЕДЕЛИЛ:\nВвести.",
        "ОПРЕДЕЛЕНИЕ\nУСТАНОВИЛ:\nТекст\nРЕШИЛ:\nПризнать.",
        "РЕШЕНИЕ\nОПРЕДЕЛЕНИЕ\nТекст",
        "РЕШИЛ:\nПризнать.\nОПРЕДЕЛИЛ:\nУтвердить.",
    ],
)
def test_conflicting_current_markers_abstain(text):
    result = extract_document_basis_candidates(text)
    assert result.selected is None
    assert result.problem == "conflicting_current_act_markers"
    assert {candidate.value for candidate in result.candidates} == {"Решение", "Определение"}
    assert all(text[c.start:c.end] == c.quote for c in result.candidates)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Решением суда от 01.01.2020 признан банкротом. Определение может быть обжаловано.",
        "Суд ранее решил признать заявление обоснованным.",
        "Суд определил: принять заявление к производству.",
        "Решение суда обжаловано в установленном порядке.",
        "human_confirmed_fields: document_basis=Решение; p1234_document_basis_Определение",
        "x" * 3501 + "\nРЕШЕНИЕ\n",
        "УСТАНОВИЛ:\nРанее вынесено:\nОПРЕДЕЛЕНИЕ\n",
    ],
)
def test_no_current_evidence_abstains(text):
    result = extract_document_basis_candidates(text)
    assert result.selected is None
    assert result.candidates == ()
    assert result.problem == "current_act_title_or_disposition_not_found"


def test_repeated_support_keeps_each_literal_quote():
    text = "\ufeff\n## Р Е Ш Е Н И Е\r\nУСТАНОВИЛ:\nТекст\nР Е Ш И Л:\nПризнать."
    result = extract_document_basis_candidates(text)
    assert result.selected.quote == "Р Е Ш Е Н И Е"
    assert [candidate.quote for candidate in result.candidates] == ["Р Е Ш Е Н И Е", "Р Е Ш И Л"]
