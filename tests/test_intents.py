import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from AutoGLM_GUI.Intents.detector import IntentDetector
from AutoGLM_GUI.Intents.types import IntentResult


def _mock_config_manager(
    intent_base_url="http://localhost/v1", intent_model_name="test-model"
):
    mgr = MagicMock()
    mgr.get_effective_config.return_value = MagicMock(
        intent_base_url=intent_base_url,
        intent_api_key="sk-test",
        intent_model_name=intent_model_name,
    )
    return mgr


class _MockResponse:
    def __init__(self, status, data):
        self.status_code = status
        self.text = json.dumps(data)
        self._data = data

    def json(self):
        return self._data


@pytest.mark.anyio
@pytest.mark.anyio
async def test_detect_classic():
    detector = IntentDetector(_mock_config_manager())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(
        return_value=_MockResponse(200, {"choices": [{"message": {"content": '{"mode": "classic"}'}}]})
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await detector.detect("打开淘宝搜索耳机")
    assert result == IntentResult(mode="classic")


@pytest.mark.anyio
async def test_detect_layered():
    detector = IntentDetector(_mock_config_manager())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(
        return_value=_MockResponse(200, {"choices": [{"message": {"content": '{"mode": "layered"}'}}]})
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await detector.detect("帮我对比淘宝和京东上同一款商品的价格")
    assert result == IntentResult(mode="layered")


@pytest.mark.anyio
async def test_detect_chat():
    detector = IntentDetector(_mock_config_manager())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(
        return_value=_MockResponse(200, {"choices": [{"message": {"content": '{"mode": "chat"}'}}]})
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await detector.detect("你好，今天天气怎么样")
    assert result == IntentResult(mode="chat")


@pytest.mark.anyio
async def test_detect_not_configured():
    detector = IntentDetector(
        _mock_config_manager(intent_base_url="", intent_model_name="")
    )
    with pytest.raises(RuntimeError, match="not configured"):
        await detector.detect("test")


@pytest.mark.anyio
async def test_detect_fallback_on_bad_response():
    detector = IntentDetector(_mock_config_manager())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(
        return_value=_MockResponse(200, {"choices": [{"message": {"content": "invalid response"}}]})
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Failed to parse"):
            await detector.detect("test")


@pytest.mark.anyio
async def test_detect_api_error():
    detector = IntentDetector(_mock_config_manager())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(
        return_value=_MockResponse(500, {"error": "internal error"})
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="status 500"):
            await detector.detect("test")
