from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from auth_utils import (
    verify_password, hash_password, generate_token,
    get_expiry, require_any
)
import models

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
    role: str
    registration_number: str = None
    phone: str = None


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Validate email format
    import re
    if not re.match(r"[^@]+@[^@]+\.[^@]+", body.email):
        raise HTTPException(status_code=422, detail="Invalid email format")

    user = db.query(models.User).filter(
        models.User.email == body.email.strip(),
        models.User.is_active == True,
    ).first()

    if not user or not verify_password(body.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Generate tokens - matches PHP bin2hex(random_bytes(32))
    session_token = generate_token()
    csrf_token    = generate_token()
    expires_at    = get_expiry()
    ip_address    = request.client.host or "0.0.0.0"

    # Save session to DB - matches PHP INSERT INTO user_sessions
    session = models.UserSession(
        user_id       = user.id,
        session_token = session_token,
        csrf_token    = csrf_token,
        ip_address    = ip_address,
        expires_at    = expires_at,
    )
    db.add(session)
    db.commit()

    # Build response with cookies - matches PHP setcookie()
    response = JSONResponse(content={
        "success":    True,
        "csrf_token": csrf_token,   # also in body as backup, matches PHP
        "user": {
            "id":                  user.id,
            "name":                user.name,
            "email":               user.email,
            "role":                user.role,
            "registration_number": user.registration_number,
        }
    })

    cookie_expires = int(expires_at.timestamp())

    # session_token cookie — httponly=True (JS cannot read, matches PHP)
    response.set_cookie(
        key      = "session_token",
        value    = session_token,
        expires  = cookie_expires,
        path     = "/",
        httponly = True,
        samesite = "lax",
        secure   = False,
    )

    # csrf_token cookie — httponly=False (JS needs to read it, matches PHP)
    response.set_cookie(
        key      = "csrf_token",
        value    = csrf_token,
        expires  = cookie_expires,
        path     = "/",
        httponly = False,
        samesite = "lax",
        secure   = False,
    )

    return response


@router.post("/logout")
def logout(ctx: dict = Depends(require_any), db: Session = Depends(get_db)):
    session = ctx["session"]
    db.delete(session)
    db.commit()

    response = JSONResponse(content={"success": True, "message": "Logged out"})
    response.delete_cookie("session_token")
    response.delete_cookie("csrf_token")
    return response


@router.get("/me")
def me(ctx: dict = Depends(require_any)):
    user = ctx["user"]
    return {
        "id":                  user.id,
        "name":                user.name,
        "email":               user.email,
        "role":                user.role,
        "registration_number": user.registration_number,
        "phone":               user.phone,
    }


@router.post("/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if body.role not in ("teacher", "student"):
        raise HTTPException(status_code=422, detail="Role must be teacher or student")

    if db.query(models.User).filter(models.User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = models.User(
        name                = body.name,
        email               = body.email,
        password            = hash_password(body.password),
        role                = body.role,
        registration_number = body.registration_number,
        phone               = body.phone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {"success": True, "message": "Account created successfully"}