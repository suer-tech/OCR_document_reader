from inference.postprocess import normalize_fio_components
from inference.contracts import FioComponents


def test_normalize_male_fio_components() -> None:
    result = normalize_fio_components(FioComponents('ОЛЕЙНИКОВА', 'юРИЯ', 'ВЛАДИМИРОВИЧА'))
    assert result.last_name == 'Олейников'
    assert result.first_name == 'Юрий'
    assert result.patronymic == 'Владимирович'


def test_normalize_female_fio_components() -> None:
    result = normalize_fio_components(FioComponents('ГЛУХОВОЙ', 'ВИКТОРИИ', 'ВИКТОРОВНЫ'))
    assert result.last_name == 'Глухова'
    assert result.first_name == 'Виктория'
    assert result.patronymic == 'Викторовна'