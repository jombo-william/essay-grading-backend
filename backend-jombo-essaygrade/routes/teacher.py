# import json
# import os
# import re
# import requests as http_requests
# from datetime import datetime
# from zoneinfo import ZoneInfo
# from fastapi import APIRouter, Depends, HTTPException, Header
# from sqlalchemy.orm import Session
# from sqlalchemy import func
# from pydantic import BaseModel
# from typing import Optional
# from database import get_db
# from auth_utils import require_teacher, validate_csrf
# import models

# router = APIRouter()

# BLANTYRE = ZoneInfo("Africa/Blantyre")


# def fmt_date(dt):
#     """Format datetime for output."""
#     if not dt:
#         return None
#     if hasattr(dt, 'year') and dt.year < 2000:
#         return None
#     if not dt: return None
#     if isinstance(dt, str): return dt
#     return dt.strftime("%Y-%m-%d %H:%M:%S")


# # ── GET /api/teacher/assignments ─────────────────────────────────────────────
# # Matches: teacher/get_assignments.php

# @router.get("/assignments")
# def get_assignments(ctx: dict = Depends(require_teacher)):
#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     rows = (
#         db.query(
#             models.Assignment,
#             func.count(models.Submission.id).label("submission_count")
#         )
#         .outerjoin(models.Submission, models.Submission.assignment_id == models.Assignment.id)
#         .filter(
#             models.Assignment.teacher_id == user.id,
#             models.Assignment.is_active  == True,
#         )
#         .group_by(models.Assignment.id)
#         .order_by(models.Assignment.created_at.desc())
#         .all()
#     )

#     assignments = []
#     for a, count in rows:
#         assignments.append({
#             "id":                 a.id,
#             "title":              a.title,
#             "description":        a.description,
#             "instructions":       a.instructions,
#             "reference_material": a.reference_material,
#             "max_score":          a.max_score,
#             "due_date":           fmt_date(a.due_date),
#             "rubric":             json.loads(a.rubric) if a.rubric else None,
#             "is_active":          a.is_active,
#             "created_at":         fmt_date(a.created_at),
#             "submission_count":   count,
#         })

#     return {"success": True, "assignments": assignments}


# # ── POST /api/teacher/assignments/create ─────────────────────────────────────
# # Matches: teacher/create_assignment.php

# class CreateAssignmentRequest(BaseModel):
#     title:              str
#     description:        Optional[str] = ""
#     instructions:       str
#     reference_material: Optional[str] = ""
#     max_score:          int = 100
#     due_date:           str
#     rubric:             Optional[dict] = None
#     csrf_token:         Optional[str] = None


# @router.post("/assignments/create")
# def create_assignment(
#     body: CreateAssignmentRequest,
#     x_csrf_token: Optional[str] = Header(default=None),
#     ctx: dict = Depends(require_teacher),
# ):
#     user: models.User       = ctx["user"]
#     session: models.UserSession = ctx["session"]
#     db: Session             = ctx["db"]

#     validate_csrf(session, x_csrf_token, body.csrf_token)

#     title        = body.title.strip()
#     instructions = body.instructions.strip()
#     due_date_str = body.due_date.strip()

#     if not title or not instructions or not due_date_str:
#         raise HTTPException(status_code=422, detail="Title, instructions, and due date are required")

#     if body.max_score < 1:
#         raise HTTPException(status_code=422, detail="Max score must be at least 1")

#     if body.rubric:
#         total = sum(body.rubric.values())
#         if total != 100:
#             raise HTTPException(status_code=422, detail="Rubric weights must total 100")

#     # Parse due_date in Blantyre timezone — matches PHP date_default_timezone_set('Africa/Blantyre')
#     try:
#         # Handle both "2025-03-12T14:30" and "2025-03-12 14:30:00" formats
#         due_date_str = due_date_str.replace("T", " ")
#         if len(due_date_str) == 16:
#             due_date_str += ":00"
#         naive_dt = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M:%S")
#         due_date = naive_dt.replace(tzinfo=BLANTYRE)
#     except Exception:
#         raise HTTPException(status_code=422, detail="Invalid due date format")

#     assignment = models.Assignment(
#         teacher_id         = user.id,
#         title              = title,
#         description        = body.description.strip() if body.description else None,
#         instructions       = instructions,
#         reference_material = body.reference_material.strip() if body.reference_material else None,
#         max_score          = body.max_score,
#         due_date           = due_date,
#         rubric             = json.dumps(body.rubric) if body.rubric else None,
#     )
#     db.add(assignment)
#     db.commit()
#     db.refresh(assignment)

