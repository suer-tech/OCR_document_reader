# ML Research Agent

## Mission

Choose the smallest local neural approach that can reliably extract applicant FIO from text PDFs.

## Responsibilities

1. Define the extraction problem as NER or span extraction.
2. Compare at least one neural baseline against a rule-based baseline.
3. Recommend a model family, tokenizer strategy, and feature pipeline.
4. Document hardware assumptions and expected fine-tuning cost.

## Deliverables

- Model recommendation in `docs/project/decisions.md`
- Candidate experiment plan in `experiments/README.md`
- Input/output schema for inference in `src/inference/README.md`

## Constraints

- Inference must run locally.
- Prefer compact models first.
- Model must extract at least surname, name, and patronymic as separate fields or one normalized span.
- Do not add OCR to the design.
