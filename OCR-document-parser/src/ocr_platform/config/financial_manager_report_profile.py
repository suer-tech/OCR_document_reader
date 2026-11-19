"""Check the report profile before a worker or a deployment uses it."""

from pathlib import Path
import sys

import yaml

PROFILE_ID = "financial_manager_report"
EXTRACTION_MODE = "llm_report_pdf"
PROVIDER = "router_ai"
CONFIG_ROOT = Path(__file__).with_name("pipelines")
ALTERNATIVE_MODEL_OPTIONS = {"models", "providers", "fallback_model", "fallback_models", "fallback_provider"}


def extraction_config(profile: dict) -> dict:
    models = profile.get("models")
    config = models.get("llm_extraction") if isinstance(models, dict) else None
    expected = f"{PROFILE_ID} требует одну модель через RouterAI ({PROVIDER}) в режиме {EXTRACTION_MODE}"
    if not isinstance(config, dict):
        raise ValueError(f"{expected}. Отсутствует models.llm_extraction.")
    settings = profile.get("extraction_mode"), config.get("provider")
    if settings != (EXTRACTION_MODE, PROVIDER):
        raise ValueError(
            f"{expected}. В конфигурации указаны режим {profile.get('extraction_mode')!r}, "
            f"провайдер {config.get('provider')!r}, модель {config.get('model')!r}."
        )
    model = config.get("model")
    if not isinstance(model, str) or not model or any(char.isspace() for char in model):
        raise ValueError(f"{expected}. Укажите непустой models.llm_extraction.model без пробелов.")
    extra_options = set(config) & ALTERNATIVE_MODEL_OPTIONS
    extra_model_blocks = set(models) - {"llm_extraction"}
    if extra_options or extra_model_blocks:
        raise ValueError(
            f"{expected}. Параметры альтернативных моделей {sorted(extra_options)!r} "
            f"или дополнительные блоки моделей {sorted(extra_model_blocks, key=str)!r}."
        )
    return config


def check_profile_config(root: Path) -> dict:
    profiles = root / "profiles"
    target = profiles / f"{PROFILE_ID}.yaml"
    profile = yaml.safe_load(target.read_text(encoding="utf-8"))
    if (not isinstance(profile, dict) or profile.get("profile_id") != PROFILE_ID
            or profile.get("document_type") != PROFILE_ID or profile.get("explicit_only") is not True):
        raise ValueError(f"В {target.name} нарушены настройки идентификатора, типа или явного выбора профиля.")
    config = extraction_config(profile)

    for path in profiles.glob("*.yaml"):
        if path != target:
            candidate = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict) and candidate.get("profile_id") == PROFILE_ID:
                raise ValueError(f"{PROFILE_ID} повторно объявлен в {path.name}.")

    router = yaml.safe_load((root / "system" / "router.yaml").read_text(encoding="utf-8"))
    mapping = router.get("document_type_to_profile") if isinstance(router, dict) else None
    if not isinstance(mapping, dict) or mapping.get(PROFILE_ID) != PROFILE_ID:
        raise ValueError(f"В router.yaml тип {PROFILE_ID} должен выбирать одноимённый профиль.")
    return config


def main() -> int:
    try:
        config = check_profile_config(CONFIG_ROOT)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Ошибка проверки OCR-конфигурации: {exc}", file=sys.stderr)
        return 1
    print(f"Конфигурация {PROFILE_ID} проверена, {config['model']} через {config['provider']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
