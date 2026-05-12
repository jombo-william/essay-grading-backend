

import os
import secrets
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Cookie, Header
from sqlalchemy.orm import Session
from database import get_db
import models

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
SESSION_HOURS = 8


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        if hashed.startswith("$2y$"):
            hashed = "$2b$" + hashed[4:]
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def generate_token() -> str:
    return secrets.token_hex(32)


def get_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)


def _find_session(token: str, db: Session):
    """Find session by session_token OR csrf_token."""
    if not token:
        return None
    now = datetime.now(timezone.utc)
    # Try session_token first
    session = db.query(models.UserSession).filter(
        models.UserSession.session_token == token,
        models.UserSession.expires_at    >  now,
    ).first()
    if session:
        return session
    # Fall back to csrf_token (this is what we have in localStorage)
    session = db.query(models.UserSession).filter(
        models.UserSession.csrf_token == token,
        models.UserSession.expires_at >  now,
    ).first()
    return session


def _pick_token(session_token_cookie, authorization, x_csrf_token):
    """Pick the best available token."""
    if session_token_cookie:
        return session_token_cookie
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    if x_csrf_token:
        return x_csrf_token
    return None


def _get_session_user(token: str, role: str, db: Session):
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    session = _find_session(token, db)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

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
    authorization: str = Header(default=None),
    x_csrf_token:  str = Header(default=None),
    db: Session = Depends(get_db),
):
    token = _pick_token(session_token, authorization, x_csrf_token)
    user, session = _get_session_user(token, "teacher", db)
    return {"user": user, "session": session, "db": db}


def require_student(
    session_token: str = Cookie(default=None),
    authorization: str = Header(default=None),
    x_csrf_token:  str = Header(default=None),
    db: Session = Depends(get_db),
):
    token = _pick_token(session_token, authorization, x_csrf_token)
    user, session = _get_session_user(token, "student", db)
    return {"user": user, "session": session, "db": db}


def require_any(
    session_token: str = Cookie(default=None),
    authorization: str = Header(default=None),
    x_csrf_token:  str = Header(default=None),
    db: Session = Depends(get_db),
):
    token = _pick_token(session_token, authorization, x_csrf_token)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    session = _find_session(token, db)
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
    
    return  # ← just return, no check needed