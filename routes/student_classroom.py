"""
routes/student_classroom.py
Google Classroom integration for STUDENTS.

Scopes used are student-facing:
  - classroom.courses.readonly          → list enrolled courses
  - classroom.coursework.me             → list assignments in those courses
  - classroom.student-submissions.me.readonly → read the student's own submissions
  - drive.readonly                      → download attached files
"""

import json
import os
import secrets
import hashlib
import base64

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from auth_utils import require_student          # same pattern as require_teacher
from database import get_db
import models

os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # Google sometimes returns extra scopes

_code_verifiers: dict = {}

router = APIRouter()

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

# Student-specific scopes — no access to other students' submissions
STUDENT_SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    #"https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

CLIENT_SECRETS_FILE = "google_credentials.json"
REDIRECT_URI = os.getenv(
    "GOOGLE_STUDENT_REDIRECT_URI",
    "http://localhost:8000/api/student/auth/google/callback"
)


# ── Helper ────────────────────────────────────────────────────────────────────
def get_student_credentials(student_id: int, db: Session):
    row = db.query(models.StudentGoogleToken).filter_by(student_id=student_id).first()
    if not row:
        raise HTTPException(
            status_code=401,
            detail="Google Classroom not connected. Please connect your Google account first."
        )
    return Credentials(
        token         = row.access_token,
        refresh_token = row.refresh_token,
        token_uri     = row.token_uri,
        client_id     = row.client_id,
        client_secret = row.client_secret,
        scopes        = json.loads(row.scopes) if row.scopes else STUDENT_SCOPES,
    )


# ── GET /api/student/auth/google/classroom ────────────────────────────────────
@router.get("/auth/google/classroom")
def start_student_google_auth(ctx: dict = Depends(require_student)):
    if not GOOGLE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Google packages not installed.")
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise HTTPException(status_code=500, detail="google_credentials.json not found.")

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    student_id = str(ctx["user"].id)

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=STUDENT_SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=student_id,
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    _code_verifiers[student_id] = code_verifier
    return {"auth_url": auth_url, "state": state}


# ── GET /api/student/auth/google/callback ─────────────────────────────────────
@router.get("/auth/google/callback")
def student_google_callback(code: str, state: str, db: Session = Depends(get_db)):
    student_id = int(state)
    code_verifier = _code_verifiers.pop(str(student_id), None)
    if not code_verifier:
        raise HTTPException(status_code=400, detail="OAuth session expired. Please try again.")

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=STUDENT_SCOPES,
        redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(code=code, code_verifier=code_verifier)
    creds = flow.credentials

    existing = db.query(models.StudentGoogleToken).filter_by(student_id=student_id).first()
    token_data = dict(
        access_token  = creds.token,
        refresh_token = creds.refresh_token,
        token_uri     = creds.token_uri,
        client_id     = creds.client_id,
        client_secret = creds.client_secret,
        scopes        = json.dumps(list(creds.scopes or STUDENT_SCOPES)),
    )
    if existing:
        for k, v in token_data.items():
            setattr(existing, k, v)
    else:
        db.add(models.StudentGoogleToken(student_id=student_id, **token_data))
    db.commit()

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return RedirectResponse(url=f"{frontend_url}?google_connected=true")


# ── GET /api/student/classroom/status ────────────────────────────────────────
@router.get("/classroom/status")
def student_connection_status(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]
    row = db.query(models.StudentGoogleToken).filter_by(student_id=user.id).first()
    return {"connected": row is not None}


# ── GET /api/student/classroom/courses ───────────────────────────────────────
@router.get("/classroom/courses")
def student_get_courses(ctx: dict = Depends(require_student)):
    """Returns courses the student is ENROLLED in (not teaching)."""
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    creds   = get_student_credentials(user.id, db)
    service = build("classroom", "v1", credentials=creds)

    result = service.courses().list(studentId="me", courseStates=["ACTIVE"]).execute()
    raw    = result.get("courses", [])

    return {
        "success": True,
        "courses": [
            {
                "id":      c.get("id"),
                "name":    c.get("name"),
                "section": c.get("section", ""),
                "subject": c.get("descriptionHeading", ""),
            }
            for c in raw
        ]
    }


