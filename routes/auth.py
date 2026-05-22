<<<<<<< HEAD
# C:\PROJECTS\Essay-Grader\backend\routes\auth.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
import bcrypt
from jose import jwt
from datetime import datetime, timedelta
import os

from database import get_db
from models.user import User

router = APIRouter()

JWT_SECRET  = os.getenv("JWT_SECRET", "secret")
JWT_EXPIRE  = int(os.getenv("JWT_EXPIRE_MINUTES", 60))

# ── Schemas ────────────────────────────────────────────────────────────────
class RegisterSchema(BaseModel):
    name:                str
    email:               EmailStr
    password:            str
    role:                str        # 'student' or 'teacher'
    registration_number: str = None

class LoginSchema(BaseModel):
    email:    EmailStr
    password: str

# ── Helper ─────────────────────────────────────────────────────────────────
def create_token(data: dict):
    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE)
    data.update({"exp": expire})
    return jwt.encode(data, JWT_SECRET, algorithm="HS256")

# ── REGISTER ───────────────────────────────────────────────────────────────
@router.post("/register")
def register(body: RegisterSchema, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    if body.role not in ["student", "teacher"]:
        raise HTTPException(status_code=400, detail="Role must be student or teacher")

    hashed = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    user = User(
        name                = body.name,
        email               = body.email,
        password            = hashed,
        role                = body.role,
        registration_number = body.registration_number,
=======


from datetime import datetime, timezone
import os
import re

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

IS_PROD = os.getenv("ENV") == "production"


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
    if not re.match(r"[^@]+@[^@]+\.[^@]+", body.email):
        raise HTTPException(status_code=422, detail="Invalid email format")

    user = db.query(models.User).filter(
        models.User.email == body.email.strip(),
        models.User.is_active == True,
    ).first()
    
    if user:
        print(f"🔍 Hash in DB: {user.password[:20]}")
        print(f"🔍 Verify result: {verify_password(body.password, user.password)}")

    # if not user or not verify_password(body.password, user.password):
    #     raise HTTPException(status_code=401, detail="Invalid email or password")

    print(f"🔍 User found: {user}")
    if user:
        print(f"🔍 Full hash: {user.password}")
        print(f"🔍 Password input: '{body.password}'")
        print(f"🔍 Verify result: {verify_password(body.password, user.password)}")

    if not user or not verify_password(body.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")


    session_token = generate_token()
    csrf_token    = generate_token()
    expires_at    = get_expiry()
    ip_address    = request.client.host or "0.0.0.0"

    session = models.UserSession(
        user_id       = user.id,
        session_token = session_token,
        csrf_token    = csrf_token,
        ip_address    = ip_address,
        expires_at    = expires_at,
    )
    db.add(session)
    db.commit()

    response = JSONResponse(content={
        "success":    True,
        "csrf_token": csrf_token,
        "session_token": session_token,
        "user": {
            "id":                  user.id,
            "name":                user.name,
            "email":               user.email,
            "role":                user.role,
            "registration_number": user.registration_number,
        }
    })

    cookie_expires = int(expires_at.timestamp())

    response.set_cookie(
        key      = "session_token",
        value    = session_token,
        expires  = cookie_expires,
        path     = "/",
        httponly = True,
        samesite = "none" if IS_PROD else "lax",
        secure   = IS_PROD,
    )

    response.set_cookie(
        key      = "csrf_token",
        value    = csrf_token,
        expires  = cookie_expires,
        path     = "/",
        httponly = False,
        samesite = "none" if IS_PROD else "lax",
        secure   = IS_PROD,
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
>>>>>>> master
    )
    db.add(user)
    db.commit()
    db.refresh(user)

<<<<<<< HEAD
    return {"message": "Account created successfully", "role": user.role}

# ── LOGIN ──────────────────────────────────────────────────────────────────
@router.post("/login")
def login(body: LoginSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not bcrypt.checkpw(body.password.encode("utf-8"), user.password.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token({
        "user_id": user.id,
        "email":   user.email,
        "role":    user.role,
        "name":    user.name,
    })

    return {
        "token": token,
        "csrf_token": token,
        "session_token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "registration_number": user.registration_number,
        },
        "role": user.role,
        "full_name": user.name,
        "registration_number": user.registration_number,
    }
=======
    return {"success": True, "message": "Account created successfully"}
>>>>>>> master
