from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


SourceType = Literal["crm", "email", "portal", "external", "other"]
ContentType = Literal["pdf", "image", "text"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class IngestDocumentRequest(BaseModel):
    source_type: SourceType = Field(..., description="Источник документа (CRM, email, личный кабинет и т.д.).")
    document_type: Optional[str] = Field(
        default=None,
        description="Явно переданный тип документа. Если не передан, система определяет тип автоматически.",
    )
    document_type_hint: Optional[str] = Field(
        default=None,
        description="Устаревающее поле-подсказка типа документа (например, 'court_decision').",
    )
    content_type: ContentType = Field(..., description="Тип содержимого: pdf, image или text.")
    content_base64: str = Field(..., description="Содержимое документа, закодированное в base64.")
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Ключ идемпотентности. При повторе возвращается существующий pipeline_run_id.",
    )
    external_id: Optional[str] = Field(
        default=None,
        description="Внешний идентификатор документа в исходной системе.",
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="URL-адрес для отправки асинхронного вебхука при завершении или ошибке обработки.",
    )
    meta: Dict[str, Any] = Field(default_factory=dict, description="Произвольные метаданные о документе.")


class IngestDocumentResponse(BaseModel):
    document_id: str = Field(..., description="Внутренний уникальный ID документа в платформе.", examples=["doc_123abc"])
    pipeline_run_id: str = Field(..., description="ID запущенного процесса обработки. Используйте его для поллинга статуса.", examples=["run_456def"])
    resolved_document_type: Optional[str] = Field(default=None, description="Определенный тип документа (если удалось определить автоматически или было передано явно).", examples=["court_decision"])
    resolved_profile_id: Optional[str] = Field(default=None, description="ID профиля обработки.", examples=["court_decision_ru"])
    detection_source: Optional[str] = Field(default=None, description="Источник определения типа (hint, classifier).")
    detection_model: Optional[str] = Field(default=None, description="Модель, определившая тип документа.")
    idempotency_key: str = Field(..., description="Ключ идемпотентности, привязанный к этому запуску.")
    status: Literal["queued", "processing", "retrying", "done", "failed"] = Field(..., description="Текущий статус задачи.", examples=["queued"])


class FieldValue(BaseModel):
    name: str = Field(..., description="Внутреннее системное имя поля.", examples=["debtor_full_name"])
    value: Any = Field(..., description="Извлеченное значение поля.", examples=["Иванов Иван Иванович"])
    reasoning: Optional[str] = Field(default=None, description="Объяснение модели, почему она извлекла именно это значение.", examples=["ФИО должника указано в шапке документа после слова 'Должник:'"])
    confidence: Optional[float] = Field(default=None, description="Уверенность модели в извлеченном значении (от 0 до 1).", examples=[0.95])
    source: Optional[str] = Field(default=None, description="Источник извлечения (llm, nlp, regex).", examples=["llm"])


class ValidationIssue(BaseModel):
    code: str = Field(..., description="Код ошибки.", examples=["missing_required_field"])
    message: str = Field(..., description="Человекочитаемое сообщение об ошибке.", examples=["Обязательное поле 'debtor_inn' не найдено"])
    field_name: Optional[str] = Field(default=None, description="Имя поля, к которому относится ошибка.", examples=["debtor_inn"])
    severity: Literal["info", "warning", "error"] = Field(default="error", description="Критичность ошибки.", examples=["error"])


