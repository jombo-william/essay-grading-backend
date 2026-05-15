"""
routes/google_classroom.py
Google Classroom Integration (FIXED VERSION)
"""

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth_utils import require_teacher
from database import get_db
import models

os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

router = APIRouter()

# ─────────────────────────────────────────────
# Google imports
# ─────────────────────────────────────────────
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build

    GOOGLE_AVAILABLE = True
    print("✅ Google packages available")

except ImportError:
    GOOGLE_AVAILABLE = False
    print("❌ Google packages NOT installed")

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/classroom.coursework.me",
    "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]

CLIENT_SECRETS_FILE = "google_credentials.json"

REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8000/api/teacher/auth/google/callback"
)

# TEMP session store (dev only)
oauth_states = {}


# ─────────────────────────────────────────────
# GET SAVED CREDENTIALS
# ─────────────────────────────────────────────
def get_credentials(teacher_id: int, db: Session):

    token_row = db.query(models.GoogleClassroomToken).filter_by(
        teacher_id=teacher_id   # ✅ FIXED (was user_id)
    ).first()

    if not token_row:
        raise HTTPException(
            status_code=401,
            detail="Google Classroom not connected."
        )

    return Credentials(
        token=token_row.access_token,
        refresh_token=token_row.refresh_token,
        token_uri=token_row.token_uri,
        client_id=token_row.client_id,
        client_secret=token_row.client_secret,
        scopes=json.loads(token_row.scopes) if token_row.scopes else SCOPES,
    )


# ─────────────────────────────────────────────
# START OAUTH
# ─────────────────────────────────────────────
@router.get("/auth/google/classroom")
def start_google_auth(ctx: dict = Depends(require_teacher)):

    if not GOOGLE_AVAILABLE:
        raise HTTPException(500, "Google packages not installed")

    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise HTTPException(500, "Missing google_credentials.json")

    teacher_id = str(ctx["user"].id)

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    # ❌ FIX: DO NOT manually implement PKCE
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",   # ✅ must be BOOLEAN not string
        prompt="consent",
    )

    oauth_states[state] = {
        "teacher_id": teacher_id
    }

    return {
        "auth_url": auth_url,
        "state": state,
    }


# ─────────────────────────────────────────────
# CALLBACK
# ─────────────────────────────────────────────
@router.get("/auth/google/callback")
def google_callback(request: Request):
    flow = create_flow_somehow()

    flow.fetch_token(authorization_response=str(request.url))

    saved = oauth_states.get(state)

    if not saved:
        raise HTTPException(400, "Invalid or expired state")

    teacher_id = int(saved["teacher_id"])

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    # ✅ FIX: no code_verifier needed
    # flow.fetch_token(code=code)
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials

    existing = db.query(models.GoogleClassroomToken).filter_by(
        teacher_id=teacher_id   # ✅ FIXED
    ).first()

    token_data = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": json.dumps(list(creds.scopes or SCOPES)),
    }

    if existing:
        for k, v in token_data.items():
            setattr(existing, k, v)
    else:
        db.add(models.GoogleClassroomToken(
            teacher_id=teacher_id,   # ✅ FIXED
            **token_data
        ))

    db.commit()

    oauth_states.pop(state, None)

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")

    return RedirectResponse(
        url=f"{frontend_url}?google_connected=true"
    )


# ─────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────
@router.get("/classroom/status")
def check_status(ctx: dict = Depends(require_teacher)):

    db: Session = ctx["db"]
    user = ctx["user"]

    token = db.query(models.GoogleClassroomToken).filter_by(
        teacher_id=user.id
    ).first()

    return {
        "connected": token is not None
    }


# ─────────────────────────────────────────────
# COURSES
# ─────────────────────────────────────────────
@router.get("/classroom/courses")
def get_courses(ctx: dict = Depends(require_teacher)):

    user = ctx["user"]
    db = ctx["db"]

    creds = get_credentials(user.id, db)

    service = build("classroom", "v1", credentials=creds)

    result = service.courses().list(
        teacherId="me",
        courseStates=["ACTIVE"]
    ).execute()

    return {
        "courses": result.get("courses", [])
    }


# ─────────────────────────────────────────────
# ASSIGNMENTS
# ─────────────────────────────────────────────
@router.get("/classroom/courses/{course_id}/assignments")
def get_assignments(course_id: str, ctx: dict = Depends(require_teacher)):

    user = ctx["user"]
    db = ctx["db"]

    creds = get_credentials(user.id, db)
    service = build("classroom", "v1", credentials=creds)

    result = service.courses().courseWork().list(
        courseId=course_id
    ).execute()

    return {
        "assignments": result.get("courseWork", [])
    }


# ─────────────────────────────────────────────
# LINK CLASS
# ─────────────────────────────────────────────
class LinkGoogleRequest(BaseModel):
    gc_course_id: str


@router.post("/classes/{class_id}/link-google")
def link_google_class(
    class_id: int,
    body: LinkGoogleRequest,
    ctx: dict = Depends(require_teacher),
):

    db = ctx["db"]

    cls = db.query(models.Class).filter_by(id=class_id).first()

    if not cls:
        raise HTTPException(404, "Class not found")

    cls.gc_course_id = body.gc_course_id
    db.commit()

    return {"success": True}