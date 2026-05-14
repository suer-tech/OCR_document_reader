import base64

from fastapi.testclient import TestClient

from api.app import app

client = TestClient(app)


MINIMAL_PDF = b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n4 0 obj<</Length 72>>stream\nBT /F1 12 Tf 20 100 Td (Zayavitel Ivanov Ivan Ivanovich) Tj ET\nendstream\nendobj\n5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\nxref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000053 00000 n \n0000000108 00000 n \n0000000212 00000 n \n0000000334 00000 n \ntrailer<</Root 1 0 R/Size 6>>\nstartxref\n404\n%%EOF"


def test_health() -> None:
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_invalid_base64_returns_400() -> None:
    response = client.post('/extract', json={'pdf_base64': 'not-base64'})
    assert response.status_code == 400


def test_valid_pdf_returns_response_shape() -> None:
    payload = base64.b64encode(MINIMAL_PDF).decode('ascii')
    response = client.post('/extract', json={'pdf_base64': payload, 'document_id': 'demo'})
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        body = response.json()
        assert 'applicant_fio' in body
        assert 'judge_fio' in body
        assert 'case_number' in body
        assert 'court_name' in body