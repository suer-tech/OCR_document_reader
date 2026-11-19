"""Verify that CI reports model changes independently by profile and stage."""

from shutil import copytree
import subprocess
import sys

import yaml

from ocr_platform.config import pipeline_model_changes as checker


def test_reports_model_provider_and_stage_changes_without_pinning_them(tmp_path, monkeypatch, capsys):
    path = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    monkeypatch.setattr(sys, "argv", ["pipeline_model_changes", "--base", "a" * 40])
    monkeypatch.setattr(checker, "check_profile_config", lambda _: None)
    baseline = [
        ("profiles/financial_manager_report.yaml", """
profile_id: financial_manager_report
models:
  llm_extraction: {provider: router_ai, model: vendor/old-vision}
"""),
        ("profiles/rtk.yaml", """
profile_id: rtk
models:
  ocr: {provider: router_ai, model: vendor/ocr}
  llm_extraction: {provider: router_ai, model: vendor/text}
  verification: {provider: router_ai, model: vendor/verify}
"""),
        ("system/router.yaml", """
detection:
  llm: {provider: router_ai, model: vendor/old-router}
"""),
    ]
    current = [
        (name, yaml.replace("vendor/old-vision", "vendor/new-vision")
                   .replace("model: vendor/ocr", "model: vendor/ocr, temperature: 0.1")
                   .replace("provider: router_ai, model: vendor/text", "provider: openrouter, model: vendor/text")
                   .replace("  verification: {provider: router_ai, model: vendor/verify}\n", "")
                   .replace("vendor/old-router", "vendor/new-router"))
        for name, yaml in baseline
    ]
    monkeypatch.setattr(checker, "_baseline_yaml", lambda *args: baseline)
    monkeypatch.setattr(checker, "_current_yaml", lambda root: current)

    assert checker.main() == 0
    warnings = capsys.readouterr().out.splitlines()
    assert len(warnings) == 4 and all(line.startswith("::warning ") for line in warnings)
    assert any("'financial_manager_report' · 'llm_extraction'" in line and "vendor/new-vision" in line for line in warnings)
    assert any("'rtk' · 'llm_extraction'" in line and "'openrouter'" in line for line in warnings)
    assert any("'rtk' · 'verification'" in line and "удалена" in line for line in warnings)
    assert any("'router' · 'detection.llm'" in line and "vendor/new-router" in line for line in warnings)
    assert "vendor/ocr" not in path.read_text(encoding="utf-8")


def test_default_preflight_accepts_different_models_in_checked_in_profiles(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pipeline_model_changes"])
    assert checker.main() == 0
    assert "Проверено настроек модели" in capsys.readouterr().out


def test_unchanged_models_do_not_emit_a_warning(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(sys, "argv", ["pipeline_model_changes", "--base", "a" * 40])
    monkeypatch.setattr(checker, "_baseline_yaml", lambda *args: checker._current_yaml(checker.CONFIG_ROOT))
    assert checker.main() == 0
    assert capsys.readouterr().out.strip() == "Модели в конфигурации не менялись."


def test_invalid_configuration_stops_preflight(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pipeline_model_changes"])
    monkeypatch.setattr(checker, "CONFIG_ROOT", tmp_path)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "financial_manager_report.yaml").write_text("""
profile_id: financial_manager_report
document_type: financial_manager_report
explicit_only: true
extraction_mode: llm_report_pdf
models:
  llm_extraction: {provider: router_ai, model: ""}
""", encoding="utf-8")
    assert checker.main() == 1
    assert "models.llm_extraction.model" in capsys.readouterr().err


def test_cli_compares_yaml_with_the_actual_git_revision(tmp_path, monkeypatch, capsys):
    config_root = tmp_path / checker.PIPELINES_PATH
    copytree(checker.CONFIG_ROOT, config_root)
    monkeypatch.setattr(checker, "CONFIG_ROOT", config_root)
    monkeypatch.setattr(checker, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def git(*args):
        command = ["git", "-c", "user.name=OCR tests", "-c", "user.email=ocr@example.invalid",
                   "-c", "commit.gpgsign=false", *args]
        return subprocess.check_output(command, cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("add", "--", checker.PIPELINES_PATH)
    base = git("commit-tree", git("write-tree"), "-m", "Baseline OCR configuration")
    path = config_root / "profiles" / "financial_manager_report.yaml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    original = profile["models"]["llm_extraction"]["model"]
    profile["models"]["llm_extraction"]["model"] = "vendor/another-vision"
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["pipeline_model_changes", "--base", base])

    assert checker.main() == 0
    rows = capsys.readouterr().out.splitlines()
    assert len(rows) == 1
    assert original in rows[0] and "vendor/another-vision" in rows[0]
