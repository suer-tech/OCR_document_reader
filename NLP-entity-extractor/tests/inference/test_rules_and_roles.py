from pathlib import Path

from api.services.pdf import extract_text_from_pdf_bytes
from inference.rules import (
    extract_case_number,
    extract_court_name,
    extract_decision_date,
    extract_inn,
    extract_motivating_part,
    extract_procedure_end_date,
    extract_procedure_end_date_with_meta,
    extract_procedure_type,
    extract_resolutive_part,
)
from inference.transformer import TransformerTokenClassifierExtractor


def test_extracts_court_fields_from_text() -> None:
    text = (
        "АРБИТРАЖНЫЙ СУД РОСТОВСКОЙ ОБЛАСТИ "
        "Дело № А53-38537/2023 "
        "ИНН 611600763369 "
        "19 декабря 2023 года "
        "сроком до 17 июня 2024 года "
        "процедуру реализации имущества гражданина"
    )
    assert extract_court_name(text) == "АРБИТРАЖНЫЙ СУД РОСТОВСКОЙ ОБЛАСТИ"
    assert extract_case_number(text) == "А53-38537/2023"
    assert extract_inn(text) == "611600763369"
    assert extract_decision_date(text) == "19.12.2023"
    assert extract_procedure_end_date(text) == "17.06.2024"
    assert extract_procedure_type(text) == "процедуру реализации имущества гражданина"


def test_derives_procedure_end_date_from_duration() -> None:
    text = (
        "Арбитражный суд Архангельской области "
        "17 февраля 2025 года "
        "ввести процедуру реализации имущества гражданина на срок 6 месяцев."
    )
    assert extract_court_name(text) == "АРБИТРАЖНЫЙ СУД АРХАНГЕЛЬСКОЙ ОБЛАСТИ"
    assert extract_decision_date(text) == "17.02.2025"
    assert extract_procedure_end_date(text) == "17.08.2025"

    # Test word durations (various grammatical cases for months and number words)
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на три месяца") == "17.05.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на трех месяцев") == "17.05.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на четыре месяца") == "17.06.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на четырех месяцев") == "17.06.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на пять месяцев") == "17.07.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на пяти месяцев") == "17.07.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на 4 месяцев") == "17.06.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: сроком на 3 месяца") == "17.05.2025"

    # Test explicit end dates with trailing г./г
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: до 17 августа 2025 г.") == "17.08.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: до 17 августа 2025 г") == "17.08.2025"
    assert extract_procedure_end_date("17 февраля 2025 года РЕШИЛ: до 17 августа 2025 года") == "17.08.2025"

    # Test metadata flag behavior
    assert extract_procedure_end_date_with_meta("17 февраля 2025 года РЕШИЛ: сроком на 6 месяцев") == ("17.08.2025", True)
    assert extract_procedure_end_date_with_meta("17 февраля 2025 года РЕШИЛ: сроком на три месяца") == ("17.05.2025", True)
    assert extract_procedure_end_date_with_meta("17 февраля 2025 года РЕШИЛ: до 17 августа 2025 г.") == ("17.08.2025", False)
    assert extract_procedure_end_date_with_meta("17 февраля 2025 года РЕШИЛ: назначить рассмотрение отчета на 17 августа 2025 года на 10 час. 00 мин.") == ("17 августа 2025 года на 10 час. 00 мин.", False)
    assert extract_procedure_end_date_with_meta("Нет никаких дат") == (None, None)


def test_model_extracts_fields_from_real_sample_pdf() -> None:
    extractor = TransformerTokenClassifierExtractor.from_pretrained("models/exports/bootstrap-russian-ner")
    text = extract_text_from_pdf_bytes(Path("Решение о признании банкротом.pdf").read_bytes())
    result = extractor.predict(text)
    assert result.fields.applicant_fio.last_name.startswith("Олейников")
    assert result.fields.judge_fio.last_name.startswith("Глухов")
    assert result.fields.court_name == "АРБИТРАЖНЫЙ СУД РОСТОВСКОЙ ОБЛАСТИ"
    assert result.fields.case_number == "А53-38537/2023"
    assert result.fields.inn == "611600763369"
    assert result.fields.decision_date == "19.12.2023"
    assert result.fields.procedure_end_date == "17.06.2024"
    assert result.fields.motivating_part is not None
    assert "установил" in result.fields.motivating_part.lower() or len(result.fields.motivating_part) > 100
    assert result.fields.resolutive_part is not None
    assert len(result.fields.resolutive_part) > 100
    assert "электронная подпись действительна" not in result.fields.resolutive_part.lower()


def test_extract_motivating_part() -> None:
    # 1. Простой случай
    text1 = "Некий текст. УСТАНОВИЛ: Важный текст мотивировки. РЕШИЛ: ввести процедуру."
    assert extract_motivating_part(text1) == "Важный текст мотивировки."

    # 2. Разрядка в буквах и разный регистр
    text2 = "Суд у с т а н о в и л: текст мотивировки... Р Е Ш И Л : признать банкротом."
    assert extract_motivating_part(text2) == "текст мотивировки..."

    # 3. Лишние символы в начале (двоеточие, пробелы, тире)
    text3 = "Суд УСТАНОВИЛ: - Вторая мотивировка. РЕШИЛ: утвердить."
    assert extract_motivating_part(text3) == "Вторая мотивировка."

    # 4. Отсутствие установления
    text4 = "Суд решил: ввести процедуру."
    assert extract_motivating_part(text4) is None

    # 5. Отсутствие резолютивной части
    text5 = "Суд установил: мотивировка без решения."
    assert extract_motivating_part(text5) is None


def test_extract_resolutive_part() -> None:
    # 1. Простой случай с подписью
    text1 = "Некий текст. РЕШИЛ: ввести процедуру. Электронная подпись действительна. Какой-то хвост."
    assert extract_resolutive_part(text1) == "ввести процедуру."

    # 2. Без подписи (до конца документа)
    text2 = "Суд решил: ввести процедуру банкротства гражданина."
    assert extract_resolutive_part(text2) == "ввести процедуру банкротства гражданина."

    # 3. Разные регистры и пробелы в маркере
    text3 = "Суд Р Е Ш И Л  :   - Вторая резолюция.   электронная   подпись   действительна   и так далее"
    assert extract_resolutive_part(text3) == "Вторая резолюция."

    # 4. Отсутствие слова РЕШИЛ
    text4 = "Суд установил мотивировку."
    assert extract_resolutive_part(text4) is None