#     return {"success": True, "message": "Assignment created", "id": assignment.id}


# # ── POST /api/teacher/assignments/update ─────────────────────────────────────
# # Matches: teacher/update_assignment.php

# class UpdateAssignmentRequest(BaseModel):
#     id:                 int
#     title:              str
#     description:        Optional[str] = ""
#     instructions:       str
#     reference_material: Optional[str] = ""
#     max_score:          int = 100
#     due_date:           str
#     rubric:             Optional[dict] = None
#     csrf_token:         Optional[str] = None


# @router.post("/assignments/update")
# def update_assignment(
#     body: UpdateAssignmentRequest,
#     x_csrf_token: Optional[str] = Header(default=None),
#     ctx: dict = Depends(require_teacher),
# ):
#     user: models.User           = ctx["user"]
#     session: models.UserSession = ctx["session"]
#     db: Session                 = ctx["db"]

#     validate_csrf(session, x_csrf_token, body.csrf_token)

#     title        = body.title.strip()
#     instructions = body.instructions.strip()
#     due_date_str = body.due_date.strip()

#     if not body.id or not title or not instructions or not due_date_str:
#         raise HTTPException(status_code=422, detail="ID, title, instructions, and due date are required")

#     # Verify ownership — matches PHP check
#     assignment = db.query(models.Assignment).filter(
#         models.Assignment.id         == body.id,
#         models.Assignment.teacher_id == user.id,
#     ).first()

#     if not assignment:
#         raise HTTPException(status_code=403, detail="Assignment not found or access denied")

#     if body.rubric:
#         total = sum(body.rubric.values())
#         if total != 100:
#             raise HTTPException(status_code=422, detail="Rubric weights must total 100")

#     try:
#         due_date_str = due_date_str.replace("T", " ")
#         if len(due_date_str) == 16:
#             due_date_str += ":00"
#         naive_dt = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M:%S")
#         due_date = naive_dt.replace(tzinfo=BLANTYRE)
#     except Exception:
#         raise HTTPException(status_code=422, detail="Invalid due date format")

#     assignment.title              = title
#     assignment.description        = body.description.strip() if body.description else None
#     assignment.instructions       = instructions
#     assignment.reference_material = body.reference_material.strip() if body.reference_material else None
#     assignment.max_score          = body.max_score
#     assignment.due_date           = due_date
#     assignment.rubric             = json.dumps(body.rubric) if body.rubric else None

#     db.commit()

#     return {"success": True, "message": "Assignment updated"}


# # ── GET /api/teacher/submissions ─────────────────────────────────────────────
# # Matches: teacher/get_submissions.php

# @router.get("/submissions")
# def get_submissions(ctx: dict = Depends(require_teacher)):
#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     rows = (
#         db.query(models.Submission, models.User, models.Assignment)
#         .join(models.User,       models.User.id       == models.Submission.student_id)
#         .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
#         .filter(models.Assignment.teacher_id == user.id)
#         .order_by(models.Submission.submitted_at.desc())
#         .all()
#     )

#     submissions = []
#     for s, u, a in rows:
#         submissions.append({
#             "id":                  s.id,
#             "assignment_id":       s.assignment_id,
#             "student_id":          s.student_id,
#             "essay_text":          s.essay_text,
#             "submit_mode":         s.submit_mode,
#             "file_name":           s.file_name,
#             "ai_score":            s.ai_score,
#             "ai_feedback":         s.ai_feedback,
#             "ai_detection_score":  s.ai_detection_score,
#             "ai_graded_at":        fmt_date(s.ai_graded_at),
#             "final_score":         s.final_score,
#             "teacher_feedback":    s.teacher_feedback,
#             "status":              s.status,
#             "submitted_at":        fmt_date(s.submitted_at),
#             "graded_at":           fmt_date(s.graded_at),
#             "student_name":        u.name,
#             "student_email":       u.email,
#             "assignment_title":    a.title,
#             "max_score":           a.max_score,
#         })

#     return {"success": True, "submissions": submissions}


# # ── GET /api/teacher/submissions/pending ─────────────────────────────────────
# # Matches: teacher/get_pending_grading.php

