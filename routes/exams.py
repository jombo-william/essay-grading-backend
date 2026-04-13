# routes/exams.py
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List
from auth_utils import require_teacher, validate_csrf
import models

router = APIRouter()

BLANTYRE = ZoneInfo("Africa/Blantyre")


def fmt_date(dt):
    if not dt: return None
    if hasattr(dt, 'year') and dt.year < 2000: return None
    if isinstance(dt, str): return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def teacher_owns_class(db: Session, teacher_id: int, class_id: int) -> bool:
    return db.query(models.TeacherClass).filter_by(
        teacher_id=teacher_id,
        class_id=class_id,
    ).first() is not None


def parse_due_date(due_date_str: str):
    due_date_str = due_date_str.strip().replace("T", " ")
    if len(due_date_str) == 16:
        due_date_str += ":00"
    naive_dt = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M:%S")
    return naive_dt.replace(tzinfo=BLANTYRE)


# ═══════════════════════════════════════════════════════════════════════════════
#  PYDANTIC SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class QuestionSchema(BaseModel):
    type:            str              # "mcq" | "structured"
    prompt:          str
    marks:           int = 1
    # MCQ only
    options:         Optional[List[str]] = None
    correct_option:  Optional[str]       = None   # "A" | "B" | "C" | "D"
    # Structured only
    marking_guide:   Optional[str]       = None


class CreateExamRequest(BaseModel):
    class_id:     int
    title:        str
    description:  Optional[str] = ""
    instructions: str
    due_date:     str
    time_limit:   Optional[int] = 60   # minutes
    questions:    List[QuestionSchema]
    csrf_token:   Optional[str] = None


class UpdateExamRequest(BaseModel):
    id:           int
    title:        str
    description:  Optional[str] = ""
    instructions: str
    due_date:     str
    time_limit:   Optional[int] = 60
    questions:    List[QuestionSchema]
    csrf_token:   Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/teacher/exams?class_id=<id>
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/exams")
def get_exams(
    class_id: Optional[int] = Query(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    q = (
        db.query(
            models.Exam,
            func.count(models.ExamSubmission.id).label("submission_count"),
        )
        .outerjoin(models.ExamSubmission, models.ExamSubmission.exam_id == models.Exam.id)
        .filter(
            models.Exam.teacher_id == user.id,
            models.Exam.is_active  == True,
        )
    )

    if class_id is not None:
        q = q.filter(models.Exam.class_id == class_id)

    rows = (
        q.group_by(models.Exam.id)
         .order_by(models.Exam.created_at.desc())
         .all()
    )

    exams = []
    for exam, sub_count in rows:
        # Load questions for this exam
        questions = (
            db.query(models.ExamQuestion)
            .filter(models.ExamQuestion.exam_id == exam.id)
            .order_by(models.ExamQuestion.order_index)
            .all()
        )
        exams.append({
            "id":               exam.id,
            "class_id":         exam.class_id,
            "title":            exam.title,
            "description":      exam.description,
            "instructions":     exam.instructions,
            "due_date":         fmt_date(exam.due_date),
            "time_limit":       exam.time_limit,
            "is_active":        exam.is_active,
            "created_at":       fmt_date(exam.created_at),
            "submission_count": sub_count,
            "questions": [
                {
                    "id":             q.id,
                    "type":           q.type,
                    "prompt":         q.prompt,
                    "marks":          q.marks,
                    "options":        json.loads(q.options) if q.options else None,
                    "correct_option": q.correct_option,
                    "marking_guide":  q.marking_guide,
                    "order_index":    q.order_index,
                }
                for q in questions
            ],
        })

    return {"success": True, "exams": exams}


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/teacher/exams/create
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/exams/create")
def create_exam(
    body: CreateExamRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    if not teacher_owns_class(db, user.id, body.class_id):
        raise HTTPException(status_code=404, detail="Class not found or access denied.")

    if not body.title.strip() or not body.instructions.strip() or not body.due_date:
        raise HTTPException(status_code=422, detail="Title, instructions, and due date are required.")

    if not body.questions:
        raise HTTPException(status_code=422, detail="At least one question is required.")

    try:
        due_date = parse_due_date(body.due_date)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid due date format.")

    exam = models.Exam(
        teacher_id    = user.id,
        class_id      = body.class_id,
        title         = body.title.strip(),
        description   = body.description.strip() if body.description else None,
        instructions  = body.instructions.strip(),
        due_date      = due_date,
        time_limit    = body.time_limit or 60,
    )
    db.add(exam)
    db.flush()   # get exam.id before adding questions

    for idx, q in enumerate(body.questions):
        question = models.ExamQuestion(
            exam_id        = exam.id,
            type           = q.type,
            prompt         = q.prompt.strip(),
            marks          = q.marks,
            options        = json.dumps(q.options) if q.options else None,
            correct_option = q.correct_option if q.type == "mcq" else None,
            marking_guide  = q.marking_guide.strip() if q.marking_guide else None,
            order_index    = idx,
        )
        db.add(question)

    db.commit()
    db.refresh(exam)

    return {"success": True, "message": "Exam created.", "id": exam.id}


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/teacher/exams/update
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/exams/update")
def update_exam(
    body: UpdateExamRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_teacher),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    exam = db.query(models.Exam).filter(
        models.Exam.id         == body.id,
        models.Exam.teacher_id == user.id,
    ).first()

    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found or access denied.")

    if not body.title.strip() or not body.instructions.strip() or not body.due_date:
        raise HTTPException(status_code=422, detail="Title, instructions, and due date are required.")

    try:
        due_date = parse_due_date(body.due_date)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid due date format.")

    exam.title        = body.title.strip()
    exam.description  = body.description.strip() if body.description else None
    exam.instructions = body.instructions.strip()
    exam.due_date     = due_date
    exam.time_limit   = body.time_limit or 60

    # Replace all questions — delete old, insert new
    db.query(models.ExamQuestion).filter(
        models.ExamQuestion.exam_id == exam.id
    ).delete()

    for idx, q in enumerate(body.questions):
        question = models.ExamQuestion(
            exam_id        = exam.id,
            type           = q.type,
            prompt         = q.prompt.strip(),
            marks          = q.marks,
            options        = json.dumps(q.options) if q.options else None,
            correct_option = q.correct_option if q.type == "mcq" else None,
            marking_guide  = q.marking_guide.strip() if q.marking_guide else None,
            order_index    = idx,
        )
        db.add(question)

    db.commit()

    return {"success": True, "message": "Exam updated."}