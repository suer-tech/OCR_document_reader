# Схема базы данных OCR-platform

Источник: `src/ocr_platform/storage/models.py` (SQLAlchemy ORM).

## ER-диаграмма (Mermaid)

```mermaid
erDiagram
    DOCUMENTS ||--o{ DOCUMENT_FILES : has
    DOCUMENTS ||--o{ PIPELINE_RUNS : has
    PIPELINE_RUNS ||--o{ PIPELINE_EVENTS : has
    PIPELINE_RUNS ||--o{ TEXT_VERSIONS : has
    PIPELINE_RUNS ||--o{ STRUCTURED_VERSIONS : has
    PIPELINE_RUNS ||--o{ QUALITY_SCORES : has
    DOCUMENTS ||--o{ TEXT_VERSIONS : has
    DOCUMENTS ||--o{ STRUCTURED_VERSIONS : has
    DOCUMENTS ||--o{ HUMAN_REVIEW_TASKS : has
    PIPELINE_RUNS ||--o{ HUMAN_REVIEW_TASKS : has
    HUMAN_REVIEW_TASKS ||--o{ HUMAN_REVIEW_ACTIONS : has
    STRUCTURED_VERSIONS ||--o{ HUMAN_REVIEW_ACTIONS : references

    DOCUMENTS {
      string id PK
      string source_type
      string document_type "nullable"
      datetime created_at
    }

    DOCUMENT_FILES {
      int id PK
      string document_id FK
      string storage_path
      string file_type
    }

    PIPELINE_RUNS {
      string id PK
      string document_id FK
      string profile_id
      string status
      string idempotency_key "nullable"
      datetime started_at "nullable"
      datetime created_at
      datetime finished_at "nullable"
      int retry_count
      text last_error "nullable"
    }

    PIPELINE_EVENTS {
      int id PK
      string pipeline_run_id FK
      string step_name
      string status
      json payload "nullable"
      datetime created_at
    }

    TEXT_VERSIONS {
      int id PK
      string document_id FK
      string pipeline_run_id FK
      text text
    }

    STRUCTURED_VERSIONS {
      int id PK
      string document_id FK
      string pipeline_run_id FK
      json data
    }

    QUALITY_SCORES {
      int id PK
      string pipeline_run_id FK
      float technical_score "nullable"
      float semantic_score "nullable"
      float overall_score "nullable"
      json details "nullable"
    }

    HUMAN_REVIEW_TASKS {
      int id PK
      string document_id FK
      string pipeline_run_id FK
      string profile_id
      float overall_quality_score "nullable"
      datetime created_at
      boolean completed
    }

    HUMAN_REVIEW_ACTIONS {
      int id PK
      int task_id FK
      int structured_version_id FK
      json payload
      datetime created_at
    }

    INGEST_REQUESTS {
      int id PK
      string idempotency_key "unique"
      string request_hash
      string source_type
      string external_id "nullable"
      string document_id FK
      string pipeline_run_id FK
      datetime created_at
    }

    DOCUMENTS ||--o{ INGEST_REQUESTS : has
    PIPELINE_RUNS ||--o{ INGEST_REQUESTS : has
```

## ER-диаграмма (PlantUML)

```plantuml
@startuml
hide methods
hide stereotypes

entity "documents" as documents {
  *id : string <<PK>>
  --
  source_type : string
  document_type : string?
  created_at : datetime
}

entity "document_files" as document_files {
  *id : int <<PK>>
  --
  document_id : string <<FK>>
  storage_path : string
  file_type : string
}

entity "pipeline_runs" as pipeline_runs {
  *id : string <<PK>>
  --
  document_id : string <<FK>>
  profile_id : string
  status : string
  idempotency_key : string?
  started_at : datetime?
  created_at : datetime
  finished_at : datetime?
  retry_count : int
  last_error : text?
}

entity "pipeline_events" as pipeline_events {
  *id : int <<PK>>
  --
  pipeline_run_id : string <<FK>>
  step_name : string
  status : string
  payload : json?
  created_at : datetime
}

entity "text_versions" as text_versions {
  *id : int <<PK>>
  --
  document_id : string <<FK>>
  pipeline_run_id : string <<FK>>
  text : text
}

entity "structured_versions" as structured_versions {
  *id : int <<PK>>
  --
  document_id : string <<FK>>
  pipeline_run_id : string <<FK>>
  data : json
}

entity "quality_scores" as quality_scores {
  *id : int <<PK>>
  --
  pipeline_run_id : string <<FK>>
  technical_score : float?
  semantic_score : float?
  overall_score : float?
  details : json?
}

entity "human_review_tasks" as human_review_tasks {
  *id : int <<PK>>
  --
  document_id : string <<FK>>
  pipeline_run_id : string <<FK>>
  profile_id : string
  overall_quality_score : float?
  created_at : datetime
  completed : boolean
}

entity "human_review_actions" as human_review_actions {
  *id : int <<PK>>
  --
  task_id : int <<FK>>
  structured_version_id : int <<FK>>
  payload : json
  created_at : datetime
}

entity "ingest_requests" as ingest_requests {
  *id : int <<PK>>
  --
  idempotency_key : string <<UQ>>
  request_hash : string
  source_type : string
  external_id : string?
  document_id : string <<FK>>
  pipeline_run_id : string <<FK>>
  created_at : datetime
}

documents ||--o{ document_files
documents ||--o{ pipeline_runs
pipeline_runs ||--o{ pipeline_events
pipeline_runs ||--o{ text_versions
pipeline_runs ||--o{ structured_versions
pipeline_runs ||--o{ quality_scores
documents ||--o{ text_versions
documents ||--o{ structured_versions
documents ||--o{ human_review_tasks
pipeline_runs ||--o{ human_review_tasks
human_review_tasks ||--o{ human_review_actions
structured_versions ||--o{ human_review_actions
documents ||--o{ ingest_requests
pipeline_runs ||--o{ ingest_requests

@enduml
```

