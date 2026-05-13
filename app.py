from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

# =========================================================
# LOAD ENVIRONMENT VARIABLES
# =========================================================
load_dotenv(
    dotenv_path=r"C:\PROJECTS\FINAL YEAR PROJECT\essay-grading-backend\.env"
)

print("🔑 GEMINI_API_KEY loaded:", "YES" if os.getenv("GEMINI_API_KEY") else "NO")
print("🔑 HF_API_KEY loaded:", "YES" if os.getenv("HF_API_KEY") else "NO")
print("🗄️ DATABASE_URL:", os.getenv("DATABASE_URL"))

# =========================================================
# DATABASE
# =========================================================
from database import engine
import models

# Create tables
models.Base.metadata.create_all(bind=engine)

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

# =========================================================
# ROOT
# =========================================================
@app.get("/")
def root():
    return {
        "message": "JomboEssayGrade API is running ✅"
    }