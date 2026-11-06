import pytest

from ocr_platform.services import fio_gender


@pytest.mark.parametrize("first,patronymic,expected", [
    ("Анна", "Ивановна", "Женский"),
    ("Иван", "Иванович", "Мужской"),
    ("Муроджон", "Махмуджонович", "Мужской"),
    ("Анна", "Иванович", None),
    ("Иван", "Ивановна", None),
    ("Саша", None, None),
    (None, None, None),
    (None, "", None),
    ("", None, None),
    ("Илья", None, "Мужской"),
    ("Никита", None, "Мужской"),
    (None, "Ильич", "Мужской"),
    (None, "Ильинична", "Женский"),
    (None, "Рашид Кызы", "Женский"),
    (None, "Салман Оглы", "Мужской"),
    (None, "Бек Уулу", "Мужской"),
])
def test_fio_inference_contract(first, patronymic, expected):
    assert fio_gender.infer_gender_from_fio(first, patronymic) == expected


@pytest.mark.parametrize("source,expected", [
    (None, None), ("", None), ("Анна", None), ("Ким Саша", None),
    ("Иван Иванович", "Мужской"), ("Ивана Ивановича", "Мужской"),
    ("Ивану Ивановичу", "Мужской"), ("Иваном Ивановичем", "Мужской"),
    ("Анна Ивановна", "Женский"), ("Анны Ивановны", "Женский"),
    ("Анне Ивановне", "Женский"), ("Анной Ивановной", "Женский"),
    ("Анну Ильиничну", "Женский"), ("Рашид Кызы", "Женский"),
    ("Салман Оглы", "Мужской"),
    # Preserve the established court evidence policy, without silently adding
    # nominative-only rules or morphology-based guesses during a refactor.
    ("Бек Уулу", None),
])
def test_source_evidence_policy_does_not_guess_by_name(monkeypatch, source, expected):
    def forbidden_dictionary():
        pytest.fail("Literal source evidence must not consult name morphology")
    monkeypatch.setattr(fio_gender, "_morph", forbidden_dictionary)
    assert fio_gender.infer_gender_from_source_patronymic(source) == expected
