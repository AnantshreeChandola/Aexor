"""
Tests for Auth Middleware (Header-Based MVP)

Tests the authentication middleware that extracts user context from headers.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uuid import UUID

from shared.middleware.auth import AuthMiddleware


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


client = TestClient(app)


def test_auth_with_valid_headers():
    """Test authentication with all valid headers."""
    response = client.get(
        "/test",
        headers={
            "X-User-ID": "b14025d0-e491-4558-a4d2-ce70609a6a92",
            "X-Context-Tier": "3",
            "X-User-Email": "test@example.com",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "b14025d0-e491-4558-a4d2-ce70609a6a92"
    assert data["context_tier"] == 3
    assert data["email"] == "test@example.com"


def test_auth_with_minimal_headers():
    """Test authentication with only required X-User-ID header."""
    response = client.get(
        "/test",
        headers={
            "X-User-ID": "b14025d0-e491-4558-a4d2-ce70609a6a92",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "b14025d0-e491-4558-a4d2-ce70609a6a92"
    assert data["context_tier"] == 1  # Default
    assert data["email"] == "unknown@example.com"  # Default


def test_auth_missing_user_id():
    """Test authentication fails without X-User-ID header."""
    response = client.get("/test")

    assert response.status_code == 401
    assert "Missing X-User-ID" in response.json()["detail"]


def test_auth_invalid_user_id_format():
    """Test authentication fails with invalid UUID format."""
    response = client.get(
        "/test",
        headers={
            "X-User-ID": "not-a-uuid",
        },
    )

    assert response.status_code == 401
    assert "Invalid X-User-ID format" in response.json()["detail"]


def test_auth_invalid_context_tier():
    """Test authentication defaults to tier 1 with invalid context tier."""
    response = client.get(
        "/test",
        headers={
            "X-User-ID": "b14025d0-e491-4558-a4d2-ce70609a6a92",
            "X-Context-Tier": "invalid",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["context_tier"] == 1  # Falls back to default


def test_auth_context_tier_out_of_range():
    """Test authentication defaults to tier 1 with out-of-range context tier."""
    response = client.get(
        "/test",
        headers={
            "X-User-ID": "b14025d0-e491-4558-a4d2-ce70609a6a92",
            "X-Context-Tier": "10",  # Out of range (1-4)
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["context_tier"] == 1  # Falls back to default


def test_health_check_bypasses_auth():
    """Test that health check endpoint bypasses authentication."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
