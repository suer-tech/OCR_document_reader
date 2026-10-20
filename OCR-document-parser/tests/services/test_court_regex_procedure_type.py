"""Source intervals and adversarial contexts for current procedure candidates."""
import pytest

from ocr_platform.services.court_regex_procedure_type import extract_procedure_type_candidates


@pytest.mark.parametrize("heading", ["РЕШИЛ:", "Р Е Ш И Л\n", "ОПРЕДЕЛИЛ:", "О П Р Е Д Е Л И Л:\n"])
@pytest.mark.parametrize(("order", "expected"), [
    ("Ввести процедуру реализации имущества гражданина на шесть месяцев.", "реализация имущества гражданина"),
    ("Открыть в отношении должника процедуру реализации его имущества.", "реализация имущества гражданина"),
    ("Продлить срок процедуры реализации ее\nимущества до 01.12.2026.", "реализация имущества гражданина"),
    ("Ввести в отношении гражданки процедуру реструктуризации долгов гражданина.", "реструктуризация долгов"),
    ("Продлить процедуру реструктуризации долгов.", "реструктуризация долгов"),
    ("Срок процедуры реализации имущества гражданина продлить на шесть месяцев.", "реализация имущества гражданина"),
    ("Процедуру реструктуризации долгов продлить до 01.12.2026.", "реструктуризация долгов"),
    ("Признать должника банкротом. Срок процедуры реализации имущества продлить на шесть месяцев.", "реализация имущества гражданина"),
    ("Признать Иванова Ивана Ивановича банкротом с введением процедуры реализации имущества.", "реализация имущества гражданина"),
])
def test_current_orders_have_literal_evidence_and_require_confirmation(heading, order, expected):
    text = "Заявитель просил ввести реструктуризацию долгов.\n" + heading + "\n" + order
    result = extract_procedure_type_candidates(text)
    assert result.problem is None
    assert result.selected.value == expected
    assert result.selected.quote == text[result.selected.start:result.selected.end]
    assert result.selected.requires_confirmation is True
    assert all(c.quote == text[c.start:c.end] for c in result.candidates)
    assert not hasattr(result.selected, "confidence")


@pytest.mark.parametrize("order", [
    "Должник просит ввести процедуру реализации имущества.",
    "Должник ходатайствует ввести процедуру реализации имущества.",
    "Предложить ввести процедуру реализации имущества.",
    "При отсутствии плана ввести процедуру реализации имущества.",
    "Если план не будет утвержден, ввести процедуру реализации имущества.",
    "Суд вправе ввести процедуру реализации имущества.",
    "В соответствии со статьей 213.24 ввести процедуру реализации имущества.",
    "Разъяснить право ввести процедуру реализации имущества.",
    "Разъяснить: «Ввести процедуру реализации имущества».",
    "Не ввести процедуру реализации имущества.",
    "Не продлить процедуру реализации имущества.",
    "Отказать в ходатайстве ввести процедуру реализации имущества.",
    "Ввести в отношении супруги должника процедуру реализации имущества.",
    "Процедура реструктуризации долгов ранее введена определением суда.",
    "Рассмотреть отчет по завершению реализации имущества гражданина.",
    "Признать требования в ходе реализации имущества обоснованными.",
    "При отсутствии ходатайства срок реализации имущества считается продленным на шесть месяцев.",
    "При отсутствии плана срок процедуры реализации имущества продлить на шесть месяцев.",
    "Должник просит процедуру реструктуризации долгов продлить до 01.12.2026.",
])
def test_proposals_law_history_spouse_and_future_are_not_current_orders(order):
    result = extract_procedure_type_candidates("РЕШИЛ:\n" + order)
    assert result.selected is None
    assert result.problem == "current_procedure_order_not_found"


def test_operative_order_overrides_historical_procedure_and_request():
    text = (
        "Определением введена процедура реструктуризации долгов. "
        "Должник просит продлить процедуру реструктуризации долгов.\nРЕШИЛ:\n"
        "Прекратить процедуру реструктуризации долгов.\n"
        "Ввести процедуру реализации имущества гражданина."
    )
    result = extract_procedure_type_candidates(text)
    assert result.selected.value == "реализация имущества гражданина"


@pytest.mark.parametrize("orders", [
    "Ввести реструктуризацию долгов. Ввести реализацию имущества.",
    "Ввести процедуру реализации имущества. Продлить реструктуризацию долгов.",
    "Ввести реализацию имущества. Отменить реализацию имущества.",
])
def test_conflicting_orders_abstain(orders):
    result = extract_procedure_type_candidates("РЕШИЛ:\n" + orders)
    assert result.selected is None
    assert result.problem == "conflicting_procedure_orders"
    assert result.candidates


def test_repeated_current_orders_are_consistent():
    result = extract_procedure_type_candidates(
        "РЕШИЛ:\nВвести реализацию имущества. Продлить реализацию имущества гражданина."
    )
    assert result.selected.value == "реализация имущества гражданина"
    assert len(result.candidates) == 2


def test_no_operative_part_refuses_even_with_title_or_introduction():
    result = extract_procedure_type_candidates(
        "РЕШЕНИЕ о признании гражданина банкротом и введении реализации имущества"
    )
    assert result.selected is None
    assert result.problem == "operative_part_not_found"


def test_does_not_inherit_action_across_another_procedure_mention():
    result = extract_procedure_type_candidates(
        "РЕШИЛ:\nВвести реализацию имущества. "
        "Разъяснить последствия процедуры реструктуризации долгов."
    )
    assert result.selected.value == "реализация имущества гражданина"
    assert len(result.candidates) == 1
