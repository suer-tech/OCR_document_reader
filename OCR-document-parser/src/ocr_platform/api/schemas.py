from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


SourceType = Literal["crm", "email", "portal", "external", "other"]
ContentType = Literal["pdf", "image", "text"]

DOCUMENT_TYPE_DESCRIPTION = (
    "Тип документа: court_decision — судебное решение/определение (профиль court_decision_ru); "
    "rtk — заявление о включении в РТК; rtk2 — судебное определение о рассмотрении требований; "
    "rtk3 — включение требований кредитора в реестр (ЕФРСБ); "
    "passport_main — главная страница паспорта РФ; passport_registration — страница регистрации; "
    "unknown — явно выбранный неизвестный тип, без автоматической классификации. "
    "Автоопределение доступно через POST /documents/ingest, если не заданы document_type и "
    "document_type_hint. В форме POST /documents/upload поле document_type обязательно."
)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class IngestDocumentRequest(BaseModel):
    source_type: SourceType = Field(
        ..., description="Источник документа (CRM, email, личный кабинет и т.д.)."
    )
    document_type: Optional[str] = Field(
        default=None,
        description=DOCUMENT_TYPE_DESCRIPTION,
    )
    document_type_hint: Optional[str] = Field(
        default=None,
        description="Устаревающий аналог document_type. Используется как явно заданный тип, если document_type не заполнен.",
    )
    content_type: ContentType = Field(
        ..., description="Тип содержимого: pdf, image или text."
    )
    content_base64: str = Field(
        ..., description="Содержимое документа, закодированное в base64."
    )
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
    meta: Dict[str, Any] = Field(
        default_factory=dict, description="Произвольные метаданные о документе."
    )


class IngestDocumentResponse(BaseModel):
    document_id: str = Field(
        ...,
        description="Внутренний уникальный ID документа в платформе.",
        examples=["doc_123abc"],
    )
    pipeline_run_id: str = Field(
        ...,
        description="ID запущенного процесса обработки. Используйте его для поллинга статуса.",
        examples=["run_456def"],
    )
    resolved_document_type: Optional[str] = Field(
        default=None,
        description="В текущем асинхронном ответе загрузки возвращается null: классификация выполняется позже.",
        examples=["court_decision"],
    )
    resolved_profile_id: Optional[str] = Field(
        default=None,
        description="В ответе загрузки возвращается null. Фактический profile_id доступен через GET /pipeline-runs/{pipeline_run_id}.",
        examples=["court_decision_ru"],
    )
    detection_source: Optional[str] = Field(
        default=None, description="Источник классификации; в текущем ответе загрузки возвращается null."
    )
    detection_model: Optional[str] = Field(
        default=None, description="Модель классификации; в текущем ответе загрузки возвращается null."
    )
    idempotency_key: str = Field(
        ..., description="Ключ идемпотентности, привязанный к этому запуску."
    )
    status: Literal["queued", "processing", "retrying", "done", "failed"] = Field(
        ..., description="Текущий статус задачи.", examples=["queued"]
    )


class FieldValue(BaseModel):
    name: str = Field(
        ..., description="Внутреннее системное имя поля.", examples=["debtor_full_name"]
    )
    value: Any = Field(
        ..., description="Значение поля: строка, число, boolean, объект, массив или null. Тип и смысл зависят от профиля; перечень приведён в DocumentResultResponse.fields.", examples=["Иванов Иван Иванович"]
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Объяснение модели, почему она извлекла именно это значение.",
        examples=["ФИО должника указано в шапке документа после слова 'Должник:'"],
    )
    confidence: Optional[float] = Field(
        default=None,
        description="Оценка уверенности от 0 до 1 включительно; null для служебных полей. Схемы извлечения отклоняют значения вне диапазона и запрашивают исправление у модели. Ранее сохранённые результаты возвращаются без пересчёта. Это оценка извлечения, а не подтверждение достоверности.",
        examples=[0.95],
    )
    source: Optional[str] = Field(
        default=None,
        description="Обработчик или метод извлечения, например rtk3_combined, rtk2_combined, court_decision_combined, court_decision, llm, llm_with_tools, regex, regex_legacy; null для служебных полей.",
        examples=["rtk3_combined"],
    )