# @router.get("/submissions/pending")
# def get_pending_grading(ctx: dict = Depends(require_teacher)):
#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     rows = (
#         db.query(models.Submission, models.User, models.Assignment)
#         .join(models.User,       models.User.id       == models.Submission.student_id)
#         .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
#         .filter(
#             models.Assignment.teacher_id == user.id,
#             models.Submission.status.in_(["submitted", "ai_graded"]),
#             models.Submission.final_score == None,
#         )
#         .order_by(models.Submission.submitted_at.asc())
#         .all()
#     )

#     submissions = []
#     for s, u, a in rows:
#         submissions.append({
#             "id":                 s.id,
#             "assignment_id":      s.assignment_id,
#             "student_id":         s.student_id,
#             "essay_text":         s.essay_text,
#             "submit_mode":        s.submit_mode,
#             "file_name":          s.file_name,
#             "ai_score":           s.ai_score,
#             "ai_feedback":        s.ai_feedback,
#             "ai_detection_score": s.ai_detection_score,
#             "status":             s.status,
#             "submitted_at":       fmt_date(s.submitted_at),
#             "student_name":       u.name,
#             "student_email":      u.email,
#             "assignment_title":   a.title,
#             "max_score":          a.max_score,
#         })

#     return {"success": True, "submissions": submissions}


# # ── POST /api/teacher/submissions/grade ──────────────────────────────────────
# # Matches: teacher/override_grade.php

# class GradeRequest(BaseModel):
#     submission_id: int
#     score:         int
#     feedback:      Optional[str] = ""
#     csrf_token:    Optional[str] = None


# @router.post("/submissions/grade")
# def override_grade(
#     body: GradeRequest,
#     x_csrf_token: Optional[str] = Header(default=None),
#     ctx: dict = Depends(require_teacher),
# ):
#     user: models.User           = ctx["user"]
#     session: models.UserSession = ctx["session"]
#     db: Session                 = ctx["db"]

#     validate_csrf(session, x_csrf_token, body.csrf_token)

#     if not body.submission_id:
#         raise HTTPException(status_code=422, detail="submission_id is required")

#     # Verify ownership — matches PHP check
#     submission = (
#         db.query(models.Submission)
#         .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
#         .filter(
#             models.Submission.id     == body.submission_id,
#             models.Assignment.teacher_id == user.id,
#         )
#         .first()
#     )

#     if not submission:
#         raise HTTPException(status_code=403, detail="Submission not found or access denied")

#     assignment = db.query(models.Assignment).filter(models.Assignment.id == submission.assignment_id).first()

#     if body.score < 0 or body.score > assignment.max_score:
#         raise HTTPException(status_code=422, detail=f"Score must be between 0 and {assignment.max_score}")

#     submission.final_score      = body.score
#     submission.teacher_feedback = body.feedback.strip() if body.feedback else None
#     submission.status           = "graded"
#     submission.graded_at        = datetime.utcnow()

#     db.commit()

#     return {"success": True, "message": "Grade saved successfully"}






import json
import os
import re
import requests as http_requests
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional
from database import get_db
from auth_utils import require_teacher, validate_csrf
import models

router = APIRouter()

BLANTYRE    = ZoneInfo("Africa/Blantyre")
UPLOAD_DIR  = "uploads/reference_materials"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def fmt_date(dt):
    if not dt: return None
    if hasattr(dt, 'year') and dt.year < 2000: return None
    if not dt: return None
    if isinstance(dt, str): return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def extract_text_from_file(file_content: bytes, filename: str) -> str:
    """Extract text from uploaded files."""
    ext = filename.lower().split('.')[-1]
    if ext == 'txt':
        return file_content.decode('utf-8', errors='ignore')
    elif ext == 'pdf':
        try:
            import io
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(file_content))
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text.strip()
        except Exception as e:
            return f"[Could not extract text from PDF: {e}]"
    else:
        return file_content.decode('utf-8', errors='ignore')


# ── GET /api/teacher/assignments ─────────────────────────────────────────────

