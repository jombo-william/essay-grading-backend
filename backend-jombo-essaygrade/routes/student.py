# import json
# import os
# import re
# import requests as http_requests
# from datetime import datetime, timezone
# from zoneinfo import ZoneInfo
# from fastapi import APIRouter, Depends, HTTPException, Header
# from sqlalchemy.orm import Session
# from pydantic import BaseModel
# from typing import Optional
# from database import get_db
# from auth_utils import require_student, validate_csrf
# import models

# router = APIRouter()

# BLANTYRE = ZoneInfo("Africa/Blantyre")

# # ── Ollama AI grading ─────────────────────────────────────────────────────────
# OLLAMA_URL   = "http://localhost:11434/api/generate"
# OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")

# def call_ollama(prompt: str) -> str:
#     resp = http_requests.post(
#         OLLAMA_URL,
#         json={
#             "model": OLLAMA_MODEL,
#             "prompt": prompt,
#             "stream": False,
#             "options": {"temperature": 0.2}
#         },
#         timeout=300,
#     )
#     resp.raise_for_status()
#     return resp.json()["response"]


# def fmt_date(dt):
#     if not dt: return None
#     if isinstance(dt, str): return dt
#     if hasattr(dt, 'year') and dt.year < 2000: return None
#     return dt.strftime("%Y-%m-%d %H:%M:%S")


# # ── GET /api/student/assignments ─────────────────────────────────────────────

# @router.get("/assignments")
# def get_assignments(ctx: dict = Depends(require_student)):
#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     rows = (
#         db.query(models.Assignment, models.Submission)
#         .outerjoin(
#             models.Submission,
#             (models.Submission.assignment_id == models.Assignment.id) &
#             (models.Submission.student_id    == user.id)
#         )
#         .filter(models.Assignment.is_active == True)
#         .order_by(models.Assignment.due_date.asc())
#         .all()
#     )

#     assignments = []
#     for a, s in rows:
#         assignments.append({
#             "id":           a.id,
#             "title":        a.title,
#             "description":  a.description,
#             "instructions": a.instructions,
#             "max_score":    a.max_score,
#             "due_date":     fmt_date(a.due_date),
#             "rubric":       json.loads(a.rubric) if a.rubric else None,
#             "submitted":    s is not None,
#             "submission": {
#                 "id":               s.id,
#                 "assignment_id":    s.assignment_id,
#                 "essay_text":       s.essay_text,
#                 "status":           s.status,
#                 "ai_score":         s.ai_score,
#                 "ai_feedback":      s.ai_feedback,
#                 "final_score":      s.final_score,
#                 "teacher_feedback": s.teacher_feedback,
#                 "submitted_at":     fmt_date(s.submitted_at),
#                 "graded_at":        fmt_date(s.graded_at),
#             } if s else None,
#         })

#     return {"success": True, "assignments": assignments}


# # ── GET /api/student/results ──────────────────────────────────────────────────

# @router.get("/results")
# def get_results(ctx: dict = Depends(require_student)):
#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     rows = (
#         db.query(models.Submission, models.Assignment)
#         .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
#         .filter(models.Submission.student_id == user.id)
#         .order_by(models.Submission.submitted_at.desc())
#         .all()
#     )

#     results = []
#     for s, a in rows:
#         results.append({
#             "id":               s.id,
#             "essay_text":       s.essay_text,
#             "ai_score":         s.ai_score,
#             "ai_feedback":      s.ai_feedback,
#             "final_score":      s.final_score,
#             "teacher_feedback": s.teacher_feedback,
#             "status":           s.status,
#             "submitted_at":     fmt_date(s.submitted_at),
#             "graded_at":        fmt_date(s.graded_at),
#             "assignment_title": a.title,
#             "max_score":        a.max_score,
#             "due_date":         fmt_date(a.due_date),
#         })

#     return {"success": True, "results": results}


# # ── POST /api/student/submit ──────────────────────────────────────────────────

# class SubmitEssayRequest(BaseModel):
#     assignment_id: int
#     essay_text:    str
#     csrf_token:    Optional[str] = None


# @router.post("/submit")
# def submit_essay(
#     body: SubmitEssayRequest,
#     x_csrf_token: Optional[str] = Header(default=None),
#     ctx: dict = Depends(require_student),
# ):
#     user: models.User           = ctx["user"]
#     session: models.UserSession = ctx["session"]
#     db: Session                 = ctx["db"]

#     validate_csrf(session, x_csrf_token, body.csrf_token)

#     assignment_id = body.assignment_id
#     essay_text    = body.essay_text.strip()

