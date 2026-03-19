




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

# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# HF_API_KEY     = os.getenv("HF_API_KEY", "")

# GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
# HF_URL     = "https://router.huggingface.co/v1/chat/completions"


# def call_gemini(prompt: str) -> str:
#     resp = http_requests.post(
#         f"{GEMINI_URL}?key={GEMINI_API_KEY}",
#         headers={"Content-Type": "application/json"},
#         json={
#             "contents": [{"parts": [{"text": prompt}]}],
#             "generationConfig": {
#                 "temperature": 0.0,
#                 "maxOutputTokens": 1024,
#                 "topP": 1.0,
#                 "topK": 1,
#             },
#         },
#         timeout=60,
#     )
#     resp.raise_for_status()
#     data = resp.json()
#     return data["candidates"][0]["content"]["parts"][0]["text"]


# def call_huggingface(prompt: str) -> str:
#     resp = http_requests.post(
#         HF_URL,
#         headers={
#             "Authorization": f"Bearer {HF_API_KEY}",
#             "Content-Type": "application/json",
#         },
#         json={
#             "model": "meta-llama/Llama-3.1-8B-Instruct",
#             "messages": [{"role": "user", "content": prompt}],
#             "max_tokens": 512,
#             "temperature": 0.0,
#         },
#         timeout=120,
#     )
#     resp.raise_for_status()
#     data = resp.json()
#     return data["choices"][0]["message"]["content"]


# def grade_with_ai(prompt: str) -> str:
#     if GEMINI_API_KEY:
#         try:
#             print("🤖 Trying Gemini for grading...")
#             result = call_gemini(prompt)
#             print("✅ Gemini responded successfully")
#             return result
#         except Exception as e:
#             print(f"⚠️ Gemini failed: {e} — trying Hugging Face backup...")

#     if HF_API_KEY:
#         try:
#             print("🤖 Trying Hugging Face backup...")
#             result = call_huggingface(prompt)
#             print("✅ Hugging Face responded successfully")
#             return result
#         except Exception as e:
#             print(f"❌ Hugging Face also failed: {e}")

#     raise Exception("Both Gemini and Hugging Face grading failed.")


# def parse_ai_response(raw_text: str, max_score: int) -> dict:
#     """Robustly parse AI response — handles broken JSON from HuggingFace."""
#     clean = raw_text.strip().replace("```json", "").replace("```", "").strip()

#     json_match = re.search(r'\{.*\}', clean, re.DOTALL)
#     if json_match:
#         clean = json_match.group()

#     # Attempt 1: direct parse
#     try:
#         return json.loads(clean)
#     except json.JSONDecodeError:
#         pass

#     # Attempt 2: extract values manually with regex
#     score_match    = re.search(r'"score"\s*:\s*(\d+)', clean)
#     feedback_match = re.search(r'"feedback"\s*:\s*"(.*?)"(?:\s*,|\s*})', clean, re.DOTALL)
#     ai_match       = re.search(r'"ai_detected"\s*:\s*(true|false)', clean)

#     if score_match:
#         feedback = ""
#         if feedback_match:
#             feedback = feedback_match.group(1).replace('\\"', '"').strip()
#         else:
#             fb = re.search(r'"feedback"\s*:\s*"(.+)', clean, re.DOTALL)
#             if fb:
#                 feedback = fb.group(1)[:500].strip().rstrip('"}')

#         return {
#             "score":       int(score_match.group(1)),
#             "feedback":    feedback or "Graded successfully.",
#             # ── IMPORTANT: default ai_detected to FALSE when parsing fails ──
#             # HuggingFace is unreliable at AI detection — only trust explicit "true"
#             "ai_detected": ai_match.group(1) == "true" if ai_match else False,
#         }