class DocumentResultResponse(BaseModel):
    document_id: str = Field(..., description="Внутренний ID документа.", examples=["doc_123abc"])
    pipeline_run_id: str = Field(..., description="ID процесса обработки.", examples=["run_456def"])
    raw_text_version_id: Optional[str] = Field(default=None, description="ID версии извлеченного сырого текста.")
    structured_version_id: Optional[str] = Field(default=None, description="ID версии извлеченных структурированных полей.")

    raw_text: Optional[str] = Field(default=None, description="Сырой текст документа, полученный после OCR.")
    fields: Dict[str, FieldValue] = Field(
        default_factory=dict, 
        description="""Словарь извлеченных полей, где ключ - системное имя поля. 
В зависимости от типа документа (например, для `court_decision`) ключи могут быть следующими:
* `debtor_full_name` — ФИО должника
* `debtor_inn` — ИНН должника
* `case_number` — Номер дела
* `judge_full_name` — ФИО судьи
* `court_name` — Название суда
* `decision_date` — Дата решения
* `procedure_type` — Тип процедуры (например, реализация имущества)
* `procedure_end_date` — Дата окончания процедуры
* `procedure_end_date_is_calculated` — Является ли дата окончания вычисленной (true/false)
* `early_report_deadline` — Заблаговременное предоставление отчета ФУ
* `motivating_part` — Мотивирующая часть судебного решения
* `resolutive_part` — Резолютивная часть судебного решения
        """.strip()
    )

    technical_quality_score: Optional[float] = Field(default=None, description="Оценка технического качества OCR (читаемости документа).", examples=[0.98])
    semantic_confidence_score: Optional[float] = Field(default=None, description="Усредненная оценка уверенности модели по всем полям.", examples=[0.91])
    overall_quality_score: Optional[float] = Field(default=None, description="Общая оценка качества извлечения.", examples=[0.94])

    validation_status: Literal["ok", "warnings", "errors"] = Field(..., description="Статус валидации извлеченных данных.", examples=["ok"])
    validation_issues: list[ValidationIssue] = Field(default_factory=list, description="Список проблем валидации, если они есть.")

    human_review_required: bool = Field(..., description="Требуется ли ручная верификация оператором (если оценка ниже порога).", examples=[False])
    human_review_reason: Optional[str] = Field(default=None, description="Причина, по которой требуется ручная проверка.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "document_id": "doc_123abc",
                    "pipeline_run_id": "run_456def",
                    "raw_text": "РЕШЕНИЕ Именем Российской Федерации...",
                    "fields": {
                        "debtor_full_name": {
                            "name": "debtor_full_name",
                            "value": "Иванов Иван Иванович",
                            "reasoning": "ФИО найдено в шапке после слова 'Должник:'",
                            "confidence": 0.98,
                            "source": "nlp"
                        },
                        "debtor_inn": {
                            "name": "debtor_inn",
                            "value": "123456789012",
                            "confidence": 0.95,
                            "source": "nlp"
                        },
                        "case_number": {
                            "name": "case_number",
                            "value": "А05-6/2025",
                            "confidence": 0.99,
                            "source": "nlp"
                        },
                        "judge_full_name": {
                            "name": "judge_full_name",
                            "value": "Петров П.П.",
                            "confidence": 0.92,
                            "source": "nlp"
                        },
                        "court_name": {
                            "name": "court_name",
                            "value": "Арбитражный суд города Москвы",
                            "confidence": 0.99,
                            "source": "nlp"
                        },
                        "decision_date": {
                            "name": "decision_date",
                            "value": "15.03.2025",
                            "confidence": 1.0,
                            "source": "nlp"
                        },
                        "procedure_type": {
                            "name": "procedure_type",
                            "value": "реализация имущества",
                            "confidence": 0.96,
                            "source": "nlp"
                        },
                        "procedure_end_date": {
                            "name": "procedure_end_date",
                            "value": "15.09.2025",
                            "confidence": 0.89,
                            "source": "nlp"
                        },
                        "procedure_end_date_is_calculated": {
                            "name": "procedure_end_date_is_calculated",
                            "value": True,
                            "confidence": 0.95,
                            "source": "nlp"
                        },
                        "early_report_deadline": {
                            "name": "early_report_deadline",
                            "value": "01.09.2025",
                            "confidence": 0.85,
                            "source": "nlp"
                        },
                        "motivating_part": {
                            "name": "motivating_part",
                            "value": "Суд, выслушав доводы сторон, установил следующее...",
                            "confidence": 0.90,
                            "source": "nlp"
                        },
                        "resolutive_part": {
                            "name": "resolutive_part",
                            "value": "РЕШИЛ: Признать Иванова И.И. банкротом...",
                            "confidence": 0.99,
                            "source": "nlp"
                        }
                    },
                    "technical_quality_score": 0.98,
                    "semantic_confidence_score": 0.95,
                    "overall_quality_score": 0.965,
                    "validation_status": "ok",
                    "human_review_required": False
                }
            ]
        }
    }


class PipelineRunStatusResponse(BaseModel):
    pipeline_run_id: str = Field(..., description="ID запущенного процесса обработки.", examples=["run_456def"])
    document_id: str = Field(..., description="ID документа.", examples=["doc_123abc"])
    profile_id: Optional[str] = Field(default=None, description="ID профиля обработки.", examples=["court_decision_ru"])
    status: Literal["queued", "processing", "retrying", "done", "failed"] = Field(..., description="Текущий статус обработки. Ждите статус 'done'.", examples=["done"])
    retry_count: int = Field(default=0, description="Количество попыток повторной обработки при сбоях.")
    created_at: str = Field(..., description="Время создания задачи.")
    started_at: Optional[str] = Field(default=None, description="Время фактического начала обработки.")
    finished_at: Optional[str] = Field(default=None, description="Время завершения обработки.")
    last_error: Optional[str] = Field(default=None, description="Текст последней ошибки (если status='failed').")


class HumanReviewTaskField(BaseModel):
    field_id: str
    field_name: str
    value_system: Any
    value_human: Optional[Any] = None
    source: Optional[str] = None
    error_type: Optional[str] = None
    correction_reason: Optional[str] = None


class HumanReviewTask(BaseModel):
    task_id: str
    document_id: str
    pipeline_run_id: str
    profile_id: str
    fields: list[HumanReviewTaskField]
    overall_quality_score: Optional[float] = None
    created_at: str


class HumanReviewTaskListResponse(BaseModel):
    tasks: list[HumanReviewTask]


class SubmitHumanReviewRequest(BaseModel):
    fields: list[HumanReviewTaskField]


class SubmitHumanReviewResponse(BaseModel):
    task_id: str
    document_id: str
    structured_version_id: str


class MlflowBackfillRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000, description="Максимум run-ов для backfill за один вызов.")
    force: bool = Field(
        default=False,
        description="Если true, отправляет в MLflow даже если run уже найден по tag pipeline_run_id.",
    )


class MlflowBackfillFailedItem(BaseModel):
    pipeline_run_id: str
    error: str


class MlflowBackfillResponse(BaseModel):
    total_candidates: int
    processed: int
    logged: int
    skipped_existing: int
    skipped_incomplete: int
    failed: int
    logged_run_ids: list[str] = Field(default_factory=list)
    failed_items: list[MlflowBackfillFailedItem] = Field(default_factory=list)

