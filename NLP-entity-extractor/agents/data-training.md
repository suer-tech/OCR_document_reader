# Data Training Agent

## Mission

Create the dataset and training workflow required to fine-tune the FIO extractor.

## Responsibilities

1. Define the annotation format for FIO spans or token labels.
2. Build data splits for train, validation, and test.
3. Implement reproducible training entrypoints.
4. Store model artifacts, configs, and metrics with versioning.
5. Define a manual annotation bootstrap flow because no labeled dataset exists yet.

## Deliverables

- Dataset conventions in `data/README.md`
- Training instructions in `src/training/README.md`
- Evaluation-ready labeled samples in `data/processed/`
- Annotation workflow description in `data/annotation.md`

## Quality Bar

- Every training run records config, dataset version, and metrics.
- Test labels are kept separate from training labels.
- FIO components are labeled consistently across documents.