#     if not assignment_id or not essay_text:
#         raise HTTPException(status_code=422, detail="assignment_id and essay_text are required")

#     word_count = len(re.findall(r'\w+', essay_text))
#     if word_count < 50:
#         raise HTTPException(status_code=422, detail="Essay must be at least 50 words")

#     assignment = db.query(models.Assignment).filter(
#         models.Assignment.id        == assignment_id,
#         models.Assignment.is_active == True,
#     ).first()

#     if not assignment:
#         raise HTTPException(status_code=404, detail="Assignment not found")

#     now = datetime.now(timezone.utc)
#     due = assignment.due_date
#     if due.tzinfo is None:
#         due = due.replace(tzinfo=timezone.utc)
#     if now > due:
#         raise HTTPException(status_code=422, detail="This assignment is past its due date")

#     existing = db.query(models.Submission).filter(
#         models.Submission.assignment_id == assignment_id,
#         models.Submission.student_id    == user.id,
#     ).first()

#     if existing:
#         raise HTTPException(status_code=409, detail="You have already submitted this assignment")

#     submission = models.Submission(
#         assignment_id = assignment_id,
#         student_id    = user.id,
#         essay_text    = essay_text,
#         status        = "submitted",
#     )
#     db.add(submission)
#     db.commit()
#     db.refresh(submission)

#     # ── AI GRADING via Ollama ────────────────────────────────────────────────
#     rubric = json.loads(assignment.rubric) if assignment.rubric else {
#         "content": 30, "structure": 25, "grammar": 20,
#         "vocabulary": 15, "argumentation": 10
#     }

#     rubric_description = "\n".join(
#         f"- {k.capitalize()}: {v}%" for k, v in rubric.items()
#     )

#     max_score = assignment.max_score

#     prompt = f"""You are a strict academic essay grader. Grade honestly and critically.

# Assignment: {assignment.title}
# Instructions: {assignment.instructions}
# Maximum Score: {max_score} points

# Grading Rubric:
# {rubric_description}

# STRICT GRADING RULES:
# - Score above 80 requires excellent structure, specific examples, and deep analysis
# - Score 60-79 means good effort but missing depth or specific examples
# - Score below 60 means poor structure, vague content, or off-topic
# - If essay is under 100 words, score must be below 40
# - If essay lacks specific examples or evidence, deduct at least 20 points
# - If essay is generic with no real analysis, score must be below 50

# AI DETECTION RULES:
# - Check if essay sounds AI-written: overly formal, no personal voice, perfect grammar, generic phrases
# - Phrases like "in conclusion", "it is important to note", "plays a crucial role" suggest AI writing
# - If you suspect AI writing, set ai_detected to true

# Student Essay ({word_count} words):
# ---
# {essay_text[:2000]}
# ---

# Respond ONLY with this exact JSON, no markdown, no extra text:
# {{"score": <integer 0-{max_score}>, "feedback": "<specific detailed feedback>", "ai_detected": <true or false>}}"""

#     ai_score           = None
#     ai_feedback        = None
#     ai_detection_score = None

#     try:
#         print(f"🤖 Sending essay to Ollama ({OLLAMA_MODEL}) for grading...")
#         raw_text = call_ollama(prompt)
#         clean    = raw_text.strip().replace("```json", "").replace("```", "").strip()

#         # Extract JSON even if there is extra text around it
#         json_match = re.search(r'\{.*\}', clean, re.DOTALL)
#         if json_match:
#             clean = json_match.group()

#         parsed = json.loads(clean)

#         if "score" in parsed and "feedback" in parsed:
#             ai_detected        = parsed.get("ai_detected", False)
#             ai_detection_score = 90 if ai_detected else 10

#             if ai_detected:
#                 ai_score    = 0
#                 ai_feedback = "⚠️ AI-generated content detected. " + str(parsed["feedback"]).strip()
#                 print(f"🚨 AI content detected — score set to 0")
#             else:
#                 ai_score    = max(0, min(max_score, int(parsed["score"])))
#                 ai_feedback = str(parsed["feedback"]).strip()
#                 print(f"✅ Ollama graded: {ai_score}/{max_score}")

#     except Exception as e:
#         print(f"❌ Ollama grading failed: {e}")

#     new_status = "ai_graded" if ai_score is not None else "submitted"
#     submission.ai_score           = ai_score
#     submission.ai_feedback        = ai_feedback
#     submission.ai_detection_score = ai_detection_score
#     submission.status             = new_status
#     if ai_score is not None:
#         submission.ai_graded_at = datetime.now(timezone.utc)
#     db.commit()

