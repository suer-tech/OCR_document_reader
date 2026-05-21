import pytest
from unittest.mock import AsyncMock, patch
from ocr_platform.orchestration.run_processor import _trigger_webhook_safely

@pytest.mark.asyncio
async def test_trigger_webhook_safely_success():
    payload = {"pipeline_run_id": "test_run", "status": "done"}
    webhook_url = "http://example.com/webhook"

    # Создаем Mock-ответ
    mock_response = AsyncMock()
    mock_response.status_code = 200

    # Мокаем httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await _trigger_webhook_safely(webhook_url, payload)
        
        mock_post.assert_called_once_with(webhook_url, json=payload, timeout=10.0)


@pytest.mark.asyncio
async def test_trigger_webhook_safely_failure():
    payload = {"pipeline_run_id": "test_run", "status": "failed"}
    webhook_url = "http://example.com/webhook"

    # Мокаем httpx.AsyncClient.post так, чтобы он выбрасывал исключение
    with patch("httpx.AsyncClient.post", side_effect=Exception("HTTP error")):
        # Не должно вызывать исключений во внешний код, так как обернуто в try/except
        await _trigger_webhook_safely(webhook_url, payload)
