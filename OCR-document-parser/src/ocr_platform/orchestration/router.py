from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml

from ocr_platform.observability.logging import get_logger
from ocr_platform.orchestration.pipeline_engine import PipelineEngine
from ocr_platform.services.document_type_service import detect_document_type

logger = get_logger(__name__)


@dataclass
class ProfileResolution:
    profile_id: str
    document_type: str
    detection_source: str
    confidence: float
    detection_model: str | None = None


CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config" / "pipelines"
SYSTEM_ROUTER_PATH = CONFIG_ROOT / "system" / "router.yaml"
PROFILES_DIR = CONFIG_ROOT / "profiles"


@lru_cache(maxsize=1)
def load_router_config() -> Dict[str, Any]:
    if not SYSTEM_ROUTER_PATH.exists():
        raise FileNotFoundError(f"Router config not found: {SYSTEM_ROUTER_PATH}")
    raw = SYSTEM_ROUTER_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(raw)


@lru_cache(maxsize=1)
def load_all_profiles() -> Dict[str, Dict[str, Any]]:
    if not PROFILES_DIR.exists():
        raise FileNotFoundError(f"Profiles directory not found: {PROFILES_DIR}")

    profiles: Dict[str, Dict[str, Any]] = {}
    for profile_file in sorted(PROFILES_DIR.glob("*.yaml")):
        raw = profile_file.read_text(encoding="utf-8")
        profile = yaml.safe_load(raw)
        profile_id = profile.get("profile_id")
        if not profile_id:
            raise ValueError(f"Missing profile_id in {profile_file}")
        profiles[profile_id] = profile
    return profiles


def load_profile(profile_id: str) -> Dict[str, Any]:
    profiles = load_all_profiles()
    if profile_id not in profiles:
        raise ValueError(f"Unsupported profile_id: {profile_id}")
    return profiles[profile_id]


def resolve_profile(
    source_type: str,
    requested_document_type: str | None,
    detection_text: str | None = None,
    document_id: str | None = None,
    pipeline_run_id: str | None = None,
    page_type: str | None = None,
) -> ProfileResolution:
    config = load_router_config()
    profiles = load_all_profiles()

    mapping: Dict[str, str] = config.get("document_type_to_profile", {})
    default_profile = config.get("default_profile", "unknown")
    unknown_document_type = config.get("unknown_document_type", "unknown")
    detection_cfg = config.get("detection", {})
    min_confidence = float(detection_cfg.get("min_confidence", 0.7))
    use_llm_when_missing = bool(detection_cfg.get("use_llm_when_missing_type", True))

    detected_type = requested_document_type.strip() if requested_document_type else None
    if detected_type == "passport":
        if page_type == "registration":
            detected_type = "passport_registration"
        else:
            detected_type = "passport_main"

    detection_source = "request" if detected_type else "fallback"
    confidence = 1.0 if detected_type else 0.0

    if not detected_type and use_llm_when_missing:
        logger.info(
            "document_type_detection_started",
            source_type=source_type,
            use_llm_when_missing=use_llm_when_missing,
        )
        allowed_types = sorted(set(mapping.keys()) | {unknown_document_type})
        llm_result = detect_document_type(
            detection_text or "",
            allowed_types,
            llm_config=detection_cfg.get("llm", {}),
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
        )
        logger.info(
            "document_type_detection_result",
            document_type=llm_result.document_type,
            confidence=llm_result.confidence,
            source=llm_result.source,
            reasoning=llm_result.reasoning,
            model=llm_result.model_name,
        )
        detected_type = llm_result.document_type
        detection_source = llm_result.source
        confidence = llm_result.confidence
        detection_model = llm_result.model_name
        if confidence < min_confidence:
            detected_type = unknown_document_type
            detection_source = "low_confidence_fallback"
            detection_model = llm_result.model_name
    else:
        if detected_type:
            logger.info(
                "document_type_detection_skipped",
                reason="provided_in_request",
                requested_document_type=detected_type,
            )
        elif not use_llm_when_missing:
            logger.info(
                "document_type_detection_skipped",
                reason="disabled_in_router_config",
            )
        detection_model = None

    if not detected_type:
        detected_type = unknown_document_type

    profile_id = mapping.get(detected_type, default_profile)
    profile = profiles.get(profile_id, profiles.get(default_profile))
    if profile is None:
        raise ValueError("No valid profile found and default profile is missing")

    applicable_sources = set(profile.get("applicable_sources", []))
    if applicable_sources and source_type not in applicable_sources:
        profile_id = default_profile
        detected_type = unknown_document_type
        detection_source = "source_mismatch_fallback"

    return ProfileResolution(
        profile_id=profile_id,
        document_type=detected_type,
        detection_source=detection_source,
        confidence=confidence,
        detection_model=detection_model,
    )


def build_pipeline_engine(profile_config: Dict[str, Any]) -> PipelineEngine:
    steps: List[Dict[str, Any]] = profile_config.get("pipeline", [])
    profile_id = profile_config["profile_id"]
    return PipelineEngine(profile_id=profile_id, steps=steps)
