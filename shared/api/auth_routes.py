"""
Auth Routes — JWT Login and Registration

Provides POST /auth/token (login) and POST /auth/register.
Reads JWT_SECRET from environment; requires it to be set.

Reference: SHARED_INFRASTRUCTURE.md §2.1 Phase 2
"""

import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.adapter import get_database_adapter
from shared.database.models import UserTable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

JWT_SECRET: str = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_jwt_secret() -> str:
    """Get JWT secret, raising if not configured."""
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is not set")
    return secret


# --- Pydantic Schemas ---


class RegisterRequest(BaseModel):
    """Registration request body."""

    email: EmailStr
    password: str
    full_name: str | None = None
    context_tier: int = Field(default=1, ge=1, le=4)


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UpdateProfileRequest(BaseModel):
    """Request body for updating user profile."""

    context_tier: int | None = Field(default=None, ge=1, le=4)
    full_name: str | None = None


class UserResponse(BaseModel):
    """User data response."""

    user_id: UUID
    email: str
    full_name: str | None
    context_tier: int


# --- JWT Service ---


class JWTService:
    """Encapsulates JWT token and password operations."""

    @staticmethod
    def hash_password(plain: str) -> str:
        """Hash a plaintext password with bcrypt."""
        return pwd_context.hash(plain)

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        """Verify a plaintext password against a bcrypt hash."""
        return pwd_context.verify(plain, hashed)

    @staticmethod
    def create_access_token(
        user_id: UUID,
        email: str,
        context_tier: int,
    ) -> str:
        """Create a signed HS256 JWT with standard claims."""
        secret = _get_jwt_secret()
        now = datetime.now(tz=UTC)
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "sub": str(user_id),
            "email": email,
            "context_tier": context_tier,
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
        }
        return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


# --- Database Dependency ---


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async database session."""
    adapter = get_database_adapter()
    async with adapter.async_session() as session:
        yield session


# --- Routes ---


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    session: AsyncSession = Depends(get_db_session),
) -> list[UserResponse]:
    """List all active (non-deleted) users. Demo/admin endpoint."""
    stmt = (
        select(UserTable)
        .where(UserTable.deleted_at.is_(None))
        .order_by(UserTable.created_at.desc())
    )
    result = await session.execute(stmt)
    users = result.scalars().all()
    return [
        UserResponse(
            user_id=u.user_id,
            email=u.email,
            full_name=u.full_name,
            context_tier=u.context_tier,
        )
        for u in users
    ]


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """
    Register a new user.

    Hashes password with bcrypt, stores user, returns user data.
    Returns 409 if email already exists.
    """
    # Check for existing user
    stmt = select(UserTable).where(
        UserTable.email == body.email,
        UserTable.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create user
    hashed = JWTService.hash_password(body.password)
    user = UserTable(
        email=body.email,
        password_hash=hashed,
        full_name=body.full_name,
        context_tier=body.context_tier,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    logger.info(
        "Registered new user",
        extra={"user_id": str(user.user_id), "email": user.email},
    )

    return UserResponse(
        user_id=user.user_id,
        email=user.email,
        full_name=user.full_name,
        context_tier=user.context_tier,
    )


@router.post("/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """
    Authenticate user and return a JWT bearer token.

    Accepts application/x-www-form-urlencoded with `username` (email)
    and `password` fields (standard OAuth2 password grant shape).
    """
    # Look up user by email (OAuth2 spec uses "username" field)
    stmt = select(UserTable).where(
        UserTable.email == form.username,
        UserTable.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    # Identical error message for not-found and wrong-password (prevent enumeration)
    if user is None or user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not JWTService.verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = JWTService.create_access_token(
        user_id=user.user_id,
        email=user.email,
        context_tier=user.context_tier,
    )

    logger.info(
        "Issued token",
        extra={"user_id": str(user.user_id), "email": user.email},
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.patch("/profile", response_model=TokenResponse)
async def update_profile(
    body: UpdateProfileRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """
    Update authenticated user's profile and return a fresh JWT.

    Allows updating context_tier and full_name.
    Returns a new JWT so the client immediately reflects updated claims.
    """
    user_id = request.state.user_id

    stmt = select(UserTable).where(
        UserTable.user_id == user_id,
        UserTable.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if body.context_tier is not None:
        user.context_tier = body.context_tier
    if body.full_name is not None:
        user.full_name = body.full_name

    await session.commit()
    await session.refresh(user)

    # Issue a fresh JWT with updated claims
    token = JWTService.create_access_token(
        user_id=user.user_id,
        email=user.email,
        context_tier=user.context_tier,
    )

    logger.info(
        "Profile updated",
        extra={"user_id": str(user.user_id), "context_tier": user.context_tier},
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