class ValidationIssue(BaseModel):
    code: str = Field(
        ..., description="Код проблемы: missing_required_field, court_field_requires_review, claimed_amount_by_queue_not_determined или claimed_amount_total_mismatch.", examples=["claimed_amount_by_queue_not_determined"]
    )
    message: str = Field(
        ...,
        description="Человекочитаемое сообщение об ошибке.",
        examples=["Обязательное поле 'debtor_inn' не найдено"],
    )
    field_name: Optional[str] = Field(
        default=None,
        description="Имя поля, к которому относится ошибка. Для распределения заявленных сумм РТК3 — claims.",
        examples=["claims"],
    )
    severity: Literal["info", "warning", "error"] = Field(
        default="error", description="Критичность ошибки.", examples=["error"]
    )


class DocumentResultResponse(BaseModel):
    document_id: str = Field(
        ..., description="Внутренний ID документа.", examples=["doc_123abc"]
    )
    pipeline_run_id: str = Field(
        ..., description="ID процесса обработки.", examples=["run_456def"]
    )
    raw_text_version_id: Optional[str] = Field(
        default=None, description="ID версии извлеченного сырого текста."
    )
    structured_version_id: Optional[str] = Field(
        default=None, description="ID версии извлеченных структурированных полей."
    )

    raw_text: Optional[str] = Field(
        default=None, description="Текст документа из текстового слоя PDF, OCR или входного текстового файла; при повторном распознавании возвращается сохранённая исправленная версия."
    )
    fields: Dict[str, FieldValue] = Field(
        default_factory=dict,
        description="""Словарь: системное имя поля → объект FieldValue с name, value, reasoning, confidence и source.
Ниже описаны значения value. Ненайденные значения могут быть null; обязательность в профиле
используется для валидации и не гарантирует, что значение найдено. Даты имеют формат ДД.ММ.ГГГГ,
если для поля не указано иное. Если другой тип не оговорён, value — строка или null.
Набор полей зависит от выбранного профиля.

Общее служебное поле для всех профилей:
* `processing_started_at` — Начало обработки: строка UTC в формате ISO-8601 (без суффикса часового пояса) или null; reasoning, confidence и source равны null.

Для `court_decision` (Судебное решение):
* `debtor_full_name` — Объект с полями-строками: `debtor_last_name` (Фамилия), `debtor_first_name` (Имя), `debtor_patronymic` (Отчество); компоненты или весь объект могут быть null
* `debtor_gender` — Пол должника: "Мужской" или "Женский"
* `debtor_birth_place` — Место рождения должника
* `debtor_birth_date` — Дата рождения должника
* `debtor_snils` — СНИЛС должника
* `debtor_registration_address` — Адрес регистрации должника
* `debtor_inn` — ИНН должника
* `case_number` — Номер дела
* `judge_full_name` — ФИО судьи
* `court_name` — Название суда
* `decision_date` — Дата полного решения
* `resolutive_part_date` — Дата резолютивной части
* `procedure_type` — Тип процедуры (например, реализация имущества)
* `procedure_end_date` — Дата окончания процедуры
* `procedure_end_date_is_calculated` — Признак вычисления даты окончания; текущий ответ содержит строку "True" / "False" или null
* `early_report_deadline` — Заблаговременное предоставление отчета ФУ
* `early_report_required` — Обязанность управляющего заранее предоставить отчёт (JSON boolean: true/false)
* `application_acceptance_date` — Дата принятия заявления о банкротстве (ДД.ММ.ГГГГ или null)
* `next_session_date` — Дата следующего заседания (ДД.ММ.ГГГГ или null)
* `next_session_time` — Время того же заседания (ЧЧ:ММ или null)
* `court_hearing_address` — Адрес проведения того же заседания или null
* `procedure_duration_months` — Явно указанная продолжительность процедуры (integer или null)
* `procedure_end_date_source` — Строка: explicit_date — явно указана дата; explicit_duration — указан срок в месяцах; not_found — не найдены ни дата, ни срок; при неоднозначности null
* `financial_manager_full_name` — ФИО финансового управляющего
* `motivating_part` — Мотивирующая часть судебного решения
* `resolutive_part` — Резолютивная часть судебного решения
* `document_basis` — Основание: "Решение" или "Определение"

Для `rtk` (Заявление о включении в РТК):
* `creditor` — Наименование юридического лица или ФИО кредитора
* `creditor_inn` — ИНН кредитора: строка из 10 или 12 цифр; может уточняться через поиск
* `claims_amount` — Сумма требований: строка с точкой и двумя знаками после точки, например "120000.00"
* `grounds` — Основание возникновения задолженности из допустимых категорий профиля
* `case_number` — Номер арбитражного дела, строка

Для `rtk2` (Судебное определение):
* `case_number` — Номер арбитражного дела, строка
* `decision_date` — Дата вынесения определения
* `debtor_full_name` — Полное ФИО должника в именительном падеже, строка
* `procedure_type` — Действующая на дату определения процедура: "реструктуризация долгов" или "реализация имущества"
* `financial_manager_full_name` — Полное ФИО действующего управляющего в именительном падеже; если известны только фамилия и инициалы — null
* `hearing_date` — Дата ближайшего назначенного заседания; если заседание предполагается, но дата отсутствует — дата определения + 1 месяц; при рассмотрении без заседания — null
* `review_required` — JSON boolean: обязан ли управляющий представить отзыв. Это требование суда, отдельное от верхнеуровневого human_review_required

Для `rtk3` (Включение кредитора ЕФРСБ):
* `inclusion_date` — Дата вынесения определения, строка
* `creditor` — ФИО или наименование кредитора, чьи требования включаются в реестр, строка
* `total_claimed_amount` — Общая заявленная сумма по всему документу, JSON number или null
* `claims` — Массив требований по очередям или null. Структура каждой строки описана ниже
* `grounds` — Основания возникновения задолженности с реквизитами (например, договор займа), строка или null
* `secured_by_pledge` — JSON boolean: true при прямо указанном обеспечении залогом имущества должника, иначе false

Поля каждого объекта `fields.claims.value[]` для РТК3:
* `priority_queue` — Номер/название очереди: строка, например "2 очередь", "3 очередь", "за реестром", или null
* `claimed_amount` — Необязательная первоначально заявленная сумма именно этой очереди до решения суда: string или null. Формат "123456.78": точка, строго два знака, без пробелов, валюты и разделителей тысяч
* `principal_debt` — Фактически включённый основной долг, проценты и госпошлина по очереди; отдельно указанные судебные расходы исключаются. JSON number или null
* `financial_sanctions` — Включённые пени, неустойки и штрафы по очереди, JSON number или null
* `total_amount` — Фактически включённая судом сумма очереди: principal_debt + financial_sanctions, JSON number или null

Для нескольких очередей сумма известных claimed_amount по всем строкам сверяется с total_claimed_amount.
При расхождении human_review_required=true и причина claimed_amount_total_mismatch; суммы сохраняются.
Если распределение не установлено или неоднозначно, неизвестные claimed_amount остаются null:
пропорционального распределения и подстановки включённых сумм нет.
Это требует ручной проверки с причиной claimed_amount_by_queue_not_determined, кроме двух случаев:
одна строка claims; либо несколько строк, все total_amount известны и их сумма равна total_claimed_amount.
Исключения снимают только эту причину проверки; низкое общее качество всё ещё может требовать проверки.

Для `passport_main` (Паспорт РФ — главная страница):
* `passport_series` — Серия паспорта (4 цифры)
* `passport_number` — Номер паспорта (6 цифр)
* `last_name` — Фамилия
* `first_name` — Имя
* `patronymic` — Отчество
* `gender` — Пол: "Мужской" или "Женский"
* `birth_date` — Дата рождения
* `birth_place` — Место рождения
* `issue_date` — Дата выдачи
* `department_code` — Код подразделения
* `issued_by` — Кем выдан

Для `passport_registration` (Паспорт РФ — прописка):
* `registration_address` — Полный адрес регистрации, строка
* `post_index` — Почтовый индекс адреса registration_address: строка из 6 цифр или null; при отсутствии в адресе используется поиск
* `region` — Регион из registration_address, строка или null
* `city` — Город или населённый пункт из registration_address, строка или null
* `street` — Тип и название улицы из registration_address, без дома, корпуса, строения и квартиры, строка или null

Для `unknown` специализированные поля извлечения не настроены.
        """.strip(),
    )

    technical_quality_score: Optional[float] = Field(
        default=None,
        description="Текущая эвристика наличия текста: 0.8 для непустого текста, 0.2 для пустого; null до вычисления.",
        examples=[0.8],
    )
    semantic_confidence_score: Optional[float] = Field(
        default=None,
        description="Доля полей, признанных заполненными текущей эвристикой, в общем числе сохранённых полей, включая служебные. В расчёте используется заполненность value; оценки confidence модели не участвуют.",
        examples=[0.8],
    )
    overall_quality_score: Optional[float] = Field(
        default=None, description="Среднее арифметическое technical_quality_score и semantic_confidence_score.", examples=[0.8]
    )

    validation_status: Literal["ok", "warnings", "errors"] = Field(
        ..., description="Статус проверки: ok или errors. Значение warnings зарезервировано. Статус done у задачи означает завершение обработки и не гарантирует validation_status=ok.", examples=["ok"]
    )
    validation_issues: list[ValidationIssue] = Field(
        default_factory=list, description="Профильные проблемы судебных полей или сумм РТК3. Webhook дополнительно содержит результаты проверки отсутствующих обязательных полей; GET результата сейчас возвращает только профильные проблемы."
    )

    human_review_required: bool = Field(
        ...,
        description="Требуется ли ручная проверка: true при профильной проблеме суда/РТК3, общей оценке ниже 0.75 или отсутствии оценки. Для РТК3 действуют исключения, описанные в fields.",
        examples=[False],
    )
    human_review_reason: Optional[str] = Field(
        default=None, description="Причина: low_quality_or_missing_fields — низкое качество/нет оценки; court_field_requires_review — проблема дополнительных судебных полей; claimed_amount_by_queue_not_determined — не определено распределение заявленной суммы; claimed_amount_total_mismatch — суммы по очередям не совпадают с общей. Если проверка не нужна — null."
    )

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
                            "value": {
                                "debtor_last_name": "Иванов",
                                "debtor_first_name": "Иван",
                                "debtor_patronymic": "Иванович",
                            },
                            "reasoning": "ФИО найдено в шапке после слова 'Должник:'.",
                            "confidence": 0.98,
                            "source": "court_decision_combined",
                        },
                        "debtor_inn": {
                            "name": "debtor_inn",
                            "value": "123456789012",
                            "confidence": 0.95,
                            "source": "court_decision_combined",
                        },
                        "case_number": {
                            "name": "case_number",
                            "value": "А05-6/2025",
                            "confidence": 0.99,
                            "source": "regex_legacy",
                        },
                        "judge_full_name": {
                            "name": "judge_full_name",
                            "value": "Петров П.П.",
                            "confidence": 0.92,
                            "source": "court_decision_combined",
                        },
                        "court_name": {
                            "name": "court_name",
                            "value": "Арбитражный суд города Москвы",
                            "confidence": 0.99,
                            "source": "court_decision_combined",
                        },
                        "decision_date": {
                            "name": "decision_date",
                            "value": "15.03.2025",
                            "confidence": 1.0,
                            "source": "court_decision_combined",
                        },
                        "procedure_type": {
                            "name": "procedure_type",
                            "value": "реализация имущества",
                            "confidence": 0.96,
                            "source": "court_decision_combined",
                        },
                        "procedure_end_date": {
                            "name": "procedure_end_date",
                            "value": "15.09.2025",
                            "confidence": 0.89,
                            "source": "regex_legacy",
                        },
                        "procedure_end_date_is_calculated": {
                            "name": "procedure_end_date_is_calculated",
                            "value": "True",
                            "confidence": 0.95,
                            "source": "regex_legacy",
                        },
                        "early_report_deadline": {
                            "name": "early_report_deadline",
                            "value": "01.09.2025",
                            "confidence": 0.85,
                            "source": "regex_legacy",
                        },
                        "financial_manager_full_name": {
                            "name": "financial_manager_full_name",
                            "value": "Сидоров С.С.",
                            "confidence": 0.95,
                            "source": "court_decision_combined",
                        },
                        "motivating_part": {
                            "name": "motivating_part",
                            "value": "Суд, выслушав доводы сторон, установил следующее...",
                            "confidence": 0.90,
                            "source": "regex",
                        },
                        "resolutive_part": {
                            "name": "resolutive_part",
                            "value": "РЕШИЛ: Признать Иванова И.И. банкротом...",
                            "confidence": 0.99,
                            "source": "regex",
                        },
                    },
                    "technical_quality_score": 0.8,
                    "semantic_confidence_score": 0.8,
                    "overall_quality_score": 0.8,
                    "validation_status": "ok",
                    "human_review_required": False,
                }
            ]
        }
    }