#     return {
#         "success": True,
#         "message": "Essay submitted and graded successfully",
#         "submission": {
#             "id":                 submission.id,
#             "ai_score":           ai_score,
#             "ai_feedback":        ai_feedback,
#             "ai_detection_score": ai_detection_score,
#             "status":             new_status,
#         }
#     }


# # ── POST /api/student/unsubmit ────────────────────────────────────────────────

# class UnsubmitRequest(BaseModel):
#     submission_id: int
#     csrf_token:    Optional[str] = None


# @router.post("/unsubmit")
# def unsubmit_essay(
#     body: UnsubmitRequest,
#     x_csrf_token: Optional[str] = Header(default=None),
#     ctx: dict = Depends(require_student),
# ):
#     user: models.User           = ctx["user"]
#     session: models.UserSession = ctx["session"]
#     db: Session                 = ctx["db"]

#     validate_csrf(session, x_csrf_token, body.csrf_token)

#     if not body.submission_id:
#         raise HTTPException(status_code=422, detail="submission_id is required")

#     submission = (
#         db.query(models.Submission)
#         .join(models.Assignment, models.Assignment.id == models.Submission.assignment_id)
#         .filter(
#             models.Submission.id         == body.submission_id,
#             models.Submission.student_id == user.id,
#         )
#         .first()
#     )

#     if not submission:
#         raise HTTPException(status_code=404, detail="Submission not found")

#     if submission.final_score is not None:
#         raise HTTPException(
#             status_code=422,
#             detail="This submission has already been graded and cannot be unsubmitted"
#         )

#     assignment = db.query(models.Assignment).filter(
#         models.Assignment.id == submission.assignment_id
#     ).first()

#     now = datetime.now(timezone.utc)
#     due = assignment.due_date
#     if due.tzinfo is None:
#         due = due.replace(tzinfo=timezone.utc)
#     if now > due:
#         raise HTTPException(
#             status_code=422,
#             detail="The deadline has passed — this submission can no longer be unsubmitted"
#         )

#     db.delete(submission)
#     db.commit()

#     return {
#         "success": True,
#         "message": "Essay unsubmitted successfully. You can now rewrite and resubmit before the deadline.",
#     }





import json
import os
import re
import requests as http_requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db
from auth_utils import require_student, validate_csrf
import models

router = APIRouter()

BLANTYRE = ZoneInfo("Africa/Blantyre")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HF_API_KEY     = os.getenv("HF_API_KEY", "")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
#HF_URL     = "https://api-inference.huggingface.co/models/HuggingFaceH4/zephyr-7b-beta/v1/chat/completions"
HF_URL = "https://router.huggingface.co/v1/chat/completions"

def call_gemini(prompt: str) -> str:
    resp = http_requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_huggingface(prompt: str) -> str:
    resp = http_requests.post(
        HF_URL,
        headers={
            "Authorization": f"Bearer {HF_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            # "messages": [{"role": "user", "content": prompt}],
            # "max_tokens": 512,
            # "temperature": 0.2,
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
            "temperature": 0.2,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def grade_with_ai(prompt: str) -> str:
    if GEMINI_API_KEY:
        try:
            print("🤖 Trying Gemini for grading...")
            result = call_gemini(prompt)
            print("✅ Gemini responded successfully")
            return result
        except Exception as e:
            print(f"⚠️ Gemini failed: {e} — trying Hugging Face backup...")

    if HF_API_KEY:
        try:
            print("🤖 Trying Hugging Face backup...")
            result = call_huggingface(prompt)
            print("✅ Hugging Face responded successfully")
            return result
        except Exception as e:
            print(f"❌ Hugging Face also failed: {e}")

    raise Exception("Both Gemini and Hugging Face grading failed.")


def fmt_date(dt):
    if not dt: return None
    if isinstance(dt, str): return dt
    if hasattr(dt, 'year') and dt.year < 2000: return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


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
            "id":           a.id,
            "title":        a.title,
            "description":  a.description,
            "instructions": a.instructions,
            "max_score":    a.max_score,
            "due_date":     fmt_date(a.due_date),
            "rubric":       json.loads(a.rubric) if a.rubric else None,
            "submitted":    s is not None,
            "submission": {
                "id":               s.id,
                "assignment_id":    s.assignment_id,
                "essay_text":       s.essay_text,
                "status":           s.status,
                "ai_score":         s.ai_score,
                "ai_feedback":      s.ai_feedback,
                "final_score":      s.final_score,
                "teacher_feedback": s.teacher_feedback,
                "submitted_at":     fmt_date(s.submitted_at),
                "graded_at":        fmt_date(s.graded_at),
            } if s else None,
        })
    return {"success": True, "assignments": assignments}


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
        results.append({
            "id":               s.id,
            "essay_text":       s.essay_text,
            "ai_score":         s.ai_score,
            "ai_feedback":      s.ai_feedback,
            "final_score":      s.final_score,
            "teacher_feedback": s.teacher_feedback,
            "status":           s.status,
            "submitted_at":     fmt_date(s.submitted_at),
            "graded_at":        fmt_date(s.graded_at),
            "assignment_title": a.title,
            "max_score":        a.max_score,
            "due_date":         fmt_date(a.due_date),
        })
    return {"success": True, "results": results}


