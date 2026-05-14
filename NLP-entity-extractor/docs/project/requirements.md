# Requirements

## Functional

1. Accept HTTP requests with Base64-encoded PDF content.
2. Decode and parse text from text PDFs.
3. Extract the applicant full FIO from the parsed text.
4. Return machine-readable JSON with structured extraction result and confidence.
5. Support fine-tuning or re-training the local model on labeled examples.
6. Support offline inference after model artifacts are prepared.
7. Support manual dataset creation from extracted PDF text because no labeled dataset exists yet.

## Non-Functional

1. Keep all inference local.
2. Separate training code from serving code.
3. Version datasets, model configs, and evaluation outputs.
4. Make failures explicit for unreadable PDFs or uncertain predictions.
5. Do not include OCR dependencies in the first implementation.

## Confirmed Scope

1. Input is text PDFs only.
2. Target output is full FIO, not only surname.
3. Initial labeling must be created in-project from available sample PDFs.
4. API v1 returns a single best FIO only, without alternative candidates.

## Open Product Questions

1. What latency and hardware limits are acceptable?
2. What metric gates should block model promotion?
3. Should the system expose surname-only output as a derived field for downstream consumers?