def _rtk3_result_example(*, review: bool) -> dict:
    """Public response examples shared by Swagger's response selector."""
    values = {
        "inclusion_date": "10.09.2026",
        "creditor": "ООО Кредитор",
        "total_claimed_amount": 120000.0,
        "claims": [
            {"priority_queue": "2 очередь", "claimed_amount": None if review else "75000.00",
             "principal_debt": 68962.0, "financial_sanctions": 0.0, "total_amount": 68962.0},
            {"priority_queue": "3 очередь", "claimed_amount": None if review else "45000.00",
             "principal_debt": 28914.0, "financial_sanctions": 14835.0, "total_amount": 43749.0},
        ],
        "grounds": "Договор займа от 15.01.2025",
        "secured_by_pledge": False,
    }
    fields = {
        name: {"name": name, "value": value, "confidence": 0.95,
               "reasoning": "Указано в определении", "source": "rtk3_combined"}
        for name, value in values.items()
    }
    if review:
        fields["claims"]["reasoning"] = "Указана общая заявленная сумма без распределения по очередям"
    fields["processing_started_at"] = {
        "name": "processing_started_at", "value": "2026-10-06T08:00:00",
        "reasoning": None, "confidence": None, "source": None,
    }
    return {
        "document_id": "doc_rtk3", "pipeline_run_id": "run_rtk3",
        "raw_text_version_id": "1", "structured_version_id": "1",
        "raw_text": "Определение о включении требований кредитора в реестр...",
        "fields": fields,
        "technical_quality_score": 0.8,
        "semantic_confidence_score": 5 / 7,
        "overall_quality_score": (0.8 + 5 / 7) / 2,
        "validation_status": "errors" if review else "ok",
        "validation_issues": [{
            "code": "claimed_amount_by_queue_not_determined",
            "message": "Не удалось распределить заявленную сумму между очередями требований.",
            "field_name": "claims", "severity": "error",
        }] if review else [],
        "human_review_required": review,
        "human_review_reason": "claimed_amount_by_queue_not_determined" if review else None,
    }


