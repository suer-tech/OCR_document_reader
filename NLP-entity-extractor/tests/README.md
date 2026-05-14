# Tests Layout

- `api/`: request validation and endpoint tests
- `inference/`: model loading and prediction tests
- `training/`: dataset and training pipeline tests
- `fixtures/`: sample payloads and extracted text fixtures

Test fixtures should cover:

- valid Base64 text PDF
- invalid Base64 payload
- valid PDF with no confident FIO prediction
- normalized FIO postprocessing cases
