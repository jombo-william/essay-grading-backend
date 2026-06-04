"""
submission_routes.py
====================
SUBMIT + UNSUBMIT ENDPOINTS LIVE HERE.

To change submit behaviour    → edit submit_essay()
To change unsubmit rules      → edit unsubmit_essay()
To change min word count      → change the 50 in submit_essay()
To change how AI score is applied → edit the grading result section
"""

import logging
import re
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import get_db
from auth_utils import require_student, validate_csrf
from routes.ai_grader import grade_with_ai
from routes.grading_prompt import build_grading_prompt
import models

logger = logging.getLogger("essay_backend.submission")
router = APIRouter()


class SubmitEssayRequest(BaseModel):
    assignment_id: int
    essay_text:    str
    csrf_token:    Optional[str] = None


class UnsubmitRequest(BaseModel):
    submission_id: int
    csrf_token:    Optional[str] = None


# ── Background grading + platform sync ────────────────────────────────────────

def _strip_markdown(text: str) -> str:
    import re
    if not text:
        return ""
    result = text
    result = re.sub(r"^#{1,6}\s+", "", result, flags=re.MULTILINE)
    result = re.sub(r"\*\*(.+?)\*\*", r"\1", result)
    result = re.sub(r"\*(.+?)\*", r"\1", result)
    result = re.sub(r"^[ \t]*[-*]\s+", "• ", result, flags=re.MULTILINE)
    result = re.sub(r"`{1,3}(.*?)`{1,3}", r"\1", result)
    result = re.sub(r"^---+\s*$", "", result, flags=re.MULTILINE)
    result = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _grade_submission_background(
    submission_id: int,
    assignment_id: int,
    student_id:    int,
    essay_text:    str,
):
    logger.info(
        "Background grading started: submission_id=%s assignment_id=%s student_id=%s",
        submission_id,
        assignment_id,
        student_id,
    )
    from database import SessionLocal
    from routes.ai_grader import grade_with_ai
    from routes.grading_prompt import build_grading_prompt

    db = SessionLocal()
    try:
        submission = db.query(models.Submission).filter(
            models.Submission.id == submission_id
        ).first()
        assignment = db.query(models.Assignment).filter(
            models.Assignment.id == assignment_id
        ).first()

        if not submission or not assignment:
                    logger.warning(
                        "Background grading skipped: submission or assignment not found (submission_id=%s)",
                        submission_id,
                    )
                    return

        word_count = len(re.findall(r'\w+', essay_text))
        ai_score = ai_feedback = ai_detection_score = None

        try:
            prompt = build_grading_prompt(assignment, essay_text, word_count)
            parsed = grade_with_ai(
                prompt=prompt,
                assignment=assignment,
                essay_text=essay_text,
                word_count=word_count,
            )

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
                    logger.info(
                        "Off-topic grading result: submission_id=%s score=%s graded_by=%s",
                        submission_id,
                        ai_score,
                        graded_by,
                    )

                elif ai_detected:
                    ai_detection_score = 75
                    ai_score           = raw_score
                    ai_feedback        = (
                        f"⚠️ Possible AI-generated content — flagged for teacher review.\n\n"
                        f"{str(parsed['feedback']).strip()}"
                    )
                    logger.info(
                        "AI-detected grading result: submission_id=%s score=%s graded_by=%s",
                        submission_id,
                        ai_score,
                        graded_by,
                    )

                elif low_confidence:
                    ai_detection_score = 10
                    ai_score           = raw_score
                    ai_feedback        = str(parsed["feedback"]).strip()
                    logger.info(
                        "Low-confidence grading result: submission_id=%s score=%s graded_by=%s",
                        submission_id,
                        ai_score,
                        graded_by,
                    )

                else:
                    ai_detection_score = 10
                    ai_score           = raw_score
                    ai_feedback        = str(parsed["feedback"]).strip()
                    logger.info(
                        "Successful grading result: submission_id=%s score=%s graded_by=%s",
                        submission_id,
                        ai_score,
                        graded_by,
                    )

        except Exception as e:
            logger.exception(
                "All grading methods failed for submission_id=%s",
                submission_id,
            )
        submission.ai_score           = ai_score
        submission.ai_feedback        = _strip_markdown(ai_feedback) if ai_feedback else None
        submission.ai_detection_score = ai_detection_score
        submission.status             = "ai_graded" if ai_score is not None else "submitted"
        if ai_score is not None:
            submission.ai_graded_at = datetime.now(timezone.utc)
        db.commit()

    except Exception as e:
        logger.exception("Background grading task crashed for submission_id=%s", submission_id)
        db.rollback()
    finally:
        db.close()


