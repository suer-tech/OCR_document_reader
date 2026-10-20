"""Offline checks of advance obligation candidates and their refusal conditions."""
import pytest

from ocr_platform.services.court_regex_early_report_required import extract_early_report_required


@pytest.mark.parametrize("heading", ["РЕШИЛ:", "Р Е Ш И Л:\n", "ОПРЕДЕЛИЛ:", "О П Р Е Д Е Л И Л\n"])
@pytest.mark.parametrize("deadline", [
    "заблаговременно", "за три дня до заседания", "за 5 рабочих дней до заседания",
    "к 01.10.2026", "не позднее чем 01.10.2026", "до окончания срока процедуры",
])
def test_clear_order_is_unconfirmed_candidate_with_exact_offsets(heading, deadline):
    text = f"Общие сведения.\n{heading}\nФинансовому управляющему {deadline} представить в суд отчёт о результатах процедуры."
    result = extract_early_report_required(text)
    assert result.value is True
    assert result.evidence == text[result.start:result.end]
    assert result.requires_confirmation is True
    assert not hasattr(result, "confidence")


@pytest.mark.parametrize("directive", [
    "Финансовому управляющему представить отчёт после завершения всех мероприятий.",
    "Назначить рассмотрение отчёта финансового управляющего на 01.10.2026.",
    "Финансовому управляющему за пять дней до заседания представить выписку банка.",
    "Финансовому управляющему представить отчёт. За пять дней до заседания должнику представить документы.",
    "Лицам, участвующим в деле, заблаговременно до рассмотрения отчета представить письменную позицию.",
    "Финансовому управляющему представить отчёт с доказательствами заблаговременного направления в адрес кредиторов.",
    "Согласно статье закона финансовому управляющему заблаговременно представить отчёт.",
])
def test_mention_or_other_document_order_is_not_advance_report(directive):
    result = extract_early_report_required("РЕШИЛ:\n" + directive)
    assert result.value is False


def test_historical_order_before_heading_is_ignored():
    result = extract_early_report_required(
        "Финансовому управляющему заблаговременно представить отчёт.\nРЕШИЛ:\nПризнать гражданина банкротом."
    )
    assert result.value is False


def test_missing_operative_part_abstains():
    assert extract_early_report_required("Финансовому управляющему заблаговременно представить отчёт.").value is None


def test_actor_can_be_introduced_in_previous_sentence():
    result = extract_early_report_required(
        "РЕШИЛ:\nФинансовому управляющему выполнить мероприятия. Заблаговременно представить отчёт суду."
    )
    assert result.value is True


def test_explicit_negative():
    result = extract_early_report_required(
        "РЕШИЛ:\nНе обязывать финансового управляющего заблаговременно представлять отчёт."
    )
    assert result.value is False
    assert result.reason == "explicit_negative_order"
    assert result.evidence


def test_conflicting_orders_abstain():
    result = extract_early_report_required(
        "РЕШИЛ:\nФинансовому управляющему заблаговременно представить отчёт. "
        "Не обязывать финансового управляющего заблаговременно представлять отчёт."
    )
    assert result.value is None
    assert result.reason == "conflicting_report_orders"


def test_other_addressee_does_not_inherit_manager_actor():
    result = extract_early_report_required(
        "РЕШИЛ:\nФинансовому управляющему провести мероприятия. "
        "Лицам, участвующим в деле, заблаговременно до заседания по рассмотрению отчёта представить позицию."
    )
    assert result.value is False


def test_unresolved_relation_to_hearing_abstains_instead_of_false():
    result = extract_early_report_required(
        "РЕШИЛ:\nФинансовому управляющему представить к судебному заседанию отчёт."
    )
    assert result.value is None
    assert result.reason == "unsupported_report_time_relation"


@pytest.mark.parametrize("contents", [
    "сведения о ходе и результатах процедуры банкротства, план необходимых мероприятий и реестр кредиторов",
    "сведения о не завершенных мероприятиях процедуры и необходимости продления или завершения",
    "документы по итогам процедуры реализации имущества гражданина, предусмотренные статьей 213.28",
])
def test_second_pass_does_not_silently_redefine_other_documents_as_report(contents):
    result = extract_early_report_required(
        f"РЕШИЛ:\nФинансовому управляющему до 13.04.2027 представить суду {contents}."
    )
    assert result.value is False


@pytest.mark.parametrize("day_count", ["пять", "три", "5"])
def test_second_pass_publication_deadline_does_not_flow_into_next_sentence(day_count):
    result = extract_early_report_required(
        "РЕШИЛ:\nФинансовому управляющему направить сообщение для публикации. "
        f"Не позднее чем за {day_count} рабочих дней до даты судебного заседания сведения о публикации представить в суд. "
        "Представить в суд отчёт о результатах процедуры реализации имущества с приложением документов."
    )
    assert result.value is False


@pytest.mark.parametrize("report", ["отчёт о результатах реализации имущества", "отчет о своей деятельности"])
def test_second_pass_creditor_advance_requirement_is_not_court_deadline(report):
    result = extract_early_report_required(
        f"РЕШИЛ:\nФинансовому управляющему представить {report}; "
        "доказательства заблаговременного направления отчета кредиторам; анализ финансового состояния должника."
    )
    assert result.value is False