#     raise ValueError(f"Could not parse AI response: {clean[:200]}")


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
#             "id":                 a.id,
#             "title":              a.title,
#             "description":        a.description,
#             "instructions":       a.instructions,
#             # ── Return reference_material so student can read it ──
#             "reference_material": a.reference_material,
#             "max_score":          a.max_score,
#             "due_date":           fmt_date(a.due_date),
#             "rubric":             json.loads(a.rubric) if a.rubric else None,
#             "submitted":          s is not None,
#             "submission": {
#                 "id":               s.id,
#                 "assignment_id":    s.assignment_id,
#                 "essay_text":       s.essay_text,
#                 "status":           s.status,
#                 "ai_score":         s.ai_score if s.final_score is not None else None,
#                 "ai_feedback":      s.ai_feedback if s.final_score is not None else None,
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
#         teacher_approved = s.final_score is not None
#         results.append({
#             "id":                 s.id,
#             "essay_text":         s.essay_text,
#             "ai_score":           s.ai_score if teacher_approved else None,
#             "ai_feedback":        s.ai_feedback if teacher_approved else None,
#             "ai_detection_score": s.ai_detection_score,
#             "final_score":        s.final_score,
#             "teacher_feedback":   s.teacher_feedback,
#             "status":             s.status,
#             "submitted_at":       fmt_date(s.submitted_at),
#             "graded_at":          fmt_date(s.graded_at),
#             "assignment_title":   a.title,
#             "max_score":          a.max_score,
#             "due_date":           fmt_date(a.due_date),
#             "assignment_id":      s.assignment_id,
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

#     # ── Build rubric ──────────────────────────────────────────────────────
#     rubric = json.loads(assignment.rubric) if assignment.rubric else {
#         "content": 30, "structure": 25, "grammar": 20,
#         "vocabulary": 15, "argumentation": 10
#     }

#     rubric_lines = []
#     for criterion, weight in rubric.items():
#         rubric_lines.append(f"- {criterion.capitalize()} ({weight}pts): score out of {weight}")
#     rubric_description = "\n".join(rubric_lines)

#     max_score      = assignment.max_score
#     reference_text = ""
#     if assignment.reference_material and assignment.reference_material.strip():
#         reference_text = f"\nREFERENCE MATERIAL (use this to assess accuracy):\n---\n{assignment.reference_material[:2000]}\n---\n"

#     prompt = f"""You are a fair and consistent academic essay grader for a secondary school or university class.

# Assignment: {assignment.title}
# Instructions: {assignment.instructions}
# Maximum Score: {max_score} points
# {reference_text}
# RUBRIC:
# {rubric_description}

# SCORING SCALE:
# - 90-100%: Exceptional — specific examples, strong analysis, well structured
# - 75-89%: Good — clear arguments, some examples, minor issues
# - 60-74%: Satisfactory — covers basics, limited depth
# - 40-59%: Weak — vague content, poor structure
# - 0-39%: Very poor — off-topic or far too short

# AI DETECTION RULES (BE VERY CONSERVATIVE):
# - Most student essays are NOT AI-generated. Default to ai_detected=false.
# - Only set ai_detected=true if ALL of these are true:
#   1. The essay has zero personal voice or opinions
#   2. Every paragraph starts with a generic topic sentence
#   3. The essay uses 5+ of these exact phrases: "it is important to note", "plays a crucial role", "in conclusion", "furthermore", "in today's society", "it is worth noting", "delve into"
#   4. The writing is suspiciously perfect with no errors at all
# - A well-written student essay should NEVER be flagged. When in doubt: ai_detected=false.
# - NOTE: Uploaded documents from the internet or textbooks are NOT student essays — if the text looks like a textbook chapter or article rather than a personal essay response, still grade it fairly but note it in feedback.

# Student Essay ({word_count} words):
# ---
# {essay_text[:3000]}
# ---

# Reply with ONLY this exact JSON and nothing else:
# {{"score": <integer 0-{max_score}>, "feedback": "your detailed feedback here", "ai_detected": false}}"""

#     ai_score           = None
#     ai_feedback        = None
#     ai_detection_score = None

#     try:
#         raw_text = grade_with_ai(prompt)
#         parsed   = parse_ai_response(raw_text, max_score)

#         if "score" in parsed and "feedback" in parsed:
#             ai_detected = parsed.get("ai_detected", False)

