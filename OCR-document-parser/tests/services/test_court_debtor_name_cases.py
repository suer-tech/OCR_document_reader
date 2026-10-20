"""Morphological regressions use unrelated names, never corpus identifiers."""
import pytest

from ocr_platform.services.court_regex_debtor_full_name import extract_debtor_full_name_candidates


@pytest.mark.parametrize("source, expected", [
    ("Гражданину Дубровскому Льву Андреевичу разъяснить последствия.", "Дубровский Лев Андреевич"),
    ("Должником Дубровским Еремеем Андреевичем представлены сведения.", "Дубровский Еремей Андреевич"),
    ("Должником Дубровским Лазарем Ильичом представлены сведения.", "Дубровский Лазарь Ильич"),
    ("Признать Соснову Савву Петровича банкротом.", "Соснов Савва Петрович"),
    ("Гражданину Соснову Луке Петровичу разъяснить последствия.", "Соснов Лука Петрович"),
    ("Должником Сосновым Данилой Петровичем представлены сведения.", "Соснов Данила Петрович"),
    ("заявление Сосновой-Ольховой Агнии Андреевны о банкротстве.", "Соснова-Ольхова Агния Андреевна"),
    ("Признать Соснову-Ольхову Евдокию Андреевну банкротом.", "Соснова-Ольхова Евдокия Андреевна"),
    ("Должником Дубровской Пелагеей Андреевной представлены сведения.", "Дубровская Пелагея Андреевна"),
    ("Гражданину Сосновой Майе Андреевне разъяснить последствия.", "Соснова Майя Андреевна"),
    ("заявление Сосновой Нелли Андреевны о банкротстве.", "Соснова Нелли Андреевна"),
    ("Должником Сосновой Нинелью Андреевной представлены сведения.", "Соснова Нинель Андреевна"),
])
def test_regular_cases_and_general_lexical_exceptions(source, expected):
    result = extract_debtor_full_name_candidates(source)
    assert result.selected is not None
    assert result.selected.value == expected
    assert result.selected.requires_confirmation
    assert source[result.selected.start:result.selected.end] == result.selected.quote


@pytest.mark.parametrize("source", [
    "Гражданину Каменному Игорю Андреевичу разъяснить последствия.",
    "Должником Каменным Игорем Андреевичем представлены сведения.",
    "Должником Сосновым Игорем Андреевичем представлены сведения.",
    "заявление Сосновой Анны Андреевны о банкротстве.",
])
def test_non_unique_surname_reversal_is_not_claimed_as_unique(source):
    result = extract_debtor_full_name_candidates(source)
    assert result.problem == "ambiguous_nominative"
    assert all(c.requires_confirmation for c in result.candidates)


def test_written_nominative_resolves_compound_surname():
    source = ("Должник: Соснова-Ольхова Анна Андреевна. "
              "Признать Соснову-Ольхову Анну Андреевну банкротом.")
    result = extract_debtor_full_name_candidates(source)
    assert result.selected.value == "Соснова-Ольхова Анна Андреевна"
    assert result.problem is None
