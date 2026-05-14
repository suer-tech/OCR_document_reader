from pathlib import Path

from api.services.pdf import extract_text_from_pdf_bytes
from inference.rules import (
    extract_case_number,
    extract_court_name,
    extract_decision_date,
    extract_inn,
    extract_procedure_end_date,
    extract_procedure_type,
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
    assert extract_decision_date(text) == "2023-12-19"
    assert extract_procedure_end_date(text) == "2024-06-17"
    assert extract_procedure_type(text) == "процедуру реализации имущества гражданина"


def test_derives_procedure_end_date_from_duration() -> None:
    text = (
        "Арбитражный суд Архангельской области "
        "17 февраля 2025 года "
        "ввести процедуру реализации имущества гражданина на срок 6 месяцев."
    )
    assert extract_court_name(text) == "АРБИТРАЖНЫЙ СУД АРХАНГЕЛЬСКОЙ ОБЛАСТИ"
    assert extract_decision_date(text) == "2025-02-17"
    assert extract_procedure_end_date(text) == "2025-08-17"


def test_model_extracts_fields_from_real_sample_pdf() -> None:
    extractor = TransformerTokenClassifierExtractor.from_pretrained("models/exports/bootstrap-russian-ner")
    text = extract_text_from_pdf_bytes(Path("Решение о признании банкротом.pdf").read_bytes())
    result = extractor.predict(text)
    assert result.fields.applicant_fio.last_name.startswith("Олейников")
    assert result.fields.judge_fio.last_name.startswith("Глухов")
    assert result.fields.court_name == "АРБИТРАЖНЫЙ СУД РОСТОВСКОЙ ОБЛАСТИ"
    assert result.fields.case_number == "А53-38537/2023"
    assert result.fields.inn == "611600763369"
    assert result.fields.decision_date == "2023-12-19"
    assert result.fields.procedure_end_date == "2024-06-17"
