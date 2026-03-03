import os
import time
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from starlette import status

from src.db import fetch_one, get_db

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


def _required_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            "It must be provided via the container .env."
        )
    return val


def _jwt_secret() -> str:
    return _required_env("JWT_SECRET")


def _jwt_issuer() -> str:
    return os.getenv("JWT_ISSUER", "notes_backend")


def _jwt_ttl_seconds() -> int:
    try:
        return int(os.getenv("JWT_TTL_SECONDS", "2592000"))  # 30 days
    except ValueError:
        return 2592000


# PUBLIC_INTERFACE
def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _pwd_context.hash(password)


# PUBLIC_INTERFACE
def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against the stored password hash."""
    return _pwd_context.verify(password, password_hash)


# PUBLIC_INTERFACE
def create_access_token(*, user_id: int, email: str) -> str:
    """Create a signed JWT access token for a user."""
    now = int(time.time())
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "iss": _jwt_issuer(),
        "iat": now,
        "exp": now + _jwt_ttl_seconds(),
        "type": "access",
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(
        token,
        _jwt_secret(),
        algorithms=["HS256"],
        issuer=_jwt_issuer(),
        options={"require": ["exp", "iat", "iss", "sub"]},
    )


# PUBLIC_INTERFACE
def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Dict[str, Any]:
    """FastAPI dependency to authenticate via Authorization: Bearer <token>."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = credentials.credentials
    try:
        payload = _decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id_str = payload.get("sub")
    if not user_id_str or not str(user_id_str).isdigit():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    user_id = int(user_id_str)

    # Ensure user still exists.
    with get_db() as conn:
        user = fetch_one(conn, "SELECT id, email, created_at, updated_at FROM users WHERE id = %s", (user_id,))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user
