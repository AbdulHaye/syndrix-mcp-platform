from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def client():
    """Create an AsyncClient backed by the FastAPI ASGI app."""
    # Patch DB and Redis init so tests run without infrastructure
    import unittest.mock as mock

    with (
        mock.patch("app.storage.db.init_db", return_value=None),
        mock.patch("redis.asyncio.from_url") as mock_redis,
    ):
        # Make the mock redis behave well
        mock_instance = mock.AsyncMock()
        mock_instance.ping = mock.AsyncMock(return_value=True)
        mock_instance.aclose = mock.AsyncMock()
        mock_redis.return_value = mock_instance

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.anyio
async def test_health_ok(client: AsyncClient) -> None:
    """GET /health should return status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "env" in data


@pytest.mark.anyio
async def test_health_detailed(client: AsyncClient) -> None:
    """GET /health/detailed should return per-service status dict."""
    response = await client.get("/health/detailed")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "services" in data
    services = data["services"]
    # All three infrastructure keys must be present
    for key in ("postgres", "redis", "ollama"):
        assert key in services, f"Missing service key: {key}"
        assert "status" in services[key]
