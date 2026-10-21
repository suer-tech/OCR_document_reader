from pathlib import Path

import yaml

from ocr_platform.services.court_regex_candidates import extract_court_candidates


def test_source_spans_and_value_types():
    text = "РЕШЕНИЕ\nДело № А40 – 123/2026\n01 октября 2026 года\nУСТАНОВИЛ:\nОбстоятельства.\nРЕШИЛ:\nВвести процедуру реализации имущества сроком на шесть месяцев.\n"
    names = ["case_number", "decision_date", "procedure_duration_months", "procedure_end_date", "procedure_end_date_is_calculated", "motivating_part", "resolutive_part", "early_report_required"]
    result = extract_court_candidates(text, names)
    assert result.fields["case_number"].value == "А40-123/2026"
    assert result.fields["procedure_duration_months"].value == 6
    assert result.fields["procedure_end_date"].value == "01.04.2027"
    assert result.fields["procedure_end_date_is_calculated"].value is True
    assert result.fields["early_report_required"].value is False
    for candidate in result.fields.values():
        assert text[candidate.start:candidate.end] == candidate.quote


def test_inline_reasoning_preserves_current_section_boundaries():
    text = "Суд, рассмотрев материалы, установил следующее.\nОбстоятельства.\nР Е Ш И Л:\nВвести процедуру."
    result = extract_court_candidates(text, ["motivating_part", "resolutive_part"])
    assert result.fields["motivating_part"].value == "Обстоятельства."
    assert result.fields["resolutive_part"].value == "Ввести процедуру."


def test_debtor_name_keeps_the_public_structured_shape():
    text = "РЕШЕНИЕ\nРЕШИЛ:\nПризнать Иванова Ивана Ивановича несостоятельным (банкротом)."
    result = extract_court_candidates(text, ["debtor_full_name"])
    assert result.fields["debtor_full_name"].value == {
        "debtor_last_name": "Иванов", "debtor_first_name": "Иван", "debtor_patronymic": "Иванович"}


def test_full_profile_can_be_dispatched_without_llm():
    profile = Path(__file__).parents[2] / "src/ocr_platform/config/pipelines/profiles/court_decision_ru.yaml"
    fields = yaml.safe_load(profile.read_text(encoding="utf-8"))["fields"]
    result = extract_court_candidates("РЕШЕНИЕ\nДело № А40-1/2026\n01 октября 2026 года\nРЕШИЛ:\nВвести процедуру реализации имущества сроком на шесть месяцев.", list(fields))
    assert set(result.fields) | set(result.problems) == set(fields)
    assert "extractor_not_registered" not in result.problems.values()
    assert "extractor_not_implemented" not in result.problems.values()
