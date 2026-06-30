"""
Tests for Auth Middleware (JWT Bearer Token Validation)

Tests the authentication middleware that validates JWT tokens
and populates request.state with user context.
"""

import os

# Must set JWT_SECRET before importing the middleware module
os.environ["JWT_SECRET"] = "test-secret-key-for-unit-tests-only"

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jose import jwt

from shared.middleware.auth import AuthMiddleware

TEST_SECRET = "test-secret-key-for-unit-tests-only"
TEST_ALGORITHM = "HS256"
TEST_USER_ID = "b14025d0-e491-4558-a4d2-ce70609a6a92"


def make_token(
    user_id: str = TEST_USER_ID,
    email: str = "test@example.com",
    context_tier: int = 3,
    expired: bool = False,
    secret: str = TEST_SECRET,
    omit_claims: list[str] | None = None,
) -> str:
    """Helper to generate test JWT tokens."""
    now = datetime.now(tz=UTC)
    exp = now - timedelta(minutes=1) if expired else now + timedelta(hours=1)
    payload = {
        "sub": user_id,
        "email": email,
        "context_tier": context_tier,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if omit_claims:
        for claim in omit_claims:
            payload.pop(claim, None)
    return jwt.encode(payload, secret, algorithm=TEST_ALGORITHM)


# Create test app
app = FastAPI()
app.add_middleware(AuthMiddleware)


@app.get("/test")
async def auth_test_endpoint(request: Request):
    """Test endpoint that returns extracted auth context."""
    return {
        "user_id": str(request.state.user_id),
        "context_tier": request.state.context_tier,
        "email": request.state.email,
    }


@app.get("/health")
async def health():
    """Health check endpoint (should bypass auth)."""
    return {"status": "ok"}


@app.post("/auth/token")
async def mock_token():
    """Mock auth token endpoint (should bypass auth)."""
    return {"access_token": "mock"}


@app.post("/auth/register")
async def mock_register():
    """Mock register endpoint (should bypass auth)."""
    return {"user_id": "mock"}


client = TestClient(app)


def test_auth_with_valid_jwt():
    """Test authentication with a valid JWT token."""
    token = make_token()
    response = client.get(
        "/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == TEST_USER_ID
    assert data["context_tier"] == 3
    assert data["email"] == "test@example.com"


def test_auth_with_default_context_tier():
    """Test authentication with context_tier=1 (default)."""
    token = make_token(context_tier=1)
    response = client.get(
        "/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["context_tier"] == 1


def test_auth_missing_authorization_header():
    """Test authentication fails without Authorization header."""
    response = client.get("/test")

    assert response.status_code == 401
    assert "Missing or invalid Authorization" in response.json()["detail"]


def test_auth_malformed_bearer_header():
    """Test authentication fails with malformed Bearer header."""
    response = client.get(
        "/test",
        headers={"Authorization": "Basic abc123"},
    )

    assert response.status_code == 401
    assert "Missing or invalid Authorization" in response.json()["detail"]


def test_auth_invalid_token_signature():
    """Test authentication fails with token signed by wrong secret."""
    token = make_token(secret="wrong-secret")
    response = client.get(
        "/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert "Invalid or expired token" in response.json()["detail"]


def test_auth_expired_token():
    """Test authentication fails with expired JWT token."""
    token = make_token(expired=True)
    response = client.get(
        "/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert "Invalid or expired token" in response.json()["detail"]


def test_auth_missing_required_claims():
    """Test authentication fails when JWT is missing required claims."""
    token = make_token(omit_claims=["email"])
    response = client.get(
        "/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert "missing required claims" in response.json()["detail"]


def test_auth_context_tier_out_of_range():
    """Test that out-of-range context_tier defaults to 1."""
    token = make_token(context_tier=10)
    response = client.get(
        "/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["context_tier"] == 1  # Clamped to default


def test_health_check_bypasses_auth():
    """Test that health check endpoint bypasses authentication."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_token_endpoint_bypasses_auth():
    """Test that /auth/token endpoint bypasses authentication."""
    response = client.post("/auth/token")

    assert response.status_code == 200


def test_auth_register_endpoint_bypasses_auth():
    """Test that /auth/register endpoint bypasses authentication."""
    response = client.post("/auth/register")

    assert response.status_code == 200


def test_auth_invalid_uuid_in_sub():
    """Test authentication fails when sub claim is not a valid UUID."""
    token = make_token(user_id="not-a-uuid")
    response = client.get(
        "/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert "Invalid token subject" in response.json()["detail"]