# ── GET /api/student/classroom/courses/{course_id}/assignments ───────────────
@router.get("/classroom/courses/{course_id}/assignments")
def student_get_assignments(course_id: str, ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    creds   = get_student_credentials(user.id, db)
    service = build("classroom", "v1", credentials=creds)

    result = service.courses().courseWork().list(courseId=course_id).execute()
    work   = result.get("courseWork", [])

    # # Check which ones already have a local assignment linked via gc_coursework_id
    # gc_ids = [a.get("id") for a in work]
    # linked = db.query(models.Assignment).filter(
    #     models.Assignment.gc_coursework_id.in_(gc_ids)
    # ).all()
    # linked_map = {a.gc_coursework_id: a.id for a in linked}

    linked_map = {}

    return {
        "success": True,
        "assignments": [
            {
                "id":               a.get("id"),
                "title":            a.get("title"),
                "description":      a.get("description", ""),
                "maxPoints":        a.get("maxPoints", 100),
                #"local_assignment_id": linked_map.get(a.get("id")),  # None if not linked
                "local_assignment_id": None,
                "dueDate":          a.get("dueDate"),
            }
            for a in work
        ]
    }


# ── POST /api/student/classroom/submit ───────────────────────────────────────
@router.post("/classroom/submit")
def student_submit_from_classroom(
    gc_course_id:        str = Query(...),
    gc_coursework_id:    str = Query(...),
    local_assignment_id: int = Query(...),
    ctx: dict = Depends(require_student)
):
    """
    Fetches the student's own submission from Google Classroom,
    extracts the essay text, and submits it into the local system for AI grading.
    """
    from routes.ai_grader import grade_with_local_model

    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    # Verify local assignment exists
    assignment = db.query(models.Assignment).filter_by(id=local_assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Local assignment not found.")

    creds         = get_student_credentials(user.id, db)
    classroom_svc = build("classroom", "v1", credentials=creds)
    drive_svc     = build("drive",     "v3", credentials=creds)

    # Fetch the student's own submission
    subs = classroom_svc.courses().courseWork().studentSubmissions().list(
        courseId     = gc_course_id,
        courseWorkId = gc_coursework_id,
        userId       = "me",
    ).execute()

    student_subs = subs.get("studentSubmissions", [])
    if not student_subs:
        raise HTTPException(status_code=404, detail="No submission found in Google Classroom for this assignment.")

    gs          = student_subs[0]
    essay_text  = ""
    attachments = gs.get("assignmentSubmission", {}).get("attachments", [])

    for att in attachments:
        if "driveFile" in att:
            file_id = att["driveFile"]["id"]
            try:
                file_meta = drive_svc.files().get(fileId=file_id, fields="mimeType,name").execute()
                mime      = file_meta.get("mimeType", "")

                if mime == "application/vnd.google-apps.document":
                    content = drive_svc.files().export(fileId=file_id, mimeType="text/plain").execute()
                    essay_text += content.decode("utf-8", errors="ignore")
                elif mime == "application/pdf":
                    content = drive_svc.files().get_media(fileId=file_id).execute()
                    try:
                        import io, pypdf
                        reader = pypdf.PdfReader(io.BytesIO(content))
                        for page in reader.pages:
                            essay_text += page.extract_text() or ""
                    except Exception:
                        essay_text += content.decode("utf-8", errors="ignore")
                elif "text" in mime:
                    content = drive_svc.files().get_media(fileId=file_id).execute()
                    essay_text += content.decode("utf-8", errors="ignore")
                else:
                    content = drive_svc.files().get_media(fileId=file_id).execute()
                    essay_text += content.decode("utf-8", errors="ignore")
            except Exception as e:
                print(f"⚠️ Could not read Drive file {file_id}: {e}")

    if not essay_text.strip():
        raise HTTPException(
            status_code=422,
            detail="No readable text found in your Google Classroom submission. Make sure you submitted a Google Doc, PDF, or text file."
        )

    word_count = len(essay_text.split())

    # Grade with AI
    grade = grade_with_local_model(
        assignment = assignment,
        essay_text = essay_text,
        word_count = word_count,
    )

    # Upsert submission
    existing = db.query(models.Submission).filter_by(
        assignment_id = assignment.id,
        student_id    = user.id,
    ).first()

    if existing:
        existing.essay_text         = essay_text[:5000]
        existing.ai_score           = grade["score"]
        existing.ai_feedback        = grade["feedback"]
        existing.ai_detection_score = 0
        existing.status             = "ai_graded"
        existing.submit_mode        = "upload"
        existing.file_name          = f"gc_{gs.get('id', 'submission')}"
    else:
        db.add(models.Submission(
            assignment_id      = assignment.id,
            student_id         = user.id,
            essay_text         = essay_text[:5000],
            submit_mode        = "upload",
            file_name          = f"gc_{gs.get('id', 'submission')}",
            ai_score           = grade["score"],
            ai_feedback        = grade["feedback"],
            ai_detection_score = 0,
            status             = "ai_graded",
        ))

    db.commit()

    return {
        "success":   True,
        "message":   "Submission imported and graded successfully!",
        "score":     grade["score"],
        "feedback":  grade["feedback"],
        "max_score": assignment.max_score,
    }