class SubmitEssayRequest(BaseModel):
    assignment_id: int
    essay_text:    str
    csrf_token:    Optional[str] = None


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

    submission = models.Submission(
        assignment_id = assignment_id,
        student_id    = user.id,
        essay_text    = essay_text,
        status        = "submitted",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    rubric = json.loads(assignment.rubric) if assignment.rubric else {
        "content": 30, "structure": 25, "grammar": 20,
        "vocabulary": 15, "argumentation": 10
    }
    rubric_description = "\n".join(
        f"- {k.capitalize()}: {v}%" for k, v in rubric.items()
    )
    max_score = assignment.max_score

    prompt = f"""You are a strict academic essay grader. Grade honestly and critically.

Assignment: {assignment.title}
Instructions: {assignment.instructions}
Maximum Score: {max_score} points

Grading Rubric:
{rubric_description}

STRICT GRADING RULES:
- Score above 80 requires excellent structure, specific examples, and deep analysis
- Score 60-79 means good effort but missing depth or specific examples
- Score below 60 means poor structure, vague content, or off-topic
- If essay is under 100 words, score must be below 40
- If essay lacks specific examples or evidence, deduct at least 20 points
- If essay is generic with no real analysis, score must be below 50

AI DETECTION RULES:
- Check if essay sounds AI-written: overly formal, no personal voice, perfect grammar, generic phrases
- Phrases like "in conclusion", "it is important to note", "plays a crucial role" suggest AI writing
- If you suspect AI writing, set ai_detected to true

Student Essay ({word_count} words):
---
{essay_text[:3000]}
---

Respond ONLY with this exact JSON, no markdown, no extra text:
{{"score": <integer 0-{max_score}>, "feedback": "<specific detailed feedback>", "ai_detected": <true or false>}}"""

    ai_score           = None
    ai_feedback        = None
    ai_detection_score = None

    try:
        raw_text = grade_with_ai(prompt)
        clean    = raw_text.strip().replace("```json", "").replace("```", "").strip()
        json_match = re.search(r'\{.*\}', clean, re.DOTALL)
        if json_match:
            clean = json_match.group()
        parsed = json.loads(clean)
        if "score" in parsed and "feedback" in parsed:
            ai_detected        = parsed.get("ai_detected", False)
            ai_detection_score = 90 if ai_detected else 10
            if ai_detected:
                ai_score    = 0
                ai_feedback = "⚠️ AI-generated content detected. " + str(parsed["feedback"]).strip()
                print(f"🚨 AI content detected — score set to 0")
            else:
                ai_score    = max(0, min(max_score, int(parsed["score"])))
                ai_feedback = str(parsed["feedback"]).strip()
                print(f"✅ Graded: {ai_score}/{max_score}")
    except Exception as e:
        print(f"❌ All grading APIs failed: {e}")

    new_status = "ai_graded" if ai_score is not None else "submitted"
    submission.ai_score           = ai_score
    submission.ai_feedback        = ai_feedback
    submission.ai_detection_score = ai_detection_score
    submission.status             = new_status
    if ai_score is not None:
        submission.ai_graded_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "message": "Essay submitted and graded successfully",
        "submission": {
            "id":                 submission.id,
            "ai_score":           ai_score,
            "ai_feedback":        ai_feedback,
            "ai_detection_score": ai_detection_score,
            "status":             new_status,
        }
    }


class UnsubmitRequest(BaseModel):
    submission_id: int
    csrf_token:    Optional[str] = None


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
        raise HTTPException(
            status_code=422,
            detail="This submission has already been graded and cannot be unsubmitted"
        )

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id == submission.assignment_id
    ).first()

    now = datetime.now(timezone.utc)
    due = assignment.due_date
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if now > due:
        raise HTTPException(
            status_code=422,
            detail="The deadline has passed — this submission can no longer be unsubmitted"
        )

    db.delete(submission)
    db.commit()

    return {
        "success": True,
        "message": "Essay unsubmitted successfully. You can now rewrite and resubmit before the deadline.",
    }