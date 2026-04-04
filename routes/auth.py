# C:\PROJECTS\Essay-Grader\backend\routes\auth.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import os

from database import get_db
from models.user import User

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

    hashed = pwd_context.hash(body.password)

    user = User(
        name                = body.name,
        email               = body.email,
        password            = hashed,
        role                = body.role,
        registration_number = body.registration_number,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {"message": "Account created successfully", "role": user.role}

# ── LOGIN ──────────────────────────────────────────────────────────────────
@router.post("/login")
def login(body: LoginSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not pwd_context.verify(body.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token({
        "user_id": user.id,
        "email":   user.email,
        "role":    user.role,
        "name":    user.name,
    })

    return {
        "token":               token,
        "role":                user.role,
        "full_name":           user.name,
        "email":               user.email,
        "registration_number": user.registration_number,
    }