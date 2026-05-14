# Agent Harness

This repository follows a harness-style layout:

- `AGENTS.md` is only the routing layer.
- Persistent project context lives in `docs/project/`.
- Agent-specific operating instructions live in `agents/`.
- Task execution state lives in `tasks/`.
- App and ML deliverables live in `src/`, `data/`, `models/`, `evals/`, and `tests/`.

## Objective

Build a local-first Python system that:

1. Accepts a Base64-encoded PDF over HTTP API.
2. Extracts text from the PDF.
3. Uses a locally deployed neural model to extract the applicant full name (surname, name, patronymic).
4. Supports model fine-tuning before serving inference.

## Agent Routing

- Start with `agents/orchestrator.md`.
- For requirements and constraints, read `docs/project/overview.md` and `docs/project/requirements.md`.
- For model strategy and training, use `agents/ml-research.md` and `agents/data-training.md`.
- For API and service delivery, use `agents/backend.md`.
- For quality gates, use `agents/evaluation.md`.
- For packaging and runtime, use `agents/mlops.md`.

## Operating Rules

- Do not duplicate long instructions here; extend the referenced files instead.
- Keep decisions current in `docs/project/decisions.md`.
- Record task-specific execution state in `tasks/backlog.md`.
- Treat FIO extraction quality as the primary success metric.

## Fixed Assumptions

- Input documents are text PDFs only; OCR is out of scope.
- Target language is assumed to be Russian.
- The system must extract full FIO, not only surname.

## Current Gaps

- The annotation format and source of training data are not yet defined.
- There is a sample PDF in the repository root, but no labeled dataset yet.
- Model family is not fixed; choose the smallest local model that meets quality targets.