def _sync_platforms_background(
    submission_id: int,
    assignment_id: int,
    student_id:    int,
    essay_text:    str,
):
    from database import SessionLocal
    from routes.google_classroom import get_gc_course_id_for_class
    from routes.student_classroom import get_student_credentials
    from routes.student_moodle import moodle_call
    from googleapiclient.discovery import build
    import io

    db = SessionLocal()
    try:
        submission = db.query(models.Submission).filter(
            models.Submission.id == submission_id
        ).first()
        assignment = db.query(models.Assignment).filter(
            models.Assignment.id == assignment_id
        ).first()
        user = db.query(models.User).filter(models.User.id == student_id).first()

        if not submission or not assignment or not user:
            print(f"⚠️ Background platform sync skipped for submission {submission_id}")
            return

        # ── Google Classroom ─────────────────────────────────────────────────────
        try:
            if assignment.gc_coursework_id and assignment.class_id:
                gc_course_id = get_gc_course_id_for_class(assignment.class_id, db)
                if gc_course_id:
                    student_creds = get_student_credentials(user.id, db)
                    classroom_svc = build("classroom", "v1", credentials=student_creds)
                    drive_svc     = build("drive",     "v3", credentials=student_creds)

                    file_metadata = {
                        "name":     f"{user.name} - {assignment.title}.txt",
                        "mimeType": "text/plain",
                    }
                    media = googleapiclient.http.MediaIoBaseUpload(
                        io.BytesIO(essay_text.encode("utf-8")),
                        mimetype="text/plain",
                        resumable=False,
                    )
                    uploaded = drive_svc.files().create(
                        body=file_metadata,
                        media_body=media,
                        fields="id",
                    ).execute()
                    file_id = uploaded.get("id")

                    student_subs = classroom_svc.courses().courseWork().studentSubmissions().list(
                        courseId     = gc_course_id,
                        courseWorkId = assignment.gc_coursework_id,
                        userId       = "me",
                    ).execute()

                    subs = student_subs.get("studentSubmissions", [])
                    if subs and file_id:
                        sub_id = subs[0]["id"]
                        classroom_svc.courses().courseWork().studentSubmissions().modifyAttachments(
                            courseId     = gc_course_id,
                            courseWorkId = assignment.gc_coursework_id,
                            id           = sub_id,
                            body={"addAttachments": [{"driveFile": {"id": file_id}}]},
                        ).execute()
                        classroom_svc.courses().courseWork().studentSubmissions().turnIn(
                            courseId     = gc_course_id,
                            courseWorkId = assignment.gc_coursework_id,
                            id           = sub_id,
                        ).execute()
                        print(f"✅ Essay pushed to Google Classroom for student {user.id}")

        except Exception as e:
            print(f"⚠️ Could not push to Google Classroom: {e}")

        # ── Moodle ──────────────────────────────────────────────────────────────
        try:
            if assignment.moodle_assignment_id and assignment.moodle_course_id:
                moodle_record = db.query(models.StudentMoodleToken).filter(
                    models.StudentMoodleToken.student_id == user.id
                ).first()

                if moodle_record:
                    moodle_call(
                        moodle_record.site_url,
                        moodle_record.token,
                        "mod_assign_save_submission",
                        {
                            "assignmentid": assignment.moodle_assignment_id,
                            "plugindata[onlinetext_editor][text]":   essay_text,
                            "plugindata[onlinetext_editor][format]": 1,
                            "plugindata[onlinetext_editor][itemid]": 0,
                        }
                    )

                    submission.moodle_assignment_id = assignment.moodle_assignment_id
                    submission.moodle_course_id     = assignment.moodle_course_id
                    db.commit()
                    print(f"✅ Essay pushed to Moodle for student {user.id}")

        except Exception as e:
            print(f"⚠️ Could not push to Moodle: {e}")

    except Exception as e:
        print(f"⚠️ Background platform sync crashed: {e}")
        db.rollback()
    finally:
        db.close()


