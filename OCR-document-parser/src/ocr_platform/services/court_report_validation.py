"""Source checks for report deadlines, shared by selective LLM and final merge.

An unrecognized printed deadline is not proof of a relative deadline. Never
ask a model to subtract ten unless its stated, quoted basis is relative.
"""
from datetime import datetime, timedelta

from .court_regex_early_report_required import _ACTOR, _OTHER_ACTOR, extract_early_report_required
from .court_regex_report_deadline import _ADVANCE, extract_report_deadline_candidates


def validate_report_deadline(
    text: str, value: str, basis: str | None, evidence: str | None,
    next_session_date: str | None,
) -> None:
    source = extract_report_deadline_candidates(text, next_session_date)
    if source.problem in {'unresolved_report_date_reference', 'conflicting_explicit_report_dates'}:
        raise ValueError('Ссылка на срок отчёта неоднозначна; верни null, ambiguous=true, deadline_basis=ambiguous для ручной проверки')
    if source.explicit_dates:
        if source.selected is None or value != source.selected.value:
            raise ValueError('Срок отчёта должен совпадать с однозначной явно указанной датой, без вычитания дней')
        if basis == 'relative_advance':
            raise ValueError('Источник задаёт готовую дату или однозначную ссылку; основание не relative_advance')
        return
    if basis in {'explicit_date', 'date_reference'}:
        # Check the model's focused literal evidence as another instruction.
        # A failure means unresolved binding, never a forced minus-ten retry.
        quoted = extract_report_deadline_candidates('РЕШИЛ:\n' + (evidence or ''), None)
        if quoted.selected is None or not quoted.explicit_dates or quoted.selected.value != value:
            raise ValueError('Не подтверждена связь указанной даты именно с подачей отчёта; уточни дословную цитату либо верни ambiguous=true. Вычитание дней не требуется')
        return
    if basis != 'relative_advance':
        raise ValueError('Непустой срок требует основания explicit_date, date_reference или relative_advance')
    quoted_order = extract_early_report_required('РЕШИЛ:\n' + (evidence or ''))
    if (quoted_order.value is False and not _ACTOR.search(evidence or '')
            and not _OTHER_ACTOR.match((evidence or '').lstrip())
            and extract_early_report_required(text).value is True):
        # A literal quotation may omit an actor inherited from the preceding
        # instruction. The full source must independently establish that actor.
        quoted_order = extract_early_report_required('РЕШИЛ:\nФинансовому управляющему ' + (evidence or ''))
    if quoted_order.value is not True or not _ADVANCE.search(evidence or ''):
        raise ValueError('Цитата не подтверждает относительный срок подачи самого отчёта; сроки анализа и иных документов не подходят')
    try:
        expected = (datetime.strptime(next_session_date, '%d.%m.%Y') - timedelta(days=10)).strftime('%d.%m.%Y')
    except (TypeError, ValueError):
        raise ValueError('Не установлена однозначная следующая дата для расчётного срока отчёта') from None
    if value != expected:
        raise ValueError('Для relative_advance срок отчёта равен подтверждённой next_session_date минус 10 календарных дней')
