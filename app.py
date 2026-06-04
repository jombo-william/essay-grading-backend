import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Configure stdout/stderr encoding
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

print("GEMINI_API_KEY loaded:", "YES" if os.getenv("GEMINI_API_KEY") else "NO")
print("HF_API_KEY loaded:", "YES" if os.getenv("HF_API_KEY") else "NO")
print("DATABASE_URL configured:", "YES" if os.getenv("DATABASE_URL") else "NO")

from database import engine  # noqa: E402
import models  # noqa: E402
from routes import (  # noqa: E402
    auth,
    exams,
    google_classroom,
    grading,
    moodle_integration,
    student,
    student_exams,
    teacher,
    chat,
)
from routes.student_classroom import router as student_classroom_router  # noqa: E402
from routes.student_moodle import router as student_moodle_router  # noqa: E402

# Create FastAPI app first
app = FastAPI(title="JomboEssayGrade API")

# Define allowed origins for CORS and Socket.IO
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://essaygrade.vercel.app",
    "https://jombo-essaygrade.vercel.app",
    "https://essaygrade-ai-xi.vercel.app",
    "https://essaygrade-aelxsmi8m-williams-projects-f21505ba.vercel.app",
]

# Setup Socket.IO
import socketio
from sqlalchemy.orm import Session
from database import SessionLocal
from models import ChatMessage

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=ALLOWED_ORIGINS,
    cors_credentials=True,
)
socket_app = socketio.ASGIApp(sio)


@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")


@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")


@sio.event
async def join_submission(sid, data):
    submission_id = data.get("submission_id")
    if submission_id:
        await sio.enter_room(sid, f"submission_{submission_id}")


@sio.event
async def leave_submission(sid, data):
    submission_id = data.get("submission_id")
    if submission_id:
        await sio.leave_room(sid, f"submission_{submission_id}")


@sio.event
async def send_message(sid, data):
    submission_id = data.get("submission_id")
    sender_id = data.get("sender_id")
    sender_role = data.get("sender_role")
    sender_name = data.get("sender_name")
    message = data.get("message")
    
    if not all([submission_id, sender_id, sender_role, sender_name, message]):
        return
    
    db = SessionLocal()
    try:
        chat_msg = ChatMessage(
            submission_id=submission_id,
            sender_id=sender_id,
            sender_role=sender_role,
            sender_name=sender_name,
            message=message
        )
        db.add(chat_msg)
        db.commit()
        db.refresh(chat_msg)
        
        await sio.emit("new_message", {
            "id": chat_msg.id,
            "submission_id": chat_msg.submission_id,
            "sender_id": chat_msg.sender_id,
            "sender_role": chat_msg.sender_role,
            "sender_name": chat_msg.sender_name,
            "message": chat_msg.message,
            "created_at": chat_msg.created_at.isoformat() if chat_msg.created_at else None
        }, room=f"submission_{submission_id}")
    except Exception as e:
        print(f"Error sending message: {e}")
        db.rollback()
    finally:
        db.close()


# Mount Socket.IO before adding middleware
app.mount("/socket.io", socket_app)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)


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


# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(teacher.router, prefix="/api/teacher", tags=["Teacher"])
app.include_router(student.router, prefix="/api/student", tags=["Student"])
app.include_router(exams.router, prefix="/api/teacher", tags=["Exams"])
app.include_router(student_exams.router, prefix="/api/student", tags=["Student Exams"])
app.include_router(google_classroom.router, prefix="/api/teacher", tags=["Google Classroom"])
app.include_router(moodle_integration.router, prefix="/api/teacher", tags=["Moodle"])
app.include_router(student_classroom_router, prefix="/api/student", tags=["Student Classroom"])
app.include_router(student_moodle_router, prefix="/api/student", tags=["Student Moodle"])
app.include_router(grading.router, prefix="/api", tags=["Grading"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "JomboEssayGrade API is running"}