#             # ── Extra safety: only trust ai_detected=true from Gemini ──────
#             # HuggingFace (Llama) is too unreliable for AI detection
#             # If Gemini is available and returned true, trust it
#             # If only HuggingFace responded, be skeptical — require score=0
#             # to have been explicitly set (not just flagged)
#             if ai_detected:
#                 # Flag for teacher review but DO NOT auto-zero
#                 ai_detection_score = 75
#                 ai_score    = max(0, min(max_score, int(parsed["score"])))
#                 ai_feedback = "⚠️ Possible AI-generated content — flagged for teacher review. " + str(parsed["feedback"]).strip()
#                 print(f"⚠️ AI content flagged for teacher review — score kept at {ai_score}/{max_score}")
#             else:
#                 ai_detection_score = 10
#                 ai_score    = max(0, min(max_score, int(parsed["score"])))
#                 ai_feedback = str(parsed["feedback"]).strip()
#                 print(f"✅ Graded: {ai_score}/{max_score}")

#     except Exception as e:
#         print(f"❌ All grading APIs failed: {e}")

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
#         "message": "Essay submitted. Results available after teacher review.",
#         "submission": {
#             "id":     submission.id,
#             "status": new_status,
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
import time
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

# ── Multiple HF fallback models — tried in order until one works ──────────────
# router.huggingface.co returns 500 frequently (their servers, not your code)
# Having multiple fallbacks means grading works even when 1-2 models are down
HF_MODELS = [
    # Model 1 — original working model (try first)
    {
        "url":  "https://router.huggingface.co/v1/chat/completions",
        "body": lambda prompt: {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 700,
            "temperature": 0.0,
        },
        "parse": lambda data: data["choices"][0]["message"]["content"],
        "name": "Llama-3.1-8B (router)",
    },
    # Model 2 — Qwen via router (very reliable, good at following JSON)
    {
        "url":  "https://router.huggingface.co/v1/chat/completions",
        "body": lambda prompt: {
            "model": "Qwen/Qwen2.5-72B-Instruct",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 700,
            "temperature": 0.0,
        },
        "parse": lambda data: data["choices"][0]["message"]["content"],
        "name": "Qwen2.5-72B (router)",
    },
    # Model 3 — Mistral via router
    {
        "url":  "https://router.huggingface.co/v1/chat/completions",
        "body": lambda prompt: {
            "model": "mistralai/Mistral-7B-Instruct-v0.3",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 700,
            "temperature": 0.0,
        },
        "parse": lambda data: data["choices"][0]["message"]["content"],
        "name": "Mistral-7B-v0.3 (router)",
    },
    # Model 4 — Llama 3 via direct inference API (different endpoint as last resort)
    {
        "url":  "https://api-inference.huggingface.co/models/meta-llama/Meta-Llama-3-8B-Instruct",
        "body": lambda prompt: {
            "inputs": prompt,
            "parameters": {"max_new_tokens": 512, "temperature": 0.01, "return_full_text": False},
        },
        "parse": lambda data: data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", ""),
        "name": "Llama-3-8B (inference API)",
    },
]


# ── AI callers ────────────────────────────────────────────────────────────────

