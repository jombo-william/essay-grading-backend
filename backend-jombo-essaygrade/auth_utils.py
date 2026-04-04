# auth_utils.py - handles both PHP password_hash() and Python bcrypt hashes
import os
import secrets
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Cookie, Header, Request
from sqlalchemy.orm import Session
from database import get_db
import models

# PHP's password_hash() uses bcrypt too — passlib reads both formats
pwd_context  = CryptContext(schemes=["bcrypt"], deprecated="auto")
SESSION_HOURS = 8


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Works with BOTH:
    - PHP password_hash() output  e.g. $2y$10$...
    - Python bcrypt output        e.g. $2b$12$...
    passlib automatically handles $2y$ (PHP) as $2b$ (Python bcrypt).
    """
    try:
        # Replace $2y$ (PHP) with $2b$ (Python bcrypt compatible)
        if hashed.startswith("$2y$"):
            hashed = "$2b$" + hashed[4:]
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def generate_token() -> str:
    return secrets.token_hex(32)


def get_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)


# ── Session lookup ──────────────────────────────────────────────────────────

def _get_session_user(session_token: str, role: str, db: Session):
    if not session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(timezone.utc)
    session = (
        db.query(models.UserSession)
        .filter(
            models.UserSession.session_token == session_token,
            models.UserSession.expires_at    >  now,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = db.query(models.User).filter(
        models.User.id        == session.user_id,
        models.User.is_active == True,
    ).first()

    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if user.role != role:
        raise HTTPException(status_code=403, detail="Forbidden")

    return user, session


def require_teacher(
    session_token: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    user, session = _get_session_user(session_token, "teacher", db)
    return {"user": user, "session": session, "db": db}


def require_student(
    session_token: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    user, session = _get_session_user(session_token, "student", db)
    return {"user": user, "session": session, "db": db}


def require_any(
    session_token: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(timezone.utc)
    session = (
        db.query(models.UserSession)
        .filter(
            models.UserSession.session_token == session_token,
            models.UserSession.expires_at    >  now,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = db.query(models.User).filter(
        models.User.id        == session.user_id,
        models.User.is_active == True,
    ).first()

    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {"user": user, "session": session, "db": db}


def validate_csrf(session: models.UserSession, x_csrf_token: str = None, body_csrf: str = None):
    token = x_csrf_token or body_csrf or ""
    if not token or not secrets.compare_digest(session.csrf_token, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")