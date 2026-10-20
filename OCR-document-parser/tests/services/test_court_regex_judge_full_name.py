from pathlib import Path

import pytest

from ocr_platform.services.court_regex_judge_full_name import extract_judge_full_name_candidates


def extract(text):
    result = extract_judge_full_name_candidates(text)
    for candidate in result.candidates:
        assert text[candidate.start:candidate.end] == candidate.quote
        assert candidate.requires_confirmation is True
    return result


@pytest.mark.parametrize("source", [
    "Судья Иванов И.П.", "Судья И.П.Иванов", "Судья И. П.\nИванов",
    "Председательствующий - судья И.П. Иванов", "СУДЬЯ И.П. ИВАНОВ",
    "Судья ______________ / И.П. Иванов",
])
def test_signature_variants(source):
    assert extract(source).selected.value == "Иванов И.П."


@pytest.mark.parametrize(("header", "signature", "expected"), [
    ("Иванова И.П.", "И.П. Иванов", "Иванов И.П."),
    ("Ковальской О.А.", "О.А. Ковальская", "Ковальская О.А."),
    ("Осадчей М.А.", "М.А. Осадчая", "Осадчая М.А."),
    ("Петровой А.А.", "А.А. Петрова", "Петрова А.А."),
    ("И.И. Кнышевского", "И.И. Кнышевский", "Кнышевский И.И."),
    ("Иванова Ивана Петровича", "Иванов Иван Петрович", "Иванов Иван Петрович"),
    ("Ивановой Анны Петровны", "Иванова Анна Петровна", "Иванова Анна Петровна"),
    ("Иванова И.П.", "Иванов Иван Петрович", "Иванов Иван Петрович"),
])
def test_header_normalization_uses_same_document(header, signature, expected):
    text = f"Арбитражный суд в составе судьи {header}, рассмотрев заявление.\nРЕШИЛ:\nСудья {signature}"
    result = extract(text)
    assert result.selected.value == expected
    assert len(result.candidates) == 2


@pytest.mark.parametrize("header,expected", [
    ("Иванова И.П.", "Иванов И.П."), ("Кнышевского И.П.", "Кнышевский И.П."),
    ("Бондаренко И.П.", "Бондаренко И.П."),
])
def test_unambiguous_header_only(header, expected):
    assert extract(f"Арбитражный суд в составе судьи {header}, рассмотрев дело.").selected.value == expected


@pytest.mark.parametrize("name", ["Ивановой А.А.", "Ланга П.П.", "Иванова Ивана Петровича"])
def test_ambiguous_genitive_without_signature_abstains(name):
    result = extract(f"Арбитражный суд в составе судьи {name}, рассмотрев дело.")
    assert result.selected is None
    assert result.problem == "ambiguous_nominative"


@pytest.mark.parametrize("header,signature", [
    ("Иванова И.П.", "П.П. Иванов"), ("Иванова И.П.", "И.П. Петров"),
    ("Иванова И.", "И.П. Иванов"),
    ("Иванова Ивана Петровича", "Иванов Игорь Петрович"),
])
def test_disagreement_abstains(header, signature):
    result = extract(f"Арбитражный суд в составе судьи {header}, рассмотрев дело.\nСудья {signature}")
    assert result.selected is None
    assert result.problem == "conflicting_judges"


def test_two_signatures_abstain():
    assert extract("РЕШИЛ:\nСудья И.П. Иванов\nСудья П.П. Петров").problem == "conflicting_judges"


def test_collegiate_header_abstains():
    text = "Арбитражный суд в составе председательствующего судьи Иванова И.П., судей Петрова П.П., Сидорова И.И., рассмотрев дело.\nСудья И.П. Иванов"
    assert extract(text).problem == "multiple_current_judges"


def test_signature_after_appeal_instructions_without_body_marker():
    assert extract("Решение можно обжаловать в апелляционный суд.\nСудья И.П. Иванов").selected.value == "Иванов И.П."


def test_no_initial_expansion():
    assert extract("Судья И. Иванов").selected.value == "Иванов И."


def test_narrative_assistant_manager_past_act_and_appeal_not_judge():
    text = """Арбитражный суд в составе судьи Иванова И.П.,
при ведении протокола помощником судьи Петровой А.А., рассмотрев дело.
Определением суда в составе судьи Сидорова П.П. заявление принято.
Финансовый управляющий Петров Петр Петрович.
Апелляционный суд в составе судьи Смирнова И.И. оставил акт в силе.
РЕШИЛ:
Судья И.П. Иванов"""
    result = extract(text)
    assert result.selected.value == "Иванов И.П."
    assert {candidate.value for candidate in result.candidates} == {"Иванов И.П."}


@pytest.mark.parametrize("text", [
    "", "Судья по своей инициативе назначает заседание.",
    "Определением суда в составе судьи Иванова И.П. принято заявление.",
    "Апелляционный суд в составе судьи Иванова И.П., рассмотрев жалобу.",
    "Финансовый управляющий Иванов И.П.", "Кому выдана Иванова Анна Петровна",
    "РЕШИЛ:\nСудья И.П. Иванов рассмотрел старое дело.\nДругие обстоятельства.",
])
def test_non_current_judge_contexts_abstain(text):
    assert extract(text).selected is None


def test_certificate_cannot_resolve_header_or_expand_name():
    text = "Арбитражный суд в составе судьи Ивановой А.А., рассмотрев дело.\nДанные ЭП:\nКому выдана Иванова Анна Александровна"
    assert extract(text).problem == "ambiguous_nominative"


def test_certificate_status_before_signature_and_multiline_status_after_it():
    text = "РЕШИЛ:\nЭлектронная подпись действительна.\nСудья И.П. Иванов\nЭлектронная подпись сформирована некорректно.\nПодпись не соответствует файлу\nДанные ЭП: центр\nКому выдана Петрова Анна"
    assert extract(text).selected.value == "Иванов И.П."


def test_consonant_final_header_is_already_nominative():
    text = "Арбитражный суд в составе судьи Штрауб В.Л., рассмотрев дело.\nДанные ЭП: Кому выдана Петрова Анна"
    assert extract(text).selected.value == "Штрауб В.Л."


def test_interleaved_issuer_requires_matching_header_and_literal_signature():
    text = ("Арбитражный суд в составе судьи Антипиной С.М., рассмотрев дело.\nРЕШИЛ:\n"
            "Судья Д а н н ы е Э П : У д о с т о в е р я ю щ и й центр С.М. Антипина\n"
            "Кому выдана Петрова Анна")
    result = extract(text)
    assert result.selected.value == "Антипина С.М."
    assert result.selected.quote == "С.М. Антипина"


@pytest.mark.parametrize("header,technical", [
    ("Антипиной С.М.", "Судья Данные ЭП: центр П.П. Петров"),
    ("Антипиной С.М.", "Судья Данные ЭП: Кому выдана С.М. Антипина"),
    ("Антипиной С.М.", "Кому выдана С.М. Антипина"),
])
def test_technical_name_cannot_override_or_supply_header(header, technical):
    result = extract(f"Арбитражный суд в составе судьи {header}, рассмотрев дело.\nРЕШИЛ:\n{technical}")
    assert result.selected is None


def test_all_immutable_corpus_candidates_have_literal_spans():
    corpus = Path(__file__).resolve().parents[3] / "pau-intro-decisions-2026-10-20/pau_intro/court_decision"
    if not corpus.is_dir():
        pytest.skip("immutable local corpus is not present")
    documents = sorted(corpus.glob("*/raw_text.txt"))
    assert len(documents) == 355
    for document in documents:
        extract(document.read_text(encoding="utf-8"))
