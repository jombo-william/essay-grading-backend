"""
submission_routes.py
====================
SUBMIT + UNSUBMIT ENDPOINTS LIVE HERE.

To change submit behaviour    → edit submit_essay()
To change unsubmit rules      → edit unsubmit_essay()
To change min word count      → change the 50 in submit_essay()
To change how AI score is applied → edit the grading result section
"""

import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import get_db
from auth_utils import require_student, validate_csrf
from routes.ai_grader import grade_with_ai
from routes.grading_prompt import build_grading_prompt
import models

router = APIRouter()


class SubmitEssayRequest(BaseModel):
    assignment_id: int
    essay_text:    str
    csrf_token:    Optional[str] = None


class UnsubmitRequest(BaseModel):
    submission_id: int
    csrf_token:    Optional[str] = None


# ── POST /api/student/submit ──────────────────────────────────────────────────

@router.post("/submit")
def submit_essay(
    body: SubmitEssayRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_student),
):
    user: models.User           = ctx["user"]
    session: models.UserSession = ctx["session"]
    db: Session                 = ctx["db"]

    validate_csrf(session, x_csrf_token, body.csrf_token)

    assignment_id = body.assignment_id
    essay_text    = body.essay_text.strip()

    if not assignment_id or not essay_text:
        raise HTTPException(status_code=422, detail="assignment_id and essay_text are required")

    word_count = len(re.findall(r'\w+', essay_text))
    if word_count < 50:
        raise HTTPException(status_code=422, detail="Essay must be at least 50 words")

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id        == assignment_id,
        models.Assignment.is_active == True,
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    now = datetime.now(timezone.utc)
    due = assignment.due_date
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if now > due:
        raise HTTPException(status_code=422, detail="This assignment is past its due date")

    existing = db.query(models.Submission).filter(
        models.Submission.assignment_id == assignment_id,
        models.Submission.student_id    == user.id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="You have already submitted this assignment")

    # Save submission first so it's never lost even if AI fails
    submission = models.Submission(
        assignment_id=assignment_id,
        student_id=user.id,
        essay_text=essay_text,
        status="submitted",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    # ── Run AI grading ────────────────────────────────────────────────────────
    ai_score = ai_feedback = ai_detection_score = None

    try:
        prompt = build_grading_prompt(assignment, essay_text, word_count)
        parsed = grade_with_ai(prompt, assignment=assignment, essay_text=essay_text, word_count=word_count)

        if "score" in parsed and "feedback" in parsed:
            off_topic      = parsed.get("off_topic",      False)
            ai_detected    = parsed.get("ai_detected",    False)
            low_confidence = parsed.get("low_confidence", False)
            graded_by      = parsed.get("graded_by",      "unknown")
            raw_score      = max(0, min(assignment.max_score, int(parsed["score"])))

            if off_topic:
                cap_score          = round(assignment.max_score * 0.05)
                ai_score           = min(raw_score, cap_score)
                ai_detection_score = 10
                ai_feedback        = (
                    f"❌ OFF-TOPIC SUBMISSION\n\n"
                    f"The assignment asked: \"{assignment.title}\"\n"
                    f"Your essay does not address this topic.\n\n"
                    f"Score capped at {ai_score}/{assignment.max_score}.\n"
                    f"Please resubmit an essay that directly answers the assignment question."
                )
                print(f"❌ Off-topic — capped at {ai_score}/{assignment.max_score} [{graded_by}]")

            elif ai_detected:
                ai_detection_score = 75
                ai_score           = raw_score
                ai_feedback        = (
                    f"⚠️ Possible AI-generated content — flagged for teacher review.\n\n"
                    f"{str(parsed['feedback']).strip()}"
                )
                print(f"⚠️ AI content flagged — {ai_score}/{assignment.max_score} [{graded_by}]")

            elif low_confidence:
                ai_detection_score = 10
                ai_score           = raw_score
                ai_feedback        = str(parsed["feedback"]).strip()
                print(f"📉 Low confidence — {ai_score}/{assignment.max_score} [{graded_by}]")

            else:
                ai_detection_score = 10
                ai_score           = raw_score
                ai_feedback        = str(parsed["feedback"]).strip()
                print(f"✅ Graded: {ai_score}/{assignment.max_score} [{graded_by}]")

    except Exception as e:
        print(f"❌ All grading methods failed: {e}")

    # ── Save AI result ────────────────────────────────────────────────────────
    submission.ai_score           = ai_score
    submission.ai_feedback        = ai_feedback
    submission.ai_detection_score = ai_detection_score
    submission.status             = "ai_graded" if ai_score is not None else "submitted"
    if ai_score is not None:
        submission.ai_graded_at = datetime.now(timezone.utc)
    db.commit()

# ── Push submission to Google Classroom if assignment is linked ───────────
    try:
        if assignment.gc_coursework_id and assignment.class_id:
            from routes.google_classroom import get_gc_course_id_for_class
            from routes.student_classroom import get_student_credentials
            from googleapiclient.discovery import build

            gc_course_id = get_gc_course_id_for_class(assignment.class_id, db)
            if gc_course_id:
                student_creds = get_student_credentials(user.id, db)
                classroom_svc = build("classroom", "v1", credentials=student_creds)

                # Mark the student's submission as TURNED_IN
                student_subs = classroom_svc.courses().courseWork().studentSubmissions().list(
                    courseId     = gc_course_id,
                    courseWorkId = assignment.gc_coursework_id,
                    userId       = "me",
                ).execute()

                subs = student_subs.get("studentSubmissions", [])
                if subs:
                    sub_id = subs[0]["id"]
                    classroom_svc.courses().courseWork().studentSubmissions().turnIn(
                        courseId          = gc_course_id,
                        courseWorkId      = assignment.gc_coursework_id,
                        id                = sub_id,
                    ).execute()
                    print(f"✅ Submission pushed to Google Classroom for student {user.id}")
    except Exception as e:
        print(f"⚠️ Could not push to Google Classroom: {e} — local submission still saved")
    return {
        "success": True,
        "message": "Essay submitted. Results available after teacher review.",
        "submission": {"id": submission.id, "status": submission.status},
    }


# ── POST /api/student/unsubmit ────────────────────────────────────────────────

@router.post("/unsubmit")
def unsubmit_essay(
    body: UnsubmitRequest,
    x_csrf_token: Optional[str] = Header(default=None),
    ctx: dict = Depends(require_student),
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
            models.Submission.student_id == user.id,
        )
        .first()
    )
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.final_score is not None:
        raise HTTPException(status_code=422, detail="This submission has already been graded and cannot be unsubmitted")

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id == submission.assignment_id
    ).first()

    now = datetime.now(timezone.utc)
    due = assignment.due_date
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if now > due:
        raise HTTPException(status_code=422, detail="The deadline has passed — this submission can no longer be unsubmitted")

    db.delete(submission)
    db.commit()

    return {
        "success": True,
        "message": "Essay unsubmitted successfully. You can now rewrite and resubmit before the deadline.",
    }