## Таблицы и поля

- `documents`
  - `id` (PK, `String`)
  - `source_type` (`String`, not null)
  - `document_type` (`String`, nullable)
  - `created_at` (`DateTime`, default `utcnow`)

- `document_files`
  - `id` (PK, `Integer`, autoincrement)
  - `document_id` (FK -> `documents.id`, not null)
  - `storage_path` (`String`, not null)
  - `file_type` (`String`, not null)

- `pipeline_runs`
  - `id` (PK, `String`)
  - `document_id` (FK -> `documents.id`, not null)
  - `profile_id` (`String`, not null)
  - `status` (`String`, default `"queued"`)
  - `idempotency_key` (`String`, nullable)
  - `started_at` (`DateTime`, nullable)
  - `created_at` (`DateTime`, default `utcnow`)
  - `finished_at` (`DateTime`, nullable)
  - `retry_count` (`Integer`, default `0`)
  - `last_error` (`Text`, nullable)

- `pipeline_events`
  - `id` (PK, `Integer`, autoincrement)
  - `pipeline_run_id` (FK -> `pipeline_runs.id`, not null)
  - `step_name` (`String`, not null)
  - `status` (`String`, not null)
  - `payload` (`JSON`, nullable)
  - `created_at` (`DateTime`, default `utcnow`)

- `text_versions`
  - `id` (PK, `Integer`, autoincrement)
  - `document_id` (FK -> `documents.id`, not null)
  - `pipeline_run_id` (FK -> `pipeline_runs.id`, not null)
  - `text` (`Text`, not null)

- `structured_versions`
  - `id` (PK, `Integer`, autoincrement)
  - `document_id` (FK -> `documents.id`, not null)
  - `pipeline_run_id` (FK -> `pipeline_runs.id`, not null)
  - `data` (`JSON`, not null)

- `quality_scores`
  - `id` (PK, `Integer`, autoincrement)
  - `pipeline_run_id` (FK -> `pipeline_runs.id`, not null)
  - `technical_score` (`Float`, nullable)
  - `semantic_score` (`Float`, nullable)
  - `overall_score` (`Float`, nullable)
  - `details` (`JSON`, nullable)

- `human_review_tasks`
  - `id` (PK, `Integer`, autoincrement)
  - `document_id` (FK -> `documents.id`, not null)
  - `pipeline_run_id` (FK -> `pipeline_runs.id`, not null)
  - `profile_id` (`String`, not null)
  - `overall_quality_score` (`Float`, nullable)
  - `created_at` (`DateTime`, default `utcnow`)
  - `completed` (`Boolean`, default `False`)

- `human_review_actions`
  - `id` (PK, `Integer`, autoincrement)
  - `task_id` (FK -> `human_review_tasks.id`, not null)
  - `structured_version_id` (FK -> `structured_versions.id`, not null)
  - `payload` (`JSON`, not null)
  - `created_at` (`DateTime`, default `utcnow`)

- `ingest_requests`
  - `id` (PK, `Integer`, autoincrement)
  - `idempotency_key` (`String`, unique, not null)
  - `request_hash` (`String`, not null)
  - `source_type` (`String`, not null)
  - `external_id` (`String`, nullable)
  - `document_id` (FK -> `documents.id`, not null)
  - `pipeline_run_id` (FK -> `pipeline_runs.id`, not null)
  - `created_at` (`DateTime`, default `utcnow`)

