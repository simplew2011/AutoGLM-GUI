import pytest
from httpx import ASGITransport, AsyncClient

from AutoGLM_GUI.api import create_app

app = create_app()


@pytest.mark.anyio
async def test_intents_detect_endpoint_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/intents/detect", json={"message": ""})
        assert resp.status_code == 422

        resp = await client.post("/api/intents/detect", json={})
        assert resp.status_code == 422

        resp = await client.post("/api/intents/detect", json={"message": "test"})
        assert resp.status_code in (200, 503)
