"""
assignment_routes.py
====================
STUDENT-FACING READ ENDPOINTS LIVE HERE.

To change what assignments students see  → edit get_assignments()
To change what results students see      → edit get_results()
To change the date format                → edit fmt_date()
"""

import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from auth_utils import require_student
import models

router = APIRouter()


# ── Date formatter ────────────────────────────────────────────────────────────

def fmt_date(dt):
    if not dt:
        return None
    if isinstance(dt, str):
        return dt
    if hasattr(dt, 'year') and dt.year < 2000:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── GET /api/student/assignments ──────────────────────────────────────────────

@router.get("/assignments")
def get_assignments(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    rows = (
        db.query(models.Assignment, models.Submission)
        .outerjoin(
            models.Submission,
            (models.Submission.assignment_id == models.Assignment.id) &
            (models.Submission.student_id    == user.id)
        )
        .filter(models.Assignment.is_active == True)
        .order_by(models.Assignment.due_date.asc())
        .all()
    )

    assignments = []
    for a, s in rows:
        assignments.append({
            "id":                 a.id,
            "title":              a.title,
            "description":        a.description,
            "instructions":       a.instructions,
            "reference_material": a.reference_material,
            "max_score":          a.max_score,
            "due_date":           fmt_date(a.due_date),
            #"rubric":             json.loads(a.rubric) if a.rubric else None,
            "rubric": a.rubric if isinstance(a.rubric, dict) else json.loads(a.rubric) if a.rubric else None,
            "submitted":          s is not None,
            "submission": {
                "id":               s.id,
                "assignment_id":    s.assignment_id,
                "essay_text":       s.essay_text,
                "status":           s.status,
                "ai_score":         s.ai_score if s.final_score is not None else None,
                "ai_feedback":      s.ai_feedback if s.final_score is not None else None,
                "final_score":      s.final_score,
                "teacher_feedback": s.teacher_feedback,
                "submitted_at":     fmt_date(s.submitted_at),
                "graded_at":        fmt_date(s.graded_at),
            } if s else None,
        })

    return {"success": True, "assignments": assignments}


# ── GET /api/student/results ──────────────────────────────────────────────────

@router.get("/results")
def get_results(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    rows = (
        db.query(models.Submission, models.Assignment)
        .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
        .filter(models.Submission.student_id == user.id)
        .order_by(models.Submission.submitted_at.desc())
        .all()
    )

    results = []
    for s, a in rows:
        teacher_approved = s.final_score is not None
        results.append({
            "id":                 s.id,
            "essay_text":         s.essay_text,
            "ai_score":           s.ai_score if teacher_approved else None,
            "ai_feedback":        s.ai_feedback if teacher_approved else None,
            "ai_detection_score": s.ai_detection_score,
            "final_score":        s.final_score,
            "teacher_feedback":   s.teacher_feedback,
            "status":             s.status,
            "submitted_at":       fmt_date(s.submitted_at),
            "graded_at":          fmt_date(s.graded_at),
            "assignment_title":   a.title,
            "max_score":          a.max_score,
            "due_date":           fmt_date(a.due_date),
            "assignment_id":      s.assignment_id,
        })

    return {"success": True, "results": results}