@router.get("/assignments")
def get_assignments(ctx: dict = Depends(require_teacher)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    rows = (
        db.query(
            models.Assignment,
            func.count(models.Submission.id).label("submission_count")
        )
        .outerjoin(models.Submission, models.Submission.assignment_id == models.Assignment.id)
        .filter(
            models.Assignment.teacher_id == user.id,
            models.Assignment.is_active  == True,
        )
        .group_by(models.Assignment.id)
        .order_by(models.Assignment.created_at.desc())
        .all()
    )

    assignments = []
    for a, count in rows:
        assignments.append({
            "id":                 a.id,
            "title":              a.title,
            "description":        a.description,
            "instructions":       a.instructions,
            "reference_material": a.reference_material,
            "max_score":          a.max_score,
            "due_date":           fmt_date(a.due_date),
            "rubric":             json.loads(a.rubric) if a.rubric else None,
            "is_active":          a.is_active,
            "created_at":         fmt_date(a.created_at),
            "submission_count":   count,
        })

    return {"success": True, "assignments": assignments}


# ── POST /api/teacher/assignments/create ─────────────────────────────────────

class CreateAssignmentRequest(BaseModel):
    title:              str
    description:        Optional[str] = ""
    instructions:       str
    reference_material: Optional[str] = ""
    max_score:          int = 100
    due_date:           str
    rubric:             Optional[dict] = None
    csrf_token:         Optional[str] = None


@router.post("/assignments/create")
def create_assignment(
    body: CreateAssignmentRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    title        = body.title.strip()
    instructions = body.instructions.strip()
    due_date_str = body.due_date.strip()

    if not title or not instructions or not due_date_str:
        raise HTTPException(status_code=422, detail="Title, instructions, and due date are required")

    if body.max_score < 1:
        raise HTTPException(status_code=422, detail="Max score must be at least 1")

    if body.rubric:
        total = sum(body.rubric.values())
        if total != 100:
            raise HTTPException(status_code=422, detail="Rubric weights must total 100")

    try:
        due_date_str = due_date_str.replace("T", " ")
        if len(due_date_str) == 16:
            due_date_str += ":00"
        naive_dt = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M:%S")
        due_date = naive_dt.replace(tzinfo=BLANTYRE)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid due date format")

    assignment = models.Assignment(
        teacher_id         = user.id,
        title              = title,
        description        = body.description.strip() if body.description else None,
        instructions       = instructions,
        reference_material = body.reference_material.strip() if body.reference_material else None,
        max_score          = body.max_score,
        due_date           = due_date,
        rubric             = json.dumps(body.rubric) if body.rubric else None,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    return {"success": True, "message": "Assignment created", "id": assignment.id}


# ── POST /api/teacher/assignments/upload-reference ───────────────────────────
# Upload a file as reference material for an assignment

@router.post("/assignments/{assignment_id}/upload-reference")
async def upload_reference_material(
    assignment_id: int,
    file: UploadFile = File(...),
    ctx: dict = Depends(require_teacher),
):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    content  = await file.read()
    text     = extract_text_from_file(content, file.filename)

    if not text or text.startswith("[Could not"):
        raise HTTPException(status_code=422, detail="Could not extract text from file")

    # Append to existing reference material or replace
    existing = assignment.reference_material or ""
    if existing:
        assignment.reference_material = existing + "\n\n--- Uploaded: " + file.filename + " ---\n" + text[:5000]
    else:
        assignment.reference_material = f"--- {file.filename} ---\n" + text[:5000]

    db.commit()

    return {
        "success": True,
        "message": f"Reference material from '{file.filename}' added successfully",
        "chars_extracted": len(text),
    }


# ── POST /api/teacher/assignments/update ─────────────────────────────────────

class UpdateAssignmentRequest(BaseModel):
    id:                 int
    title:              str
    description:        Optional[str] = ""
    instructions:       str
    reference_material: Optional[str] = ""
    max_score:          int = 100
    due_date:           str
    rubric:             Optional[dict] = None
    csrf_token:         Optional[str] = None


@router.post("/assignments/update")
def update_assignment(
    body: UpdateAssignmentRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    title        = body.title.strip()
    instructions = body.instructions.strip()
    due_date_str = body.due_date.strip()

    if not body.id or not title or not instructions or not due_date_str:
        raise HTTPException(status_code=422, detail="ID, title, instructions, and due date are required")

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if not assignment:
        raise HTTPException(status_code=403, detail="Assignment not found or access denied")

    if body.rubric:
        total = sum(body.rubric.values())
        if total != 100:
            raise HTTPException(status_code=422, detail="Rubric weights must total 100")

    try:
        due_date_str = due_date_str.replace("T", " ")
        if len(due_date_str) == 16:
            due_date_str += ":00"
        naive_dt = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M:%S")
        due_date = naive_dt.replace(tzinfo=BLANTYRE)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid due date format")

    assignment.title              = title
    assignment.description        = body.description.strip() if body.description else None
    assignment.instructions       = instructions
    assignment.reference_material = body.reference_material.strip() if body.reference_material else None
    assignment.max_score          = body.max_score
    assignment.due_date           = due_date
    assignment.rubric             = json.dumps(body.rubric) if body.rubric else None

    db.commit()

    return {"success": True, "message": "Assignment updated"}


# ── GET /api/teacher/submissions ─────────────────────────────────────────────

@router.get("/submissions")
def get_submissions(ctx: dict = Depends(require_teacher)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    rows = (
        db.query(models.Submission, models.User, models.Assignment)
        .join(models.User,       models.User.id       == models.Submission.student_id)
        .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
        .filter(models.Assignment.teacher_id == user.id)
        .order_by(models.Submission.submitted_at.desc())
        .all()
    )

    submissions = []
    for s, u, a in rows:
        submissions.append({
            "id":                  s.id,
            "assignment_id":       s.assignment_id,
            "student_id":          s.student_id,
            "essay_text":          s.essay_text,
            "submit_mode":         s.submit_mode,
            "file_name":           s.file_name,
            "ai_score":            s.ai_score,
            "ai_feedback":         s.ai_feedback,
            "ai_detection_score":  s.ai_detection_score,
            "ai_graded_at":        fmt_date(s.ai_graded_at),
            "final_score":         s.final_score,
            "teacher_feedback":    s.teacher_feedback,
            "status":              s.status,
            "submitted_at":        fmt_date(s.submitted_at),
            "graded_at":           fmt_date(s.graded_at),
            "student_name":        u.name,
            "student_email":       u.email,
            "assignment_title":    a.title,
            "max_score":           a.max_score,
        })

    return {"success": True, "submissions": submissions}


# ── GET /api/teacher/submissions/pending ─────────────────────────────────────

@router.get("/submissions/pending")
def get_pending_grading(ctx: dict = Depends(require_teacher)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    rows = (
        db.query(models.Submission, models.User, models.Assignment)
        .join(models.User,       models.User.id       == models.Submission.student_id)
        .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
        .filter(
            models.Assignment.teacher_id == user.id,
            models.Submission.status.in_(["submitted", "ai_graded"]),
            models.Submission.final_score == None,
        )
        .order_by(models.Submission.submitted_at.asc())
        .all()
    )

    submissions = []
    for s, u, a in rows:
        submissions.append({
            "id":                 s.id,
            "assignment_id":      s.assignment_id,
            "student_id":         s.student_id,
            "essay_text":         s.essay_text,
            "submit_mode":        s.submit_mode,
            "file_name":          s.file_name,
            "ai_score":           s.ai_score,
            "ai_feedback":        s.ai_feedback,
            "ai_detection_score": s.ai_detection_score,
            "status":             s.status,
            "submitted_at":       fmt_date(s.submitted_at),
            "student_name":       u.name,
            "student_email":      u.email,
            "assignment_title":   a.title,
            "max_score":          a.max_score,
        })

    return {"success": True, "submissions": submissions}


# ── POST /api/teacher/submissions/grade ──────────────────────────────────────
# Teacher approves AI grade or overrides with their own score

class GradeRequest(BaseModel):
    submission_id: int
    score:         int
    feedback:      Optional[str] = ""
    csrf_token:    Optional[str] = None


@router.post("/submissions/grade")
def override_grade(
    body: GradeRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    if not body.submission_id:
        raise HTTPException(status_code=422, detail="submission_id is required")

    submission = (
        db.query(models.Submission)
        .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
        .filter(
            models.Submission.id         == body.submission_id,
            models.Assignment.teacher_id == user.id,
        )
        .first()
    )

    if not submission:
        raise HTTPException(status_code=403, detail="Submission not found or access denied")

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id == submission.assignment_id
    ).first()

    if body.score < 0 or body.score > assignment.max_score:
        raise HTTPException(status_code=422, detail=f"Score must be between 0 and {assignment.max_score}")

    # ── Teacher approval — results now visible to student ──
    submission.final_score      = body.score
    submission.teacher_feedback = body.feedback.strip() if body.feedback else None
    submission.status           = "graded"
    submission.graded_at        = datetime.utcnow()

    db.commit()

    print(f"✅ Teacher approved grade: {body.score}/{assignment.max_score} for submission {body.submission_id}")

    return {"success": True, "message": "Grade approved and released to student"}