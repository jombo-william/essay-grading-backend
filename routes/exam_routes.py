"""
exam_routes.py
==============
READ-ONLY EXAM ENDPOINTS LIVE HERE.

  GET /api/student/exams         → list exams for enrolled classes
  GET /api/student/exams/results → student's own exam results
"""

import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from auth_utils import require_student
import models

router = APIRouter()


def fmt_date(dt):
    if not dt: return None
    if hasattr(dt, 'year') and dt.year < 2000: return None
    if isinstance(dt, str): return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_submission(sub: models.ExamSubmission) -> dict:
    if not sub: return None
    return {
        "id":           sub.id,
        "status":       sub.status,
        "total_score":  sub.total_score,
        "submitted_at": fmt_date(sub.submitted_at),
        "graded_at":    fmt_date(sub.graded_at),
    }


# ── GET /api/student/exams ────────────────────────────────────────────────────

@router.get("/exams")
def get_student_exams(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    enrolled_class_ids = [
        e.class_id
        for e in db.query(models.ClassEnrollment)
                   .filter(models.ClassEnrollment.student_id == user.id)
                   .all()
    ]

    if not enrolled_class_ids:
        return {"success": True, "exams": []}

    exams = (
        db.query(models.Exam)
        .filter(
            models.Exam.class_id.in_(enrolled_class_ids),
            models.Exam.is_active == True,
        )
        .order_by(models.Exam.due_date.asc())
        .all()
    )

    result = []
    for exam in exams:
        questions = (
            db.query(models.ExamQuestion)
            .filter_by(exam_id=exam.id)
            .order_by(models.ExamQuestion.order_index)
            .all()
        )
        submission  = db.query(models.ExamSubmission).filter_by(
            exam_id=exam.id, student_id=user.id
        ).first()
        total_marks = sum(q.marks for q in questions)

        result.append({
            "id":           exam.id,
            "title":        exam.title,
            "description":  exam.description,
            "instructions": exam.instructions,
            "due_date":     fmt_date(exam.due_date),
            "time_limit":   exam.time_limit,
            "total_marks":  total_marks,
            "questions": [
                {
                    "id":      q.id,
                    "type":    q.type,
                    "prompt":  q.prompt,
                    "marks":   q.marks,
                    "options": json.loads(q.options) if q.options else None,
                    # correct_option and marking_guide intentionally hidden from student
                }
                for q in questions
            ],
            "my_submission": _fmt_submission(submission),
        })

    return {"success": True, "exams": result}


# ── GET /api/student/exams/results ────────────────────────────────────────────

@router.get("/exams/results")
def get_exam_results(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    submissions = (
        db.query(models.ExamSubmission)
        .filter_by(student_id=user.id)
        .order_by(models.ExamSubmission.submitted_at.desc())
        .all()
    )

    results = []
    for sub in submissions:
        exam = db.query(models.Exam).filter_by(id=sub.exam_id).first()
        if not exam:
            continue

        questions   = (
            db.query(models.ExamQuestion)
            .filter_by(exam_id=exam.id)
            .order_by(models.ExamQuestion.order_index)
            .all()
        )
        q_map       = {q.id: q for q in questions}
        total_marks = sum(q.marks for q in questions)
        answers     = db.query(models.ExamAnswer).filter_by(submission_id=sub.id).all()

        answer_details = []
        for ans in answers:
            q = q_map.get(ans.question_id)
            if not q:
                continue
            detail = {
                "question_id":   q.id,
                "type":          q.type,
                "prompt":        q.prompt,
                "marks":         q.marks,
                "score_awarded": ans.score_awarded,
                "ai_feedback":   ans.ai_feedback,
            }
            if q.type == "mcq":
                detail["selected_option"] = ans.selected_option
                detail["correct_option"]  = q.correct_option
                detail["is_correct"]      = ans.is_correct
                detail["options"]         = json.loads(q.options) if q.options else None
            else:
                detail["answer_text"] = ans.answer_text

            answer_details.append(detail)

        results.append({
            "submission_id": sub.id,
            "exam_id":       exam.id,
            "exam_title":    exam.title,
            "total_marks":   total_marks,
            "total_score":   sub.total_score,
            "status":        sub.status,
            "submitted_at":  fmt_date(sub.submitted_at),
            "graded_at":     fmt_date(sub.graded_at),
            "answers":       answer_details,
        })

    return {"success": True, "results": results}