def call_gemini(prompt: str) -> str:
    resp = http_requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1500,
                "topP": 1.0,
                "topK": 1,
            },
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_huggingface(prompt: str) -> str:
    """Try each HF model in order. Returns the first successful response."""
    last_error = None
    for model in HF_MODELS:
        try:
            print(f"🤖 Trying HF model: {model['name']}...")
            resp = http_requests.post(
                model["url"],
                headers={
                    "Authorization": f"Bearer {HF_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=model["body"](prompt),
                timeout=120,
            )
            resp.raise_for_status()
            result = model["parse"](resp.json())
            if result and result.strip():
                print(f"✅ {model['name']} responded successfully")
                return result
            print(f"⚠️ {model['name']} returned empty response — trying next...")
        except Exception as e:
            print(f"⚠️ {model['name']} failed: {e} — trying next...")
            last_error = e

    raise Exception(f"All HuggingFace models failed. Last error: {last_error}")


def grade_with_ai(prompt: str) -> str:
    """
    Try Gemini first. On 429 rate limit, wait 65s and retry once.
    If Gemini still fails, fall back through all HuggingFace models.
    """
    if GEMINI_API_KEY:
        try:
            print("🤖 Trying Gemini for grading...")
            result = call_gemini(prompt)
            print("✅ Gemini responded successfully")
            return result
        except http_requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                print("⏳ Gemini 429 rate limit — waiting 65s then retrying...")
                time.sleep(65)
                try:
                    result = call_gemini(prompt)
                    print("✅ Gemini retry succeeded")
                    return result
                except Exception as retry_err:
                    print(f"⚠️ Gemini retry failed: {retry_err} — trying Hugging Face...")
            else:
                print(f"⚠️ Gemini failed: {e} — trying Hugging Face backup...")
        except Exception as e:
            print(f"⚠️ Gemini failed: {e} — trying Hugging Face backup...")

    if HF_API_KEY:
        try:
            return call_huggingface(prompt)
        except Exception as e:
            print(f"❌ All Hugging Face models failed: {e}")

    raise Exception("Gemini and all Hugging Face models failed.")


# ── Response parser ───────────────────────────────────────────────────────────

def parse_ai_response(raw_text: str, max_score: int) -> dict:
    clean = raw_text.strip().replace("```json", "").replace("```", "").strip()

    json_match = re.search(r'\{.*\}', clean, re.DOTALL)
    if json_match:
        clean = json_match.group()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    score_match    = re.search(r'"score"\s*:\s*(\d+)', clean)
    feedback_match = re.search(r'"feedback"\s*:\s*"(.*?)"(?:\s*,|\s*})', clean, re.DOTALL)
    ai_match       = re.search(r'"ai_detected"\s*:\s*(true|false)', clean)
    topic_match    = re.search(r'"off_topic"\s*:\s*(true|false)', clean)

    if score_match:
        feedback = ""
        if feedback_match:
            feedback = feedback_match.group(1).replace('\\"', '"').strip()
        else:
            fb = re.search(r'"feedback"\s*:\s*"(.+)', clean, re.DOTALL)
            if fb:
                feedback = fb.group(1)[:500].strip().rstrip('"}')
        return {
            "score":       int(score_match.group(1)),
            "feedback":    feedback or "Graded successfully.",
            "ai_detected": ai_match.group(1) == "true" if ai_match else False,
            "off_topic":   topic_match.group(1) == "true" if topic_match else False,
        }

    raise ValueError(f"Could not parse AI response: {clean[:200]}")


# ── Grading prompt ────────────────────────────────────────────────────────────

def build_grading_prompt(assignment, essay_text: str, word_count: int) -> str:
    rubric = json.loads(assignment.rubric) if assignment.rubric else {
        "content": 30, "structure": 25, "grammar": 20,
        "vocabulary": 15, "argumentation": 10,
    }
    rubric_lines = "\n".join(
        f"  - {k.capitalize()}: {v} points" for k, v in rubric.items()
    )
    reference_block = ""
    if assignment.reference_material and assignment.reference_material.strip():
        reference_block = (
            f"\nREFERENCE MATERIAL (provided by teacher — use to verify accuracy):\n"
            f"---\n{assignment.reference_material[:2500]}\n---\n"
        )
    max_score = assignment.max_score

    return f"""You are a strict academic essay grader. Grade the student essay ONLY based on how well it answers the assignment question below.

════════════════════════════════════════
ASSIGNMENT
════════════════════════════════════════
Title: {assignment.title}
Instructions: {assignment.instructions}
Maximum score: {max_score} points
{reference_block}
════════════════════════════════════════
GRADING RUBRIC
════════════════════════════════════════
{rubric_lines}

════════════════════════════════════════
MANDATORY RULES — FOLLOW EXACTLY
════════════════════════════════════════

RULE 1 — CHECK TOPIC FIRST (most important rule):
Before scoring anything, ask: Does this essay actually answer the assignment question?
- If the essay is about a COMPLETELY DIFFERENT SUBJECT than what is asked, set off_topic=true and score 0 to {round(max_score * 0.05)}.
- Example: assignment asks about "Java programming for beginners" but essay is about "climate change" → off_topic=true, score={round(max_score * 0.05)}
- A beautifully written essay on the WRONG topic still scores near 0. Writing quality cannot save an off-topic essay.

RULE 2 — LENGTH CHECK:
- If instructions say "five page essay" (~1200+ words) but submission is under 300 words, deduct heavily.
- Under 100 words on any assignment → max score is {round(max_score * 0.20)}.

RULE 3 — SCORING SCALE (only applies if essay is ON-TOPIC):
- 90-100%: Exceptional — directly answers question, strong analysis, specific examples
- 75-89%: Good — answers question, minor gaps
- 60-74%: Satisfactory — partially answers, limited depth
- 40-59%: Weak — barely addresses the question
- 20-39%: Very poor — mostly irrelevant content
- 0-15%: Completely off-topic or wrong subject entirely

RULE 4 — AI DETECTION (be very conservative):
- Default: ai_detected=false
- Only set ai_detected=true if ALL three are true:
  (a) zero personal voice or student perspective
  (b) robotic, perfectly structured paragraphs with no errors at all
  (c) contains 5 or more of these exact phrases: "it is important to note", "plays a crucial role",
      "in today's society", "it is worth noting", "delve into", "in conclusion it is", "furthermore it is"

════════════════════════════════════════
STUDENT ESSAY ({word_count} words)
════════════════════════════════════════
{essay_text[:4000]}
════════════════════════════════════════

THINK STEP BY STEP:
Step 1: What topic does the assignment ask about?
Step 2: What topic is the essay actually about?
Step 3: Do they match? If not → off_topic=true, score very low
Step 4: If they match → score using the rubric

Reply ONLY with this exact JSON, nothing else:
{{"score": <integer 0-{max_score}>, "feedback": "specific feedback explaining what the essay got right, what was wrong, and why this score was given", "off_topic": <true or false>, "ai_detected": <true or false>}}"""


# ── Date formatter ────────────────────────────────────────────────────────────

def fmt_date(dt):
    if not dt: return None
    if isinstance(dt, str): return dt
    if hasattr(dt, 'year') and dt.year < 2000: return None
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
            "rubric":             json.loads(a.rubric) if a.rubric else None,
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


# ── POST /api/student/submit ──────────────────────────────────────────────────

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

    ai_score           = None
    ai_feedback        = None
    ai_detection_score = None

    try:
        prompt   = build_grading_prompt(assignment, essay_text, word_count)
        raw_text = grade_with_ai(prompt)
        parsed   = parse_ai_response(raw_text, assignment.max_score)

        if "score" in parsed and "feedback" in parsed:
            off_topic   = parsed.get("off_topic",   False)
            ai_detected = parsed.get("ai_detected", False)
            raw_score   = max(0, min(assignment.max_score, int(parsed["score"])))

            if off_topic:
                cap_score          = round(assignment.max_score * 0.05)
                ai_score           = min(raw_score, cap_score)
                ai_detection_score = 10
                ai_feedback        = (
                    f"❌ OFF-TOPIC SUBMISSION\n\n"
                    f"The assignment asked you to write about: \"{assignment.title}\"\n"
                    f"Your essay does not address this topic.\n\n"
                    f"Please resubmit an essay that directly answers the assignment question.\n\n"
                    f"Score capped at {ai_score}/{assignment.max_score} for off-topic submission."
                )
                print(f"❌ Off-topic — capped at {ai_score}/{assignment.max_score}")

            elif ai_detected:
                ai_detection_score = 75
                ai_score           = raw_score
                ai_feedback        = (
                    f"⚠️ Possible AI-generated content — flagged for teacher review.\n\n"
                    f"{str(parsed['feedback']).strip()}"
                )
                print(f"⚠️ AI content flagged — score {ai_score}/{assignment.max_score}")

            else:
                ai_detection_score = 10
                ai_score           = raw_score
                ai_feedback        = str(parsed["feedback"]).strip()
                print(f"✅ Graded: {ai_score}/{assignment.max_score}")

    except Exception as e:
        print(f"❌ Grading failed: {e}")

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
        "message": "Essay submitted. Results available after teacher review.",
        "submission": {"id": submission.id, "status": new_status},
    }


# ── POST /api/student/unsubmit ────────────────────────────────────────────────

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
            detail="This submission has already been graded and cannot be unsubmitted",
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
            detail="The deadline has passed — this submission can no longer be unsubmitted",
        )

    db.delete(submission)
    db.commit()

    return {
        "success": True,
        "message": "Essay unsubmitted successfully. You can now rewrite and resubmit before the deadline.",
    }