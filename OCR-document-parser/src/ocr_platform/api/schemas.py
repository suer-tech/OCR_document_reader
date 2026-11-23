from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer


SourceType = Literal["crm", "email", "portal", "external", "other"]
ContentType = Literal["pdf", "image", "text", "docx", "doc"]

DOCUMENT_TYPE_DESCRIPTION = (
    "Тип документа: court_decision — судебное решение/определение (профиль court_decision_ru); "
    "rtk — заявление о включении в РТК; rtk2 — судебное определение о рассмотрении требований; "
    "rtk3 — включение требований кредитора в реестр (ЕФРСБ); "
    "passport_main — главная страница паспорта РФ; passport_registration — страница регистрации; "
    "bankruptcy_application — заявление на банкротство, паспортные данные должника из шапки; "
    "procedure_completion — акт о завершении РИ или РД, включая переход РД → РИ (явный выбор); "
    "financial_manager_report — отчёт финансового управляющего о результатах РИ или РД (явный выбор); "
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
        ..., description="Тип содержимого: pdf, image, text, docx или doc."
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
        description="Причина выбора значения. В новых результатах court_decision включает дословную цитату (для длинных частей — отмеченные фрагменты границ); при отсутствии/неоднозначности объясняет null, при расчёте — исходные факты и правило расчёта.",
        examples=["ФИО должника указано в шапке документа после слова 'Должник:'"],
    )
    confidence: Optional[float] = Field(
        default=None,
        description="Оценка уверенности от 0 до 1 включительно; null для служебных полей. Для extraction_method=regex_jev это вероятность подтверждения значения моделью JEV (текущий порог — 0.6), для LLM — оценка извлечения. В старых результатах метод хранится в source. Уверенно установленное отсутствие допускает высокую confidence при value=null. Оценка не гарантирует достоверность.",
        examples=[0.95],
    )
    source: Optional[str] = Field(
        default=None,
        description="Для court_decision и procedure_completion — часть исходного документа: header, motivating_part, resolutive_part, signature_block, document (проверен документ целиком), либо null, если источник значения не установлен. В старых записях и других профилях сохраняется прежнее обозначение обработчика. Исторические ответы не мигрируются.",
        examples=["resolutive_part"],
    )
    extraction_method: Optional[str] = Field(
        default=None,
        description="Способ извлечения поля. Для court_decision — regex_jev, court_llm_fallback, regex_legacy и др. Для procedure_completion — llm или calculation. null для служебных полей, старых записей и профилей, ещё не передающих метод отдельно.",
        examples=["regex_jev"],
    )
    evidence: dict[str, Any] | None = Field(
        default=None,
        description="Для отчёта финансового управляющего: page (номер страницы PDF или кадра изображения с 1, "
        "для DOCX, DOC и текста — номер текстового блока 1), "
        "table, row, column или quote. В таблицах источники по ячейкам лежат в value.rows[].sources.",
    )

    @field_validator("evidence", mode="before")
    @classmethod
    def preserve_structured_evidence(cls, value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    @model_serializer(mode="wrap")
    def serialize_field(self, handler):
        data = handler(self)
        if self.evidence is None:
            data.pop("evidence", None)
        return data


class ValidationIssue(BaseModel):
    code: str = Field(
        ..., description="Код проблемы: missing_required_field, court_field_requires_review, claimed_amount_by_queue_not_determined, claimed_amount_total_mismatch, rtk3_requires_review и др. Для procedure_completion: procedure_completion_requires_review или partial_debt_release.", examples=["claimed_amount_by_queue_not_determined", "partial_debt_release"]
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
        description="""Словарь: системное имя поля → объект FieldValue с name, value, reasoning, confidence, source и extraction_method.
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
* `resolutive_part_date` — Необязательная явно указанная дата резолютивной части или дата шапки самостоятельной резолютивной части; без такого источника null, дата полного решения не подставляется
* `procedure_type` — «Реализация имущества» или «Реструктуризация долгов», либо null
* `procedure_end_date` — Дата окончания процедуры
* `procedure_end_date_is_calculated` — JSON boolean: true, если дата окончания вычислена по сроку в месяцах; false, если дата явно указана; null, если окончание не определено
* `early_report_deadline` — Явно указанный срок отчёта; без него при требовании заранее/за N дней — next_session_date минус 10 календарных дней, иначе null
* `early_report_required` — Обязанность управляющего заранее предоставить отчёт (JSON boolean: true/false)
* `application_acceptance_date` — Дата принятия заявления о банкротстве (ДД.ММ.ГГГГ или null)
* `next_session_date` — Явная дата следующего рассмотрения; если не найдена, дата окончания процедуры по согласованному правилу (ДД.ММ.ГГГГ или null)
* `next_session_time` — Время того же заседания (ЧЧ:ММ или null)
* `court_hearing_address` — Адрес проведения того же заседания или null
* `procedure_duration_months` — Явно назначенная продолжительность процедуры в месяцах (integer или null), без расчёта из дат и значения по умолчанию. Regex-кандидат проверяется JEV; при отсутствии подтверждения поле извлекает LLM
* `procedure_end_date_source` — Строка: explicit_date — явно указана дата; explicit_duration — указан срок в месяцах; not_specified — не найдены ни дата, ни срок; при неоднозначности null
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
* `case_number` — Основной номер дела о банкротстве из шапки, без номера обособленного спора; пробелы нормализованы
* `decision_date` — Дата текущего определения из шапки, ДД.ММ.ГГГГ
* `creditor_name` — Наиболее полное наименование кредитора текущего требования, по возможности в именительном падеже; строка или null
* `creditor_application_date` — Дата подачи заявления именно этим кредитором, ДД.ММ.ГГГГ или null; не дата заявления должника, публикации или рассмотрения
* `debtor_full_name` — Полное ФИО должника в именительном падеже, строка
* `procedure_type` — Действующая на дату определения процедура: "реструктуризация долгов" или "реализация имущества"
* `financial_manager_full_name` — Полное ФИО действующего управляющего в именительном падеже; если известны только фамилия и инициалы — null
* `hearing_date` — Только явно назначенная дата заседания по текущему требованию; без заседания или без явной даты — null. Не вычисляется и не подменяется сроками отзыва/рассмотрения
* `review_required` — JSON boolean или null: прямая обязанность должника/управляющего представить отзыв, возражения или заменяющие отзыв пояснения. Общие права и служебные фразы не считаются обязанностью; неоднозначность → null и human_review_required=true. Reasoning содержит конкретную фразу

Неизвестные обязательные значения RTK2 и неоднозначность возвращаются с validation_status=errors,
human_review_required=true и human_review_reason=rtk2_requires_review. Отсутствие заседания допустимо.

Для `rtk3` (Включение кредитора ЕФРСБ), режим `regex_jev`. Источник полей новых результатов — `rtk3_evidence_v2`.
* `document_case_number` — Номер конкретного спора из определения, включая суффикс
* `case_number` — Основное дело о банкротстве
* `debtor_inn` — ИНН должника, строка из 12 цифр с ведущими нулями
* `creditor_inn` — ИНН кредитора, строка из 10 или 12 цифр с ведущими нулями. Для «ФНС России в лице…» берётся ИНН названного подразделения. Если указана только «ФНС России», берётся ИНН федеральной службы. Недостающий ИНН может быть подтверждён по наименованию через справочник
* `inclusion_date` — Дата определения, ДД.ММ.ГГГГ. При явно указанной дате изготовления полного текста используется она
* `kad_publication_date` — Всегда null
* `creditor` — Наименование кредитора из акта с указанной правовой формой. Для налогового органа сохраняется написание из акта, включая «в лице…» с названным подразделением
* `grounds` — Кредитный договор / Договор займа / Налоговая задолженность / Оплата коммунальных услуг. Категория определяется по происхождению признанного долга. Несколько кредитных договоров дают одну категорию «Кредитный договор». Если основание установить нельзя — null
* `grounds_ocr` — Дословная формулировка основания включённой суммы с реквизитами из документа либо null
* `pledge_resolution` — Общий статус yes / no / separated по совпадающим статусам строк или самостоятельному решению суда по всему требованию. При противоречии или неоднозначности возвращается null.
* `secured_by_pledge` — true при общем статусе yes, false при no или separated, null при неизвестном или неоднородном статусе
* `total_claimed_amount` — По актуальной подтверждённой форме ЕМУ: признанная судом сумма без отдельно выделенной пошлины по текущему спору, не первоначально запрошенная сумма
* `claims` — Все включённые требования. Разные договоры передаются отдельными строками, в том числе в одной очереди

Поля каждого объекта `claims`:
* `priority_queue` — 1 очередь / 2 очередь / 3 очередь / 4 очередь / За реестром / Иное. При неизвестной очереди возвращается null
* `contract_number` — Номер договора из акта или null
* `pledge_resolution` — yes при признании залога, no при прямом отказе, указании на необеспеченность либо окончательном включении обычного требования в полном акте без упоминания залога. separated при отдельном рассмотрении вопроса, null при недостаточных данных
* `secured_by_pledge` — true при статусе yes, false при no или separated, null при неизвестном статусе
* `principal_debt` — Основной долг, обычные проценты и ранее присуждённые расходы в составе принятого требования, без пошлины по текущему спору. Если строка состоит только из санкций, передаётся "0.00"
* `financial_sanctions` — Все неустойки, пени, штрафы и иные финансовые санкции
* `pau_only_amount` — Отдельно присуждённая пошлина по текущему спору, без двойного счёта
* `claimed_amount` — По подтверждённой форме ЕМУ: principal_debt + financial_sanctions
* `total_amount` — principal_debt + financial_sanctions + pau_only_amount
* `is_salary_or_severance` — true при подтверждённой зарплате или выходном пособии, false при подтверждённой иной природе требования, null при неопределённости
Все денежные значения новых результатов — строки "123456.78" или null; точка, два знака,
без разделителей тысяч. Неизвестные суммы не заменяются нулём и требуют ручной проверки.
Ошибка суммы, цитаты, полноты или реквизитов: validation_status=errors,
human_review_required=true, human_review_reason=rtk3_requires_review независимо от quality.
Допустимые null в необязательных полях и подтверждённые значения false сохраняются. Оценка заполненности учитывает обязательные поля.
Нужные внешние данные обозначаются validation_issues.code=rtk3_external_data_required
с severity=warning; значение поля остаётся null. Если иных ошибок нет,
validation_status=warnings. Отсутствующий ИНН участника
требует внешнего дополнения: human_review_reason=rtk3_external_data_required.
Внешние значения ЕМУ не подставляются в поля с источником документного извлечения.
Показатели efrsb_total_amount/pau_total_amount и подтверждения amount_mismatch_* формирует ЕМУ.
Сохранённые исторические результаты rtk3_combined сохраняют исходные суммы и строки требований;
в них деньги могут быть JSON number, а claimed_amount означает первоначально заявленную сумму.
Для них сохраняются прежние проверки claimed_amount; дополнительно проверяется арифметика.

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

Для `bankruptcy_application` (Заявление на банкротство):
Активный обработчик: Gemini Argon (`google/gemini-4-argon`) через RouterAI.
extraction_method=llm, source=bankruptcy_llm. Локальный парсер полей не используется.
confidence — самооценка Argon для каждого поля от 0 до 1, в том числе для
уверенно установленного отсутствия (value=null). Оценка не является проверенной
вероятностью; при локальном отклонении значения или отсутствии ответа confidence=null.
PDF без текста, смешанные PDF и фото отправляются Argon изображениями страниц,
без промежуточного Gemini OCR/Tesseract. PDF с текстом и DOCX передают модели текст.
* `passport_series` — Серия паспорта РФ, 4 цифры; null для иностранного паспорта.
* `passport_number` — Номер паспорта РФ, 6 цифр; полный номер иностранного паспорта.
* `last_name` — Фамилия должника, первое написание перед фамилией в скобках.
* `first_name` — Имя должника.
* `patronymic` — Отчество должника либо null.
* `gender` — Мужской/Женский по ФИО либо null при неоднозначности.
* `birth_date` — Дата рождения, ДД.ММ.ГГГГ.
* `birth_place` — Место рождения из шапки.
* `issue_date` — Дата выдачи паспорта, ДД.ММ.ГГГГ либо null.
* `department_code` — Код подразделения XXX-XXX либо null.
* `issued_by` — Наименование органа выдачи из шапки.
* `registration_address` — Полный адрес регистрации должника.
* `post_index` — Индекс из адреса регистрации, 6 цифр либо null.
* `region` — Регион из адреса регистрации либо null.
* `city` — Населённый пункт из адреса регистрации либо null.
* `street` — Улица без дома/корпуса/квартиры либо null.
* Иностранный паспорт: полный passport_number, passport_series=null; код подразделения необязателен.
* gender определяет модель по ФИО, при неоднозначности null. Отсутствующие сведения не дополняются из тела заявления.
* Адрес для корреспонденции и фактическое проживание не заменяют registration_address.
* post_index берётся только из адреса регистрации, без внешнего поиска.
* Недостающие обязательные поля и неоднозначность требуют ручной проверки независимо от quality.
* PDF с текстовым слоем, текст и DOCX читаются напрямую. PDF без читаемого текста и
  изображения используют штатный Gemini OCR через RouterAI и требуют сверки результата.

Для `procedure_completion` (Акт о завершении процедуры):
Укажите тип явно при отправке. Gemini Argon (`google/gemini-4-argon`) через RouterAI
извлекает значения по всему акту с дословными цитатами. `confidence` — оценка модели от 0 до 1.
Даты имеют формат YYYY-MM-DD. Поля о новой процедуре относятся к переходу РД → РИ.
* `document_is_completion_act` — Подтверждено ли завершение процедуры, boolean/null.
* `document_type` — determination, decision, resolutive_only, other или null.
* `document_title` — Дословный заголовок.
* `case_number_raw` — Номер дела в написании суда.
* `case_number` — Нормализованный номер текущего дела с четырёхзначным годом.
* `court_name` — Название суда.
* `judge_name` — ФИО или инициалы судьи.
* `judicial_act_date` — Дата текущего акта или его полного текста.
* `resolutive_part_date` — Явно указанная дата объявления резолюции.
* `debtor_full_name` — ФИО должника в именительном падеже.
* `debtor_aliases` — Массив прежних фамилий и вариантов ФИО либо null при неопределённости.
* `debtor_inn` — ИНН должника, строка из 12 цифр с сохранением ведущего нуля.
* `debtor_snils` — СНИЛС должника, строка или null.
* `financial_manager_full_name` — Действующий управляющий завершённой процедуры.
* `completed_procedure_type` — realization, restructuring или null.
* `completion_result` — completed, completed_and_realization_started, not_completed, unclear или null.
* `next_procedure_type` — realization при переходе РД → РИ, иначе null.
* `resolutive_part_text` — Дословный текст распоряжений суда.
* `debt_release_status` — applies, does_not_apply, partially_applies, not_specified или null. null при завершении РД и при отсутствии подтверждённого завершения РИ. not_specified при завершении РИ, если результат освобождения не установлен, с ручной проверкой. В сохранённых ответах прежней версии встречаются not_applicable и unclear.
* `debt_release_text` — Полный дословный абзац резолюции об освобождении. Из value этого поля ЕФРСБ получает «Причину неприменения освобождения».
* `debt_release_reason_text` — Дословный фрагмент мотивировки суда, если указан.
* `excluded_creditors` — Массив исключений. В каждой строке creditor_name, creditor_inn, amount (десятичная строка в рублях), дословный restriction_text и прежние basis_act_date и basis_text. Неустановленные имя, ИНН, дата и сумма имеют значение null. При отсутствии подтверждённых строк возвращается [].
* `secured_creditor_settlement_approved` — true при подтверждённом утверждении мирового соглашения с залоговым кредитором, false при установленном отсутствии в полном акте, null при неопределённости. Значение независимо от debt_release_status.
* `secured_creditor_settlement_text` — Дословный фрагмент резолюции об утверждении мирового соглашения с залоговым кредитором либо null.
* `new_procedure_start_date` — Дата начала новой реализации имущества.
* `new_procedure_end_date` — Дата окончания новой реализации имущества.
* `new_procedure_end_date_is_calculated` — true для расчётной даты, false для прямо назначенной, иначе null.
* `next_session_date` — Дата следующего назначенного заседания.
* `next_session_time` — Время заседания, ЧЧ:ММ.
* `next_session_address` — Адрес заседания.
* `intermediate_report_required` — Требование о промежуточном отчёте, boolean/null.
* `intermediate_report_deadline` — Срок подачи промежуточного отчёта.

Для полей RouterAI с required=true в профиле пустое значение проверяется повторно:
до 3 дополнительных запросов после первого ответа. Повторяются только недостающие
обязательные поля; заполненные значения сохраняются. Необязательные null, а также
корректные 0 и false не запускают повторы пустых полей. Это отдельный механизм от
сетевых повторов SDK и очереди. После исчерпания попыток недостающее значение
остаётся null; действующие проверки обязательности/ручной проверки сохраняются.

Для `financial_manager_report` (Отчёт финансового управляющего):
Тип указывается явно. Для PDF и изображений обрабатываются страницы. Для DOCX, DOC и текстового файла
используется прочитанный текст как блок 1. Суммы передаются строками с точкой и двумя знаками после неё.
Реквизиты могут содержать `FieldValue.evidence`. Табличное значение имеет вид `{status, source, rows}`.
`source` — объект с номером страницы `page` и доступными `table`, `row`, `column`, `quote` или null.
`status` — `listed` (строки распознаны), `none_declared` (явно заявлено отсутствие строк),
`not_found` (раздел не найден), `unreadable` (раздел нечитаем), `partial` (результат неполный).
В каждой строке `sources` хранит источники отдельных полей, а `null_reasons` — причины отсутствующих
или неподтверждённых значений: `not_stated`, `unreadable`, `ambiguous`, `not_applicable`, `unsupported_source`.

* `case_number` — Номер текущего дела из шапки отчёта.
* `debtor_full_name` — ФИО должника одной строкой.
* `debtor_inn` — Напечатанный ИНН должника, строка из 12 цифр с ведущими нулями или null.
* `procedure_type` — `РИ` (реализация имущества) или `РД` (реструктуризация долгов).
* `report_date` — Дата составления отчёта в формате YYYY-MM-DD.
* `inventory_date` — Дата проведения описи в формате YYYY-MM-DD или null.
* `property_inventory` — Состав имущества по описи. `rows` содержит строки объектов.
  * `name` — Наименование объекта.
  * `manager_valuation` — Стоимость по оценке финансового управляющего.
  * `appraiser_valuation` — Стоимость по оценке оценщика.
  * `sole_home` — Признак единственного жилья, boolean/null.
  * `secured_by_pledge` — Признак залога объекта, boolean/null.
  * `pledgee` — Залогодержатель по строке объекта.
  * `pledge_details` — Дополнительные сведения о залоге.
  * `exclusion_text` — Дословная формулировка об исключении имущества.
  * `exclusion_basis` — Указанное основание или документ об исключении.
  * `sources` — Источники по полям объекта.
  * `null_reasons` — Причины пропусков по полям объекта.
* `property_sales` — Реализация имущества. `rows` содержит строки таблицы продаж.
  * `name` — Наименование объекта в таблице реализации.
  * `sale_method` — Указанный способ продажи.
  * `sale_result` — Исход торгов или продажи по отчёту.
  * `contract_price` — Стоимость имущества по договору.
  * `paid_amount` — Фактическая оплата из таблицы реализации.
  * `unsold_return_text` — Формулировка о несостоявшихся торгах и возврате имущества должнику.
  * `sources` — Источники по полям строки реализации.
  * `null_reasons` — Причины пропусков по полям строки реализации.
* `creditor_register` — Реестр требований и блок за реестром. `rows` содержит требования и напечатанные итоги.
  * `row_kind` — `detail` (требование), `subtotal` (промежуточный итог) или `total` (общий итог).
  * `source` — Источник строки целиком или null.
  * `queue` — Очередь требования.
  * `queue_part` — Часть очереди.
  * `creditor_name` — Наименование кредитора.
  * `claim_type` — Вид требования в формулировке отчёта.
  * `claim_category` — `secured` (залог), `principal` (основной долг), `sanctions` (санкции),
    `salary_or_severance` (зарплата или выходное пособие), `procedure_interest` (проценты в ходе процедуры) или `other`.
  * `secured_by_pledge` — Признак обеспечения требования залогом, boolean/null.
  * `outside_register` — true за реестром, false внутри реестра, null для смешанного итога или при неопределённости.
  * `included_amount` — Сумма требования из соответствующей колонки.
  * `paid_amount` — Сумма погашения требования.
  * `sources` — Источники по полям строки реестра.
  * `null_reasons` — Причины пропусков по полям строки реестра.
* `current_payments` — Текущие платежи и расходы. `rows` содержит обязательства и напечатанные итоги.
  * `row_kind` — `detail` (обязательство), `subtotal` (промежуточный итог) или `total` (общий итог).
  * `source` — Источник строки целиком или null.
  * `queue` — Очередь текущего платежа.
  * `name` — Наименование обязательства или расхода.
  * `expense_category` — `manager_remuneration` (вознаграждение), `engaged_services` (привлечённые лица) или `other`.
  * `obligation_amount` — Размер обязательства из одноимённой колонки.
  * `paid_amount` — Сумма погашения обязательства.
  * `unpaid_balance` — Непогашенный остаток на дату отчёта.
  * `sources` — Источники по полям строки платежа.
  * `null_reasons` — Причины пропусков по полям строки платежа.
* `report_text_complete` — Boolean. true, если прочитан текстовый вход или все страницы прочитаны полностью либо подтверждены пустыми.
  В `raw_text` возвращается прочитанный текст. Для PDF и изображений добавляются номера страниц и отметки о пропусках.

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
        description="Для текущего РТК3 — доля заполненных обязательных полей без замечания в самом поле. Для procedure_completion — средняя confidence обязательных полей, пропуск или ошибка считается за 0. Для других профилей и исторических форматов — доля заполненных полей, включая служебные.",
        examples=[0.8],
    )
    overall_quality_score: Optional[float] = Field(
        default=None, description="Среднее арифметическое technical_quality_score и semantic_confidence_score.", examples=[0.8]
    )

    validation_status: Literal["ok", "warnings", "errors"] = Field(
        ..., description="Статус проверки: ok, warnings или errors. В RTK3 warnings может обозначать необходимость внешних метаданных, отсутствующих в документе. Статус done у задачи означает завершение обработки и не гарантирует validation_status=ok.", examples=["ok"]
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
        default=None, description=(
            "Причина ручной проверки. low_quality_or_missing_fields означает низкую или отсутствующую оценку. "
            "court_field_requires_review указывает на проблему дополнительных судебных полей. "
            "rtk3_requires_review означает ошибку проверки РТК3. rtk3_external_data_required означает, "
            "что ИНН нужно дополнить из внешнего источника. Для исторических результатов РТК3 "
            "claimed_amount_by_queue_not_determined означает отсутствие распределения заявленной суммы по очередям. "
            "claimed_amount_total_mismatch означает расхождение сумм по очередям с общей суммой. "
            "Если проверка не нужна, возвращается null."
        )
    )

    model_config = {'json_schema_extra': {'examples': [{'document_id': 'doc_123abc',
                                         'pipeline_run_id': 'run_456def',
                                         'raw_text': 'Арбитражный суд города Москвы\n'
                                                     'РЕШЕНИЕ\n'
                                                     'Дело № А40-123/2026\n'
                                                     '01 октября 2026 года\n'
                                                     'Судья Петров П.П.\n'
                                                     'Должник: Иванов Иван Иванович, ИНН 051204647801.\n'
                                                     'УСТАНОВИЛ:\n'
                                                     'Суд исследовал материалы дела и установил '
                                                     'неплатёжеспособность должника.\n'
                                                     'РЕШИЛ:\n'
                                                     'Признать Иванова Ивана Ивановича банкротом.\n'
                                                     'Ввести процедуру реализации имущества сроком на шесть '
                                                     'месяцев.\n'
                                                     'Утвердить финансовым управляющим Сидорова С.С.\n'
                                                     'Финансовому управляющему заблаговременно представить '
                                                     'отчёт.',
                                         'fields': {'debtor_full_name': {'name': 'debtor_full_name',
                                                                         'value': {'debtor_last_name': 'Иванов',
                                                                                   'debtor_first_name': 'Иван',
                                                                                   'debtor_patronymic': 'Иванович'},
                                                                         'reasoning': 'ФИО должника приведено к '
                                                                                      'именительному падежу. '
                                                                                      'Цитата из документа: '
                                                                                      '«Должник: Иванов Иван '
                                                                                      'Иванович».',
                                                                         'confidence': 0.98,
                                                                         'source': 'header',
                                                                         'extraction_method': 'regex_jev'},
                                                    'debtor_inn': {'name': 'debtor_inn',
                                                                   'value': '051204647801',
                                                                   'confidence': 0.95,
                                                                   'source': 'header',
                                                                   'extraction_method': 'regex_jev',
                                                                   'reasoning': 'Выбран ИНН должника, ведущий '
                                                                                'ноль сохранён. Цитата из '
                                                                                'документа: «ИНН 051204647801».'},
                                                    'case_number': {'name': 'case_number',
                                                                    'value': 'А40-123/2026',
                                                                    'confidence': 0.99,
                                                                    'source': 'header',
                                                                    'extraction_method': 'regex_jev',
                                                                    'reasoning': 'Выбран номер текущего дела. '
                                                                                 'Цитата из документа: «Дело № '
                                                                                 'А40-123/2026».'},
                                                    'judge_full_name': {'name': 'judge_full_name',
                                                                        'value': 'Петров П.П.',
                                                                        'confidence': 0.92,
                                                                        'source': 'header',
                                                                        'extraction_method': 'regex_jev',
                                                                        'reasoning': 'Выбран судья текущего '
                                                                                     'акта, инициалы не '
                                                                                     'раскрыты. Цитата из '
                                                                                     'документа: «Судья Петров '
                                                                                     'П.П.».'},
                                                    'court_name': {'name': 'court_name',
                                                                   'value': 'Арбитражный суд города Москвы',
                                                                   'confidence': 0.99,
                                                                   'source': 'header',
                                                                   'extraction_method': 'regex_jev',
                                                                   'reasoning': 'Выбран суд текущего акта. '
                                                                                'Цитата из документа: '
                                                                                '«Арбитражный суд города '
                                                                                'Москвы».'},
                                                    'decision_date': {'name': 'decision_date',
                                                                      'value': '01.10.2026',
                                                                      'confidence': 1.0,
                                                                      'source': 'header',
                                                                      'extraction_method': 'regex_jev',
                                                                      'reasoning': 'Выбрана дата текущего акта. '
                                                                                   'Цитата из документа: «01 '
                                                                                   'октября 2026 года».'},
                                                    'procedure_type': {'name': 'procedure_type',
                                                                       'value': 'Реализация имущества',
                                                                       'confidence': 0.96,
                                                                       'source': 'resolutive_part',
                                                                       'extraction_method': 'regex_jev',
                                                                       'reasoning': 'Название введённой '
                                                                                    'процедуры приведено к '
                                                                                    'формату ТЗ. Цитата из '
                                                                                    'документа: «Ввести '
                                                                                    'процедуру реализации '
                                                                                    'имущества сроком на шесть '
                                                                                    'месяцев.».'},
                                                    'procedure_end_date': {'name': 'procedure_end_date',
                                                                           'value': '01.04.2027',
                                                                           'confidence': 0.89,
                                                                           'source': 'resolutive_part',
                                                                           'extraction_method': 'regex_jev',
                                                                           'reasoning': 'Окончание рассчитано: '
                                                                                        '01.10.2026 + 6 месяцев. '
                                                                                        'Основание даты акта: '
                                                                                        '«01 октября 2026 года». '
                                                                                        'Цитата из документа: '
                                                                                        '«Ввести процедуру '
                                                                                        'реализации имущества '
                                                                                        'сроком на шесть '
                                                                                        'месяцев.».'},
                                                    'procedure_end_date_is_calculated': {'name': 'procedure_end_date_is_calculated',
                                                                                         'value': True,
                                                                                         'confidence': 0.95,
                                                                                         'source': 'resolutive_part',
                                                                                         'extraction_method': 'regex_jev',
                                                                                         'reasoning': 'Дата '
                                                                                                      'окончания '
                                                                                                      'вычислена '
                                                                                                      'по '
                                                                                                      'продолжительности, '
                                                                                                      'а не '
                                                                                                      'напечатана '
                                                                                                      'как '
                                                                                                      'готовая '
                                                                                                      'дата. '
                                                                                                      'Цитата из '
                                                                                                      'документа: '
                                                                                                      '«Ввести '
                                                                                                      'процедуру '
                                                                                                      'реализации '
                                                                                                      'имущества '
                                                                                                      'сроком на '
                                                                                                      'шесть '
                                                                                                      'месяцев.».'},
                                                    'early_report_deadline': {'name': 'early_report_deadline',
                                                                              'value': '22.03.2027',
                                                                              'confidence': 0.85,
                                                                              'source': 'resolutive_part',
                                                                              'extraction_method': 'regex_jev',
                                                                              'reasoning': 'Срок рассчитан по '
                                                                                           'текущему правилу: '
                                                                                           'подставленная '
                                                                                           'следующая дата '
                                                                                           '01.04.2027 минус 10 '
                                                                                           'календарных дней. '
                                                                                           'Цитата из документа: '
                                                                                           '«Финансовому '
                                                                                           'управляющему '
                                                                                           'заблаговременно '
                                                                                           'представить '
                                                                                           'отчёт.».'},
                                                    'financial_manager_full_name': {'name': 'financial_manager_full_name',
                                                                                    'value': 'Сидоров С.С.',
                                                                                    'confidence': 0.95,
                                                                                    'source': 'resolutive_part',
                                                                                    'extraction_method': 'court_llm_fallback',
                                                                                    'reasoning': 'Выбран утверждённый управляющий. Цитата из документа: «Утвердить финансовым управляющим Сидорова С.С.».'},
                                                    'motivating_part': {'name': 'motivating_part',
                                                                        'value': 'Суд исследовал материалы дела '
                                                                                 'и установил '
                                                                                 'неплатёжеспособность должника.',
                                                                        'confidence': 0.9,
                                                                        'source': 'motivating_part',
                                                                        'extraction_method': 'regex_jev',
                                                                        'reasoning': 'Возвращена мотивировка в '
                                                                                     'границах УСТАНОВИЛ — '
                                                                                     'РЕШИЛ. Цитата из '
                                                                                     'документа: «Суд исследовал '
                                                                                     'материалы дела и установил '
                                                                                     'неплатёжеспособность '
                                                                                     'должника.».'},
                                                    'resolutive_part': {'name': 'resolutive_part',
                                                                        'value': 'Признать Иванова Ивана '
                                                                                 'Ивановича банкротом.\n'
                                                                                 'Ввести процедуру реализации '
                                                                                 'имущества сроком на шесть '
                                                                                 'месяцев.\n'
                                                                                 'Утвердить финансовым управляющим Сидорова С.С.\n'
                                                                                 'Финансовому управляющему '
                                                                                 'заблаговременно представить '
                                                                                 'отчёт.',
                                                                        'confidence': 0.99,
                                                                        'source': 'resolutive_part',
                                                                        'extraction_method': 'regex_jev',
                                                                        'reasoning': 'Возвращена резолютивная '
                                                                                     'часть после РЕШИЛ. Цитата '
                                                                                     'из документа: «Признать '
                                                                                     'Иванова Ивана Ивановича '
                                                                                     'банкротом.».'}},
                                         'technical_quality_score': 0.8,
                                         'semantic_confidence_score': 0.8,
                                         'overall_quality_score': 0.8,
                                         'validation_status': 'ok',
                                         'human_review_required': False}]}}


def _rtk3_result_example(*, review: bool) -> dict:
    """Public response examples shared by Swagger's response selector."""
    values = {
        "document_case_number": None if review else "А40-123/2026-1-2Ф",
        "case_number": "А40-123/2026",
        "debtor_inn": "012345678901",
        "creditor_inn": "0123456789",
        "inclusion_date": "10.09.2026",
        "kad_publication_date": None,
        "creditor": "ООО «Пример»",
        "grounds": None,
        "grounds_ocr": None,
        "pledge_resolution": None,
        "secured_by_pledge": None,
        "total_claimed_amount": "1525.00",
        "claims": [
            {
                "priority_queue": "3 очередь", "contract_number": "А-1",
                "principal_debt": "1000.00", "financial_sanctions": "25.00", "pau_only_amount": "15.00",
                "claimed_amount": "1025.00", "total_amount": "1040.00", "is_salary_or_severance": False,
                "pledge_resolution": "yes", "secured_by_pledge": True,
            },
            {
                "priority_queue": "3 очередь", "contract_number": "А-2",
                "principal_debt": "500.00", "financial_sanctions": "0.00", "pau_only_amount": "0.00",
                "claimed_amount": "500.00", "total_amount": "500.00", "is_salary_or_severance": False,
                "pledge_resolution": "no", "secured_by_pledge": False,
            },
        ],
    }
    fields = {
        name: {"name": name, "value": value, "confidence": None,
               "reasoning": "Извлечено из определения" if value is not None else None, "source": "rtk3_evidence_v2"}
        for name, value in values.items()
    }
    fields["kad_publication_date"]["extraction_method"] = "fixed"
    fields["processing_started_at"] = {
        "name": "processing_started_at", "value": "2026-10-06T08:00:00",
        "reasoning": None, "confidence": None, "source": None,
    }
    semantic = 7 / 8 if review else 1.0
    return {
        "document_id": "doc_rtk3", "pipeline_run_id": "run_rtk3",
        "raw_text_version_id": "1", "structured_version_id": "1",
        "raw_text": "Определение о включении требований кредитора в реестр...",
        "fields": fields,
        "technical_quality_score": 0.8,
        "semantic_confidence_score": semantic,
        "overall_quality_score": (0.8 + semantic) / 2,
        "validation_status": "errors" if review else "ok",
        "validation_issues": [{
            "code": "rtk3_requires_review",
            "message": "Не установлено обязательное значение РТК3: document_case_number.",
            "field_name": "document_case_number", "severity": "error",
        }] if review else [],
        "human_review_required": review,
        "human_review_reason": "rtk3_requires_review" if review else None,
    }


DOCUMENT_RESULT_EXAMPLES = {
    "court_decision": {
        "summary": "Судебный акт: пример извлечённых полей",
        "value": DocumentResultResponse.model_config["json_schema_extra"]["examples"][0],
    },
    "rtk3": {
        "summary": "РТК3: два договора одной очереди с разным залоговым статусом",
        "value": _rtk3_result_example(review=False),
    },
    "rtk3_review": {
        "summary": "РТК3: отсутствует номер определения, нужна ручная проверка",
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
