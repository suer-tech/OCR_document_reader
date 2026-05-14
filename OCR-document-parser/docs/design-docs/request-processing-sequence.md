# Диаграммы последовательности обработки запросов

Актуально для асинхронной архитектуры:

- `POST /documents/ingest` только принимает, сохраняет и ставит в очередь (`202`).
- Worker обрабатывает pipeline отдельно.
- Статусы run: `queued -> processing -> done/failed/retrying`.

Основные модули: `api/main.py`, `workers/pipeline_worker.py`, `orchestration/run_processor.py`, `services/*`, `observability/mlflow_client.py`.

## Mermaid

### 1) Ingest + Queue: `POST /documents/ingest`

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client
    participant API as FastAPI
    participant FS as FileStorage
    participant DB as RepositoryDB
    participant MQ as RabbitMQ

    Client->>API: POST /documents/ingest
    API->>API: decode base64 + compute content_hash
    API->>DB: check IngestRequest(idempotency_key)
    alt duplicate request
        DB-->>API: existing document_id + pipeline_run_id
        API-->>Client: 202 Accepted (existing run status)
    else new request
        API->>FS: save_document_bytes(...)
        FS-->>API: storage_path
        API->>DB: insert Document + DocumentFile
        API->>DB: insert PipelineRun(status=queued)
        API->>DB: insert IngestRequest
        API->>MQ: publish IngestJob(pipeline_run_id)
        API-->>Client: 202 Accepted + pipeline_run_id
    end
```

### 2) Worker pipeline execution

```mermaid
sequenceDiagram
    autonumber
    participant Worker as PipelineWorker
    participant MQ as RabbitMQ
    participant DB as RepositoryDB
    participant Router as Router
    participant Engine as PipelineEngine
    participant OCR as OCRService
    participant Intel as DocumentIntelService
    participant TypeSvc as DocumentTypeService
    participant LLM as LLMGateway
    participant Quality as QualityService
    participant Valid as ValidationService
    participant MLF as MLflow

    Worker->>MQ: consume IngestJob
    Worker->>DB: set PipelineRun.status=processing, started_at
    Worker->>DB: load Document + DocumentFile

    Worker->>OCR: extract_text_at_ingest(storage_path, content_type)
    Note over OCR: PDF: текстовый слой → при пустоте OCR\nImage: OCR\nText: чтение файла
    OCR-->>Worker: (extracted_text, ocr_was_used)

    Worker->>Router: resolve_profile(..., detection_text=extracted_text)
    alt document_type missing
        Router->>TypeSvc: detect_document_type(...)
        TypeSvc->>LLM: call_llm_json_with_fallback
        LLM-->>TypeSvc: parsed + attempts + winner
    end
    Worker->>Router: load_profile(profile_id)
    Worker->>Engine: run profile steps

    Note over Worker: extracted_text получен на входе (extract_text_at_ingest)
    Worker->>Intel: simple_extract_fields(text=extracted_text, ...)
    Intel->>LLM: call_llm_json_with_fallback
    LLM-->>Intel: extracted fields / fail

    Worker->>Valid: validate_fields
    Worker->>Quality: compute_quality_scores
    Worker->>DB: save TextVersion + StructuredVersion + QualityScore
    Worker->>DB: set PipelineRun.status=done, finished_at
    Worker->>MLF: async MLflow logging (summary + LLM artifacts)

    alt processing exception
        Worker->>DB: set PipelineRun.status=retrying/failed, last_error
        Worker->>MQ: requeue if retries left
    end
```

### 3) Status / Result / Backfill

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client
    participant API as FastAPI
    participant DB as RepositoryDB
    participant MLF as MLflow

    Client->>API: GET /pipeline-runs/{pipeline_run_id}
    API->>DB: fetch PipelineRun
    API-->>Client: status + started_at/finished_at/last_error

    Client->>API: GET /documents/{document_id}/result
    API->>DB: latest run + versions + quality
    API-->>Client: structured result

    Client->>API: POST /mlflow/backfill
    API->>DB: select done runs (limit)
    loop each run
        API->>MLF: check existing run by tag pipeline_run_id
        alt not exists or force=true
            API->>MLF: log summary payload
        end
    end
    API-->>Client: backfill report (logged/skipped/failed)
```

## PlantUML

### 1) Ingest + Queue: `POST /documents/ingest`

```plantuml
@startuml
autonumber
actor Client
participant "FastAPI" as API
participant "FileStorage" as FS
database "RepositoryDB" as DB
queue "RabbitMQ" as MQ

Client -> API: POST /documents/ingest
API -> API: decode base64 + content_hash
API -> DB: lookup IngestRequest(idempotency_key)
alt duplicate
  DB --> API: existing run
  API --> Client: 202 Accepted (existing)
else new
  API -> FS: save_document_bytes(...)
  FS --> API: storage_path
  API -> DB: insert Document + DocumentFile
  API -> DB: insert PipelineRun(status=queued)
  API -> DB: insert IngestRequest
  API -> MQ: publish IngestJob
  API --> Client: 202 Accepted + pipeline_run_id
end
@enduml
```

### 2) Worker pipeline execution

```plantuml
@startuml
autonumber
participant "PipelineWorker" as Worker
queue "RabbitMQ" as MQ
database "RepositoryDB" as DB
participant "Router" as Router
participant "PipelineEngine" as Engine
participant "OCRService" as OCR
participant "DocumentIntelService" as Intel
participant "DocumentTypeService" as TypeSvc
participant "LLMGateway" as LLM
participant "ValidationService" as Valid
participant "QualityService" as Quality
participant "MLflow" as MLF

Worker -> MQ: consume IngestJob
Worker -> DB: status=processing, started_at
Worker -> DB: load document + file
Worker -> OCR: extract_text_at_ingest(storage_path, content_type)
OCR --> Worker: (extracted_text, ocr_was_used)
Worker -> Router: resolve_profile(..., detection_text=extracted_text)
alt document type missing
  Router -> TypeSvc: detect_document_type(...)
  TypeSvc -> LLM: call_llm_json_with_fallback
  LLM --> TypeSvc: parsed + attempts
end
Worker -> Router: load_profile(profile_id)
Worker -> Engine: run profile steps
Note right of Worker: extracted_text уже получен на входе
Worker -> Intel: simple_extract_fields(text=extracted_text, ...)
Intel -> LLM: call_llm_json_with_fallback
LLM --> Intel: fields / fail
Worker -> Valid: validate_fields
Worker -> Quality: compute_quality_scores
Worker -> DB: persist versions + scores
Worker -> DB: status=done, finished_at
Worker -> MLF: async summary + llm artifacts

alt exception
  Worker -> DB: status=retrying/failed, last_error
  Worker -> MQ: republish if retry
end
@enduml
```

### 3) Status / Result / Backfill

```plantuml
@startuml
autonumber
actor Client
participant "FastAPI" as API
database "RepositoryDB" as DB
participant "MLflow" as MLF

Client -> API: GET /pipeline-runs/{pipeline_run_id}
API -> DB: load PipelineRun
API --> Client: run status DTO

Client -> API: GET /documents/{document_id}/result
API -> DB: latest run + text + structured + quality
API --> Client: DocumentResultResponse

Client -> API: POST /mlflow/backfill
API -> DB: select done runs
loop runs
  API -> MLF: exists by tag pipeline_run_id?
  alt not exists or force
    API -> MLF: log payload sync
  end
end
API --> Client: backfill summary
@enduml
```

