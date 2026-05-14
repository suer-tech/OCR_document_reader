from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List

from ocr_platform.observability.logging import get_logger
from ocr_platform.observability.metrics import inc_pipeline_step, observe_pipeline_step_latency

logger = get_logger(__name__)


@dataclass
class PipelineContext:
    document_id: str
    pipeline_run_id: str
    profile_id: str
    data: Dict[str, Any] = field(default_factory=dict)


class PipelineEngine:
    """
    Исполнитель шагов пайплайна на основе YAML‑профилей.

    Реальная логика шагов и интеграция с сервисами будет добавлена позже.
    """

    def __init__(self, profile_id: str, steps: List[Dict[str, Any]]) -> None:
        self.profile_id = profile_id
        self.steps = steps

    async def run(self, context: PipelineContext) -> PipelineContext:
        pipeline_started = perf_counter()
        logger.info(
            "pipeline_steps_started",
            document_id=context.document_id,
            pipeline_run_id=context.pipeline_run_id,
            profile_id=context.profile_id,
            steps_total=len(self.steps),
        )
        for step in self.steps:
            step_name = step.get("name", "unknown_step")
            step_started = perf_counter()
            logger.info(
                "pipeline_step_started",
                document_id=context.document_id,
                pipeline_run_id=context.pipeline_run_id,
                profile_id=context.profile_id,
                step=step_name,
            )
            inc_pipeline_step(profile_id=context.profile_id, step=step_name, status="started")
            context.data.setdefault("executed_steps", []).append(step_name)
            step_duration = perf_counter() - step_started
            observe_pipeline_step_latency(
                profile_id=context.profile_id,
                step=step_name,
                seconds=step_duration,
            )
            inc_pipeline_step(profile_id=context.profile_id, step=step_name, status="finished")
            logger.info(
                "pipeline_step_finished",
                document_id=context.document_id,
                pipeline_run_id=context.pipeline_run_id,
                profile_id=context.profile_id,
                step=step_name,
                duration_ms=round(step_duration * 1000, 2),
            )
        logger.info(
            "pipeline_steps_finished",
            document_id=context.document_id,
            pipeline_run_id=context.pipeline_run_id,
            profile_id=context.profile_id,
            steps_executed=len(context.data.get("executed_steps", [])),
            duration_ms=round((perf_counter() - pipeline_started) * 1000, 2),
        )
        return context


