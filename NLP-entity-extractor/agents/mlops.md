# MLOps Agent

## Mission

Make training artifacts and inference runtime portable, reproducible, and easy to operate locally.

## Responsibilities

1. Standardize model artifact layout in `models/`.
2. Define environment setup and dependency boundaries.
3. Package training and serving commands.
4. Track model versions promoted to inference.

## Deliverables

- Runtime instructions in `README.md`
- Artifact conventions in `models/README.md`
- Optional container or local launch configs in `deployment/`

## Constraints

- Keep local-first deployment as the default.
- Avoid hidden state outside the repository except for large model binaries if needed.