# ── POST /api/student/submit ──────────────────────────────────────────────────

@router.post("/submit")
def submit_essay(
    background_tasks: BackgroundTasks,
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

    submission = models.Submission(
        assignment_id = assignment_id,
        student_id    = user.id,
        essay_text    = essay_text,
        status        = "submitted",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    logger.info(
        "Received submission request: student_id=%s assignment_id=%s word_count=%s submission_id=%s",
        user.id,
        assignment_id,
        word_count,
        submission.id,
    )
    background_tasks.add_task(
        _grade_submission_background,
        submission_id = submission.id,
        assignment_id = assignment.id,
        student_id    = user.id,
        essay_text    = essay_text,
    )
    background_tasks.add_task(
        _sync_platforms_background,
        submission_id = submission.id,
        assignment_id = assignment.id,
        student_id    = user.id,
        essay_text    = essay_text,
    )

    return {
        "success": True,
        "message": "Essay submitted! Grading is processing in the background.",
        "submission": {
            "id":           submission.id,
            "status":       "submitted",
            "submitted_at": submission.submitted_at.isoformat(),
            "ai_score":     None,
        },
        "grading_status": "in_progress",
    }


# ── GET /api/student/submissions/{submission_id}/status ──────────────────────

class SubmissionStatusResponse(BaseModel):
    id:           int
    status:       str
    submitted_at: str
    ai_score:     Optional[int]
    ai_feedback:  Optional[str]
    ai_detection_score: Optional[int]
    ai_graded_at: Optional[str]
    grading_status: str


@router.get("/submissions/{submission_id}/status", response_model=dict)
def get_submission_status(
    submission_id: int,
    ctx: dict = Depends(require_student),
):
    user: models.User           = ctx["user"]
    db: Session                 = ctx["db"]

    submission = db.query(models.Submission).filter(
        models.Submission.id         == submission_id,
        models.Submission.student_id == user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    grading_status = "completed"
    if submission.status == "submitted" and submission.ai_score is None:
        grading_status = "in_progress"
    elif submission.status == "ai_graded":
        grading_status = "completed"

    return {
        "success": True,
        "submission": {
            "id":                  submission.id,
            "status":              submission.status,
            "submitted_at":        submission.submitted_at.isoformat(),
            "ai_score":            submission.ai_score,
            "ai_feedback":         submission.ai_feedback,
            "ai_detection_score":  submission.ai_detection_score,
            "ai_graded_at":        submission.ai_graded_at.isoformat() if submission.ai_graded_at else None,
            "grading_status":      grading_status,
        },
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

    # ── Unsubmit from Google Classroom if linked ──────────────────────────────
    try:
        if assignment.gc_coursework_id and assignment.class_id:
            from routes.google_classroom import get_gc_course_id_for_class
            from routes.student_classroom import get_student_credentials
            from googleapiclient.discovery import build

            gc_course_id = get_gc_course_id_for_class(assignment.class_id, db)
            if gc_course_id:
                student_creds = get_student_credentials(user.id, db)
                classroom_svc = build("classroom", "v1", credentials=student_creds)

                student_subs = classroom_svc.courses().courseWork().studentSubmissions().list(
                    courseId     = gc_course_id,
                    courseWorkId = assignment.gc_coursework_id,
                    userId       = "me",
                ).execute()

                subs = student_subs.get("studentSubmissions", [])
                if subs:
                    sub_id = subs[0]["id"]
                    classroom_svc.courses().courseWork().studentSubmissions().reclaim(
                        courseId     = gc_course_id,
                        courseWorkId = assignment.gc_coursework_id,
                        id           = sub_id,
                    ).execute()
                    print(f"✅ Unsubmitted from Google Classroom for student {user.id}")

    except Exception as e:
        print(f"⚠️ Could not unsubmit from Google Classroom: {e} — local unsubmit still proceeding")

    db.delete(submission)
    db.commit()

    return {
        "success": True,
        "message": "Essay unsubmitted successfully. You can now rewrite and resubmit before the deadline.",
    }