"""
exam_submit.py
==============
EXAM SUBMISSION ENDPOINT LIVES HERE.

  POST /api/student/exams/submit

To change submit rules        → edit submit_exam()
To change MCQ grading         → edit the mcq block inside submit_exam()
To change structured grading  → edit routes/exam_grader.py → grade_structured_answer()
To switch AI model            → edit routes/exam_grader.py → grade_structured_answer()
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from auth_utils import require_student, validate_csrf
from routes.exam_grader import grade_structured_answer
import models

router = APIRouter()

BLANTYRE = ZoneInfo("Africa/Blantyre")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AnswerSchema(BaseModel):
    question_id:     int
    selected_option: Optional[str] = None   # MCQ only
    answer_text:     Optional[str] = None   # structured only


class SubmitExamRequest(BaseModel):
    exam_id:    int
    answers:    List[AnswerSchema]
    csrf_token: Optional[str] = None


def _fmt_submission(sub: models.ExamSubmission) -> dict:
    if not sub: return None
    return {
        "id":           sub.id,
        "status":       sub.status,
        "total_score":  sub.total_score,
        "submitted_at": sub.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if sub.submitted_at else None,
        "graded_at":    sub.graded_at.strftime("%Y-%m-%d %H:%M:%S")    if sub.graded_at    else None,
    }


# ── POST /api/student/exams/submit ────────────────────────────────────────────

@router.post("/exams/submit")
def submit_exam(
    body: SubmitExamRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_student),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    # ── Validate exam ──────────────────────────────────────────────────────────
    exam = db.query(models.Exam).filter_by(id=body.exam_id, is_active=True).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found.")

    if not db.query(models.ClassEnrollment).filter_by(
        class_id=exam.class_id, student_id=user.id
    ).first():
        raise HTTPException(status_code=403, detail="You are not enrolled in this class.")

    # ── Deadline check ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    due = exam.due_date
    if due.tzinfo is None:
        due = due.replace(tzinfo=BLANTYRE)
    if now > due:
        raise HTTPException(status_code=422, detail="This exam is past its due date.")

    # ── Duplicate check ────────────────────────────────────────────────────────
    if db.query(models.ExamSubmission).filter_by(
        exam_id=exam.id, student_id=user.id
    ).first():
        raise HTTPException(status_code=409, detail="You have already submitted this exam.")

    # ── Load questions ─────────────────────────────────────────────────────────
    questions  = db.query(models.ExamQuestion).filter_by(exam_id=exam.id).all()
    answer_map = {a.question_id: a for a in body.answers}

    # ── Create submission row (flush for ID) ───────────────────────────────────
    exam_submission = models.ExamSubmission(
        exam_id    = exam.id,
        student_id = user.id,
        status     = "submitted",
    )
    db.add(exam_submission)
    db.flush()

    # ── Grade each question ────────────────────────────────────────────────────
    total_score = 0
    has_pending = False

    for question in questions:
        student_ans = answer_map.get(question.id)

        # ── MCQ: instant correct/wrong ────────────────────────────────────────
        if question.type == "mcq":
            selected      = student_ans.selected_option if student_ans else None
            is_correct    = bool(selected and selected == question.correct_option)
            score_awarded = question.marks if is_correct else 0
            total_score  += score_awarded

            db.add(models.ExamAnswer(
                submission_id   = exam_submission.id,
                question_id     = question.id,
                selected_option = selected,
                is_correct      = is_correct,
                score_awarded   = score_awarded,
            ))
            print(f"✅ MCQ Q{question.id} → {'correct' if is_correct else 'wrong'} | {score_awarded}/{question.marks}")

        # ── Structured: AI graded ─────────────────────────────────────────────
        elif question.type == "structured":
            answer_text = (
                student_ans.answer_text.strip()
                if student_ans and student_ans.answer_text
                else ""
            )

            ai_score    = None
            ai_feedback = None

            try:
                graded      = grade_structured_answer(question, answer_text)
                ai_score    = graded["score"]
                ai_feedback = graded["feedback"]
                total_score += ai_score
                print(f"✅ Structured Q{question.id} graded → {ai_score}/{question.marks}")
            except Exception as e:
                print(f"❌ AI grading failed Q{question.id}: {e} — pending teacher review")
                has_pending = True

            db.add(models.ExamAnswer(
                submission_id = exam_submission.id,
                question_id   = question.id,
                answer_text   = answer_text,
                score_awarded = ai_score,
                ai_feedback   = ai_feedback,
            ))

    # ── Finalise submission ────────────────────────────────────────────────────
    if has_pending:
        exam_submission.total_score = None
        exam_submission.status      = "submitted"
    else:
        exam_submission.total_score = total_score
        exam_submission.status      = "graded"
        exam_submission.graded_at   = datetime.now(timezone.utc)

    db.commit()
    db.refresh(exam_submission)

    return {
        "success": True,
        "message": (
            "Exam submitted and graded!"
            if not has_pending
            else "Exam submitted. Some answers are pending teacher review."
        ),
        "submission": _fmt_submission(exam_submission),
    }