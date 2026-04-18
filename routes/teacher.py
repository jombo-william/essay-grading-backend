


import json
import os
import re
import requests as http_requests
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List
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


def teacher_owns_class(db: Session, teacher_id: int, class_id: int) -> bool:
    """Returns True if the teacher is assigned to this class via teacher_classes."""
    return db.query(models.TeacherClass).filter_by(
        teacher_id=teacher_id,
        class_id=class_id,
    ).first() is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

# ── GET /api/teacher/classes ──────────────────────────────────────────────────

@router.get("/classes")
def get_classes(ctx: dict = Depends(require_teacher)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    rows = (
        db.query(
            models.Class,
            func.count(func.distinct(models.Assignment.id)).label("total_assignments"),
            func.count(func.distinct(models.ClassEnrollment.student_id)).label("total_students"),
        )
        # Join through teacher_classes to find classes assigned to this teacher
        .join(models.TeacherClass, models.TeacherClass.class_id == models.Class.id)
        .outerjoin(models.Assignment,      models.Assignment.class_id      == models.Class.id)
        .outerjoin(models.ClassEnrollment, models.ClassEnrollment.class_id == models.Class.id)
        .filter(
            models.TeacherClass.teacher_id == user.id,
            models.Class.is_active         == True,
        )
        .group_by(models.Class.id)
        .order_by(models.Class.created_at.desc())
        .all()
    )

    classes = []
    for c, total_assignments, total_students in rows:
        classes.append({
            "id":                c.id,
            "name":              c.name,
            "description":       c.description,
            "subject":           c.subject,
            "section":           c.section,
            "is_active":         c.is_active,
            "created_at":        fmt_date(c.created_at),
            "total_assignments": total_assignments,
            "total_students":    total_students,
        })

    return {"success": True, "classes": classes}


# ── GET /api/teacher/classes/{class_id}/students ─────────────────────────────

@router.get("/classes/{class_id}/students")
def get_class_students(class_id: int, ctx: dict = Depends(require_teacher)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    if not teacher_owns_class(db, user.id, class_id):
        raise HTTPException(status_code=404, detail="Class not found or access denied.")

    rows = (
        db.query(models.User, models.ClassEnrollment)
        .join(models.ClassEnrollment, models.ClassEnrollment.student_id == models.User.id)
        .filter(models.ClassEnrollment.class_id == class_id)
        .order_by(models.User.name)
        .all()
    )

    students = [
        {
            "id":          u.id,
            "name":        u.name,
            "email":       u.email,
            "enrolled_at": fmt_date(e.enrolled_at),
        }
        for u, e in rows
    ]

    return {"success": True, "students": students}


# ── POST /api/teacher/classes/{class_id}/enroll ──────────────────────────────

class EnrollRequest(BaseModel):
    student_ids: List[int]
    csrf_token:  Optional[str] = None


@router.post("/classes/{class_id}/enroll")
def enroll_students(
    class_id: int,
    body: EnrollRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    if not teacher_owns_class(db, user.id, class_id):
        raise HTTPException(status_code=404, detail="Class not found or access denied.")

    added = 0
    for sid in body.student_ids:
        exists = db.query(models.ClassEnrollment).filter_by(
            class_id=class_id, student_id=sid
        ).first()
        if not exists:
            db.add(models.ClassEnrollment(class_id=class_id, student_id=sid))
            added += 1

    db.commit()
    return {"success": True, "message": f"{added} student(s) enrolled."}


# ── POST /api/teacher/classes/{class_id}/unenroll ────────────────────────────

class UnenrollRequest(BaseModel):
    student_id: int
    csrf_token: Optional[str] = None


@router.post("/classes/{class_id}/unenroll")
def unenroll_student(
    class_id: int,
    body: UnenrollRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    enrollment = db.query(models.ClassEnrollment).filter_by(
        class_id=class_id, student_id=body.student_id
    ).first()

    if enrollment:
        db.delete(enrollment)
        db.commit()

    return {"success": True, "message": "Student removed from class."}


# ═══════════════════════════════════════════════════════════════════════════════
#  ASSIGNMENTS  (filtered by class_id)
# ═══════════════════════════════════════════════════════════════════════════════

# ── GET /api/teacher/assignments?class_id=<id> ────────────────────────────────

@router.get("/assignments")
def get_assignments(
    class_id: Optional[int] = Query(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    q = (
        db.query(
            models.Assignment,
            func.count(models.Submission.id).label("submission_count")
        )
        .outerjoin(models.Submission, models.Submission.assignment_id == models.Assignment.id)
        .filter(
            models.Assignment.teacher_id == user.id,
            models.Assignment.is_active  == True,
        )
    )

    if class_id is not None:
        q = q.filter(models.Assignment.class_id == class_id)

    rows = (
        q.group_by(models.Assignment.id)
         .order_by(models.Assignment.created_at.desc())
         .all()
    )

    assignments = []
    for a, count in rows:
        assignments.append({
            "id":                 a.id,
            "class_id":           a.class_id,
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
    class_id:           int
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

    # Verify the teacher is assigned to this class
    if not teacher_owns_class(db, user.id, body.class_id):
        raise HTTPException(status_code=404, detail="Class not found or access denied.")

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
        class_id           = body.class_id,
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

     # ── NEW: also create in Google Classroom if class is linked ──────────────
    try:
        from routes.google_classroom import create_gc_assignment
        gc_id = create_gc_assignment(user.id, body.class_id, assignment, db)
        if gc_id:
            assignment.gc_coursework_id = gc_id
            db.commit()
            print(f"✅ Assignment also created in Google Classroom: {gc_id}")
    except Exception as e:
        print(f"⚠️ Google Classroom sync skipped: {e}")

    return {"success": True, "message": "Assignment created", "id": assignment.id}

    return {"success": True, "message": "Assignment created", "id": assignment.id}


# ── POST /api/teacher/assignments/{assignment_id}/upload-reference ────────────

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

    content = await file.read()
    text    = extract_text_from_file(content, file.filename)

    if not text or text.startswith("[Could not"):
        raise HTTPException(status_code=422, detail="Could not extract text from file")

    existing = assignment.reference_material or ""
    if existing:
        assignment.reference_material = existing + "\n\n--- Uploaded: " + file.filename + " ---\n" + text[:5000]
    else:
        assignment.reference_material = f"--- {file.filename} ---\n" + text[:5000]

    db.commit()

    return {
        "success":         True,
        "message":         f"Reference material from '{file.filename}' added successfully",
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


# ═══════════════════════════════════════════════════════════════════════════════
#  SUBMISSIONS  (filtered by class_id)
# ═══════════════════════════════════════════════════════════════════════════════

# ── GET /api/teacher/submissions?class_id=<id> ───────────────────────────────

@router.get("/submissions")
def get_submissions(
    class_id: Optional[int] = Query(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    q = (
        db.query(models.Submission, models.User, models.Assignment)
        .join(models.User,       models.User.id       == models.Submission.student_id)
        .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
        .filter(models.Assignment.teacher_id == user.id)
    )

    if class_id is not None:
        q = q.filter(models.Assignment.class_id == class_id)

    rows = q.order_by(models.Submission.submitted_at.desc()).all()

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
            "class_id":            a.class_id,
        })

    return {"success": True, "submissions": submissions}


# ── GET /api/teacher/submissions/pending?class_id=<id> ───────────────────────

@router.get("/submissions/pending")
def get_pending_grading(
    class_id: Optional[int] = Query(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    q = (
        db.query(models.Submission, models.User, models.Assignment)
        .join(models.User,       models.User.id       == models.Submission.student_id)
        .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
        .filter(
            models.Assignment.teacher_id == user.id,
            models.Submission.status.in_(["submitted", "ai_graded"]),
            models.Submission.final_score == None,
        )
    )

    if class_id is not None:
        q = q.filter(models.Assignment.class_id == class_id)

    rows = q.order_by(models.Submission.submitted_at.asc()).all()

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
            "class_id":           a.class_id,
        })

    return {"success": True, "submissions": submissions}


# ── POST /api/teacher/submissions/grade ──────────────────────────────────────

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

    submission.final_score      = body.score
    submission.teacher_feedback = body.feedback.strip() if body.feedback else None
    submission.status           = "graded"
    submission.graded_at        = datetime.utcnow()

    db.commit()

    print(f"✅ Teacher approved grade: {body.score}/{assignment.max_score} for submission {body.submission_id}")

    return {"success": True, "message": "Grade approved and released to student"}