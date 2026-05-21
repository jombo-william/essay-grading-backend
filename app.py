from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
from pathlib import Path
from sqlalchemy.exc import SQLAlchemyError

# =========================================================
# LOAD ENVIRONMENT VARIABLES
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

print("GEMINI_API_KEY loaded:", "YES" if os.getenv("GEMINI_API_KEY") else "NO")
print("HF_API_KEY loaded:", "YES" if os.getenv("HF_API_KEY") else "NO")
print("DATABASE_URL configured:", "YES" if os.getenv("DATABASE_URL") else "NO")

# =========================================================
# DATABASE
# =========================================================
from database import engine
import models

# =========================================================
# ROUTES
# =========================================================
from routes import (
    auth,
    teacher,
    student,
    exams,
    student_exams,
    google_classroom,
    moodle_integration,
    grading,
)

from routes.student_classroom import router as student_classroom_router
from routes.student_moodle import router as student_moodle_router

# =========================================================
# FASTAPI APP
# =========================================================
app = FastAPI(title="JomboEssayGrade API")


@app.on_event("startup")
def startup() -> None:
    if os.getenv("AUTO_CREATE_TABLES", "false").lower() not in {"1", "true", "yes"}:
        print("AUTO_CREATE_TABLES disabled; skipping database schema creation.")
        return

    try:
        models.Base.metadata.create_all(bind=engine)
        print("Database tables checked/created.")
    except SQLAlchemyError as exc:
        print(f"Could not connect to the database during startup: {exc}")
        print("API started anyway. Database-backed endpoints will fail until the DB is reachable.")

# =========================================================
# CORS
# =========================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:3000",
        "https://essaygrade.vercel.app",
        "https://jombo-essaygrade.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# =========================================================
# API ROUTES
# =========================================================
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])

app.include_router(
    teacher.router,
    prefix="/api/teacher",
    tags=["Teacher"]
)

app.include_router(
    student.router,
    prefix="/api/student",
    tags=["Student"]
)

app.include_router(
    exams.router,
    prefix="/api/teacher",
    tags=["Exams"]
)

app.include_router(
    student_exams.router,
    prefix="/api/student",
    tags=["Student Exams"]
)

app.include_router(
    google_classroom.router,
    prefix="/api/teacher",
    tags=["Google Classroom"]
)

app.include_router(
    moodle_integration.router,
    prefix="/api/teacher",
    tags=["Moodle"]
)

app.include_router(
    student_classroom_router,
    prefix="/api/student",
    tags=["Student Classroom"]
)

app.include_router(
    student_moodle_router,
    prefix="/api/student",
    tags=["Student Moodle"]
)

app.include_router(
    grading.router,
    prefix="/api",
    tags=["Grading"]
)
app.include_router(
    teacher.router,
    prefix="/api/teacher",
    tags=["Teacher"]
)

# =========================================================
# ROOT
# =========================================================
@app.get("/")
def root():
    return {
        "message": "JomboEssayGrade API is running âœ…"
    }