DOCUMENT_RESULT_EXAMPLES = {
    "court_decision": {
        "summary": "Судебный акт: пример извлечённых полей",
        "value": DocumentResultResponse.model_config["json_schema_extra"]["examples"][0],
    },
    "rtk3": {
        "summary": "РТК3: первоначально заявленные и фактически включённые суммы",
        "value": _rtk3_result_example(review=False),
    },
    "rtk3_review": {
        "summary": "РТК3: распределение неизвестно, нужна ручная проверка",
        "value": _rtk3_result_example(review=True),
    },
}


class PipelineRunStatusResponse(BaseModel):
    pipeline_run_id: str = Field(
        ..., description="ID запущенного процесса обработки.", examples=["run_456def"]
    )
    document_id: str = Field(..., description="ID документа.", examples=["doc_123abc"])
    profile_id: Optional[str] = Field(
        default=None,
        description="ID профиля обработки.",
        examples=["court_decision_ru"],
    )
    status: Literal["queued", "processing", "retrying", "done", "failed"] = Field(
        ...,
        description="Текущий статус обработки. Ждите статус 'done'.",
        examples=["done"],
    )
    retry_count: int = Field(
        default=0, description="Количество попыток повторной обработки при сбоях."
    )
    created_at: str = Field(..., description="Время создания задачи.")
    started_at: Optional[str] = Field(
        default=None, description="Время фактического начала обработки."
    )
    finished_at: Optional[str] = Field(
        default=None, description="Время завершения обработки."
    )
    last_error: Optional[str] = Field(
        default=None, description="Текст последней ошибки, в том числе при status='retrying' или 'failed'."
    )


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
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Максимум run-ов для backfill за один вызов.",
    )
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
