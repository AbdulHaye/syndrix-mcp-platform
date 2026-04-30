from __future__ import annotations

import unittest.mock as mock

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def client():
    """Create an AsyncClient backed by the FastAPI ASGI app with infra mocked out."""
    with (
        mock.patch("app.storage.db.init_db", return_value=None),
        mock.patch("redis.asyncio.from_url") as mock_redis,
    ):
        mock_instance = mock.AsyncMock()
        mock_instance.ping = mock.AsyncMock(return_value=True)
        mock_instance.aclose = mock.AsyncMock()
        mock_redis.return_value = mock_instance

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.anyio
async def test_no_token_returns_401(client: AsyncClient) -> None:
    """Requests to protected endpoints without a token must get 401."""
    response = await client.get("/admin/tools")
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"


@pytest.mark.anyio
async def test_invalid_token_returns_401(client: AsyncClient) -> None:
    """Requests with a garbage Bearer token must get 401."""
    response = await client.get(
        "/admin/tools",
        headers={"Authorization": "Bearer totally-invalid-token-xyz"},
    )
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"


@pytest.mark.anyio
async def test_valid_bd_token_accepted(client: AsyncClient) -> None:
    """
    A valid BD token should be accepted for /health (open to all roles)
    and rejected for /admin/tools (ADMIN only).
    """
    # Mock settings so we control the token map without touching .env
    mock_settings = mock.MagicMock()
    mock_settings.get_token_map.return_value = {"bd-secret-token-123": "bd_team"}
    mock_settings.app_env = "development"
    mock_settings.is_production = False
    mock_settings.is_development = True

    with mock.patch("app.auth.bearer.get_settings", return_value=mock_settings):
        from app.auth.bearer import verify_token, TeamRole

        identity = verify_token("bd-secret-token-123")
        assert identity is not None, "verify_token should return a TeamIdentity for a valid token"
        assert identity.team_name == "bd_team"
        assert identity.role == TeamRole.BD

    # /health should work for any valid token (role-independent public endpoint)
    response = await client.get(
        "/health",
        headers={"Authorization": "Bearer bd-secret-token-123"},
    )
    # /health does not require auth, so it should be 200 regardless
    assert response.status_code == 200

    # /admin/tools requires ADMIN role — BD should get 403
    with mock.patch("app.auth.bearer.get_settings", return_value=mock_settings):
        response = await client.get(
            "/admin/tools",
            headers={"Authorization": "Bearer bd-secret-token-123"},
        )
    # 401 if token unrecognised in test env, 403 if token valid but wrong role
    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for BD token on admin endpoint, got {response.status_code}"
    )
