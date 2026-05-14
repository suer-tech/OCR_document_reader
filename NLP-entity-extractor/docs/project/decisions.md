# Decisions Log

## Accepted Constraints

- Input is text PDF only.
- OCR is out of scope for v1.
- The extraction target is full FIO.
- Training data must be created inside the project from unlabeled examples.
- API v1 returns a single best `fio` object plus overall confidence.

## Pending Decisions

- Training architecture: transformer token classifier vs spaCy NER.
- Annotation schema: BIO tags vs span labels.
- Deployment target: local process vs containerized service.
- Should the API expose a derived surname-only field in addition to `fio`?

## Decision Format

For each accepted decision, record:

- Date
- Decision
- Reason
- Impact
- Owner
