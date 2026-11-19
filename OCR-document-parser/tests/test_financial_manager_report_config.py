"""Offline checks for the report model before OCR starts."""

import runpy
from shutil import copytree

import pytest
import yaml

from ocr_platform.config import financial_manager_report_profile as guard


@pytest.fixture
def config_copy(tmp_path, monkeypatch):
    root = tmp_path / "pipelines"
    copytree(guard.CONFIG_ROOT, root)
    monkeypatch.setattr(guard, "CONFIG_ROOT", root)
    return root


def test_checked_in_report_configuration_is_valid(capsys):
    with pytest.raises(SystemExit) as result:
        runpy.run_path(guard.__file__, run_name="__main__")
    assert result.value.code == 0
    expected = guard.check_profile_config(guard.CONFIG_ROOT)["model"]
    assert f"{expected} через router_ai" in capsys.readouterr().out


@pytest.mark.parametrize(("path", "bad_value", "error_fragment"), [
    (("models", "llm_extraction", "model"), " ", "models.llm_extraction.model"),
    (("models", "llm_extraction", "provider"), "other-provider", "other-provider"),
    (("models", "llm_extraction", "fallback_models"), ["another-model"], "fallback_models"),
    (("models", "llm_transcription"), {"model": "another-model"}, "llm_transcription"),
    (("models", "llm_extraction"), None, "Отсутствует models.llm_extraction"),
    (("extraction_mode",), "llm_header", "llm_header"),
    (("explicit_only",), False, "явного выбора"),
], ids=["model", "provider", "fallback", "other-block", "missing-extraction", "mode", "explicit-only"])
def test_preflight_reports_invalid_configuration(config_copy, capsys, path, bad_value, error_fragment):
    profile_file = config_copy / "profiles" / "financial_manager_report.yaml"
    profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
    parent = profile
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = bad_value
    profile_file.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")

    assert guard.main() == 1
    message = capsys.readouterr().err
    assert "Ошибка проверки OCR-конфигурации" in message
    assert error_fragment in message


def test_preflight_reports_route_to_a_different_profile(config_copy, capsys):
    router_file = config_copy / "system" / "router.yaml"
    router = yaml.safe_load(router_file.read_text(encoding="utf-8"))
    router["document_type_to_profile"]["financial_manager_report"] = "court_decision_ru"
    router_file.write_text(yaml.safe_dump(router, allow_unicode=True), encoding="utf-8")

    assert guard.main() == 1
    assert "router.yaml" in capsys.readouterr().err


def test_preflight_reports_a_duplicate_profile_id(config_copy, capsys):
    (config_copy / "profiles" / "zz_duplicate.yaml").write_text(
        "profile_id: financial_manager_report\n", encoding="utf-8",
    )

    assert guard.main() == 1
    assert "повторно объявлен в zz_duplicate.yaml" in capsys.readouterr().err


def test_preflight_accepts_a_different_valid_model(config_copy, capsys):
    path = config_copy / "profiles" / "financial_manager_report.yaml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile["models"]["llm_extraction"]["model"] = "vendor/report-vision"
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")

    assert guard.main() == 0
    assert "vendor/report-vision через router_ai" in capsys.readouterr().out
