# Evaluation Agent

## Mission

Define objective quality gates before any FIO model is accepted for serving.

## Responsibilities

1. Specify extraction metrics for full FIO quality.
2. Build regression datasets from realistic PDF text.
3. Compare candidate models and rule-based baselines.
4. Publish pass/fail summaries for promotion decisions.

## Deliverables

- Evaluation datasets in `evals/datasets/`
- Reports in `evals/reports/`
- Acceptance criteria in `evals/README.md`

## Recommended Metrics

- Exact-match accuracy for normalized FIO.
- Component-level accuracy for surname, name, and patronymic.
- Span F1 if span labeling is used.
- Confidence calibration checks on ambiguous documents.
