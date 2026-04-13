# import json
# import os
# import re
# import time
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

# # ── Local model ───────────────────────────────────────────────────────────────
# #LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "all-MiniLM-L6-v2")

# #LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "all-MiniLM-L6-v2")

# LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "essay-grader-finetuned")
# _local_model = None  # lazy-loaded on first use

# # Similarity below this = low confidence → flagged for teacher review
# CONFIDENCE_THRESHOLD = 0.60


# # ── HuggingFace fallback models ───────────────────────────────────────────────
# HF_MODELS = [
#     {
#         "url":  "https://router.huggingface.co/v1/chat/completions",
#         "body": lambda prompt: {
#             "model": "meta-llama/Llama-3.1-8B-Instruct",
#             "messages": [{"role": "user", "content": prompt}],
#             "max_tokens": 700,
#             "temperature": 0.0,
#         },
#         "parse": lambda data: data["choices"][0]["message"]["content"],
#         "name": "Llama-3.1-8B (router)",
#     },
#     {
#         "url":  "https://router.huggingface.co/v1/chat/completions",
#         "body": lambda prompt: {
#             "model": "Qwen/Qwen2.5-72B-Instruct",
#             "messages": [{"role": "user", "content": prompt}],
#             "max_tokens": 700,
#             "temperature": 0.0,
#         },
#         "parse": lambda data: data["choices"][0]["message"]["content"],
#         "name": "Qwen2.5-72B (router)",
#     },
#     {
#         "url":  "https://router.huggingface.co/v1/chat/completions",
#         "body": lambda prompt: {
#             "model": "mistralai/Mistral-7B-Instruct-v0.3",
#             "messages": [{"role": "user", "content": prompt}],
#             "max_tokens": 700,
#             "temperature": 0.0,
#         },
#         "parse": lambda data: data["choices"][0]["message"]["content"],
#         "name": "Mistral-7B-v0.3 (router)",
#     },
#     {
#         "url":  "https://api-inference.huggingface.co/models/meta-llama/Meta-Llama-3-8B-Instruct",
#         "body": lambda prompt: {
#             "inputs": prompt,
#             "parameters": {"max_new_tokens": 512, "temperature": 0.01, "return_full_text": False},
#         },
#         "parse": lambda data: data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", ""),
#         "name": "Llama-3-8B (inference API)",
#     },
# ]


# # ── AI callers ────────────────────────────────────────────────────────────────

# def call_gemini(prompt: str) -> str:
#     resp = http_requests.post(
#         f"{GEMINI_URL}?key={GEMINI_API_KEY}",
#         headers={"Content-Type": "application/json"},
#         json={
#             "contents": [{"parts": [{"text": prompt}]}],
#             "generationConfig": {
#                 "temperature": 0.0,
#                 "maxOutputTokens": 1500,
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
#     last_error = None
#     for model in HF_MODELS:
#         try:
#             print(f"🤖 Trying HF model: {model['name']}...")
#             resp = http_requests.post(
#                 model["url"],
#                 headers={
#                     "Authorization": f"Bearer {HF_API_KEY}",
#                     "Content-Type": "application/json",
#                 },
#                 json=model["body"](prompt),
#                 timeout=120,
#             )
#             resp.raise_for_status()
#             result = model["parse"](resp.json())
#             if result and result.strip():
#                 print(f"✅ {model['name']} responded successfully")
#                 return result
#             print(f"⚠️ {model['name']} returned empty — trying next...")
#         except Exception as e:
#             print(f"⚠️ {model['name']} failed: {e} — trying next...")
#             last_error = e
#     raise Exception(f"All HuggingFace models failed. Last error: {last_error}")


# # ── Local model loader & grader ───────────────────────────────────────────────

# def get_local_model():
#     """Load the local SentenceTransformer model from disk (once, then cached)."""
#     global _local_model
#     if _local_model is None:
#         try:
#             from sentence_transformers import SentenceTransformer
#             print("📦 Loading local model from disk...")
#             _local_model = SentenceTransformer(LOCAL_MODEL_PATH)
#             print("✅ Local model ready")
#         except Exception as e:
#             print(f"❌ Failed to load local model: {e}")
#             raise
#     return _local_model



# def grade_with_local_model(assignment, essay_text: str, word_count: int = 0) -> dict:
#     model = get_local_model()

#     title        = (assignment.title or "").strip()
#     instructions = (assignment.instructions or "").strip()
#     ref_material = (assignment.reference_material or "")[:1500].strip()

#     # Build multiple reference sentences and average them
#     refs = [
#         f"{title}. {instructions}",
#         f"{title}. {title}. {instructions}",
#         f"{instructions} {ref_material}" if ref_material else instructions,
#     ]

#     anchored_essay = f"{title}. {essay_text[:2500]}"

#     ref_embeddings   = model.encode(refs, convert_to_tensor=True)
#     essay_embedding  = model.encode([anchored_essay], convert_to_tensor=True)

#     # Take the MAX similarity across all reference variants
#     from sentence_transformers import util as st_util
#     scores = st_util.cos_sim(essay_embedding, ref_embeddings)[0]
#     raw_similarity = float(scores.max().item())

#     # Scale: map 0.2–0.9 range → 0–100
#     low, high = 0.20, 0.90
#     scaled = (raw_similarity - low) / (high - low)
#     scaled = max(0.0, min(1.0, scaled))

#     max_score      = assignment.max_score or 100
#     confidence_pct = raw_similarity * 100

#     # Word count penalty
#     expected_words = 150
#     wc_ratio       = min(word_count / expected_words, 1.0) if word_count > 0 else 0.5
#     wc_factor      = 0.7 + (0.3 * wc_ratio)

#     base_score    = scaled * max_score * wc_factor
#     off_topic     = confidence_pct < 10
#     low_confidence = confidence_pct < 35

#     if off_topic:
#         final_score = min(base_score, max_score * 0.05)
#     elif low_confidence:
#         final_score = base_score * 0.75
#     else:
#         final_score = base_score

#     final_score = round(max(0, min(final_score, max_score)))

#     print(f"🖥️  Local model → raw_similarity={confidence_pct:.1f}% | score={final_score}/{max_score} | off_topic={off_topic} | low_confidence={low_confidence}")

#     return {
#         "score": final_score,
#         "max_score": max_score,
#         "feedback": f"Score: {final_score}/{max_score}. {'Essay appears off-topic.' if off_topic else 'Low confidence — flagged for teacher review.' if low_confidence else 'Graded successfully.'}",
#         "ai_detected": False,
#         "off_topic": off_topic,
#         "low_confidence": low_confidence,
#         "graded_by": "local_model",
#     }



# # def grade_with_local_model(assignment, essay_text: str, word_count: int) -> dict:
# #     """
# #     Grade essay using the local all-MiniLM-L6-v2 sentence similarity model.

# #     Pipeline:
# #       1. Build reference text from assignment instructions + reference material.
# #       2. Encode both texts into sentence embeddings.
# #       3. Cosine similarity (0.0–1.0) = confidence/relevance score.
# #       4. Apply word-count penalties for very short essays.
# #       5. Map similarity to score, flag low-confidence submissions.

# #     Confidence bands:
# #       >= 60%  → Normal grade, decent confidence
# #       35–59%  → Grade given, flagged LOW CONFIDENCE for teacher review
# #       15–34%  → Very weak match, score heavily penalised, flagged
# #       < 15%   → Off-topic
# #     """
# #     from sentence_transformers import util

# #     model     = get_local_model()
# #     max_score = assignment.max_score

# #     # Build reference text
# #     reference = (assignment.instructions or "").strip()
# #     if assignment.reference_material and assignment.reference_material.strip():
# #         reference += " " + assignment.reference_material[:1500]
# #     if not reference:
# #         reference = assignment.title or "essay"

# #     # Encode both texts
# #     ref_emb   = model.encode(reference,         convert_to_tensor=True)
# #     essay_emb = model.encode(essay_text[:2500],  convert_to_tensor=True)

# #     # Cosine similarity → confidence
# #     similarity     = float(util.cos_sim(ref_emb, essay_emb)[0][0])
# #     confidence_pct = round(similarity * 100, 1)

# #     # Word-count penalty applied to similarity before scoring
# #     if word_count < 50:
# #         similarity *= 0.10
# #     elif word_count < 100:
# #         similarity *= 0.30
# #     elif word_count < 200:
# #         similarity *= 0.60
# #     elif word_count < 300:
# #         similarity *= 0.80
# #     # 300+ words → no penalty

# #     raw_score = max(0, min(max_score, round(similarity * max_score)))

# #     off_topic      = confidence_pct < 15
# #     low_confidence = (not off_topic) and (confidence_pct < CONFIDENCE_THRESHOLD * 100)

# #     print(
# #         f"🖥️  Local model → raw_similarity={confidence_pct}% | "
# #         f"score={raw_score}/{max_score} | "
# #         f"off_topic={off_topic} | low_confidence={low_confidence}"
# #     )

# #     # Build feedback message
# #     if off_topic:
# #         feedback = (
# #             f"❌ OFF-TOPIC SUBMISSION\n\n"
# #             f"The assignment asked: \"{assignment.title}\"\n"
# #             f"Your essay does not appear to address this topic "
# #             f"(relevance: {confidence_pct}%).\n\n"
# #             f"Score capped at {raw_score}/{max_score}. "
# #             f"Please reread the instructions and resubmit."
# #         )
# #     elif low_confidence:
# #         if confidence_pct < 35:
# #             feedback = (
# #                 f"⚠️ LOW CONFIDENCE GRADE (local AI)\n\n"
# #                 f"Your essay weakly addresses the assignment topic "
# #                 f"(relevance: {confidence_pct}%).\n"
# #                 f"Score: {raw_score}/{max_score} — pending teacher review.\n\n"
# #                 f"Consider expanding your answer to better address: \"{assignment.title}\""
# #             )
# #         else:
# #             feedback = (
# #                 f"📝 LOW CONFIDENCE GRADE (local AI)\n\n"
# #                 f"Your essay partially addresses the topic "
# #                 f"(relevance: {confidence_pct}%).\n"
# #                 f"Score: {raw_score}/{max_score} — pending teacher review.\n\n"
# #                 f"Try to connect your points more directly to the assignment question."
# #             )
# #     elif confidence_pct >= 80:
# #         feedback = (
# #             f"✅ Good essay! Your response closely addresses the assignment "
# #             f"(relevance: {confidence_pct}%).\n"
# #             f"Score: {raw_score}/{max_score} — graded by local AI, pending teacher confirmation."
# #         )
# #     else:
# #         feedback = (
# #             f"📝 Your essay addresses the topic (relevance: {confidence_pct}%).\n"
# #             f"Score: {raw_score}/{max_score} — graded by local AI, pending teacher review."
# #         )

# #     return {
# #         "score":          raw_score,
# #         "feedback":       feedback,
# #         "ai_detected":    False,
# #         "off_topic":      off_topic,
# #         "low_confidence": low_confidence,
# #         "confidence_pct": confidence_pct,
# #         "graded_by":      "local_model",
# #     }


# # ── Main grading dispatcher ───────────────────────────────────────────────────

# def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
#     # ── Local model ONLY (Gemini + HuggingFace temporarily disabled) ──────────
#     if assignment and essay_text:
#         print("🖥️  Using local model...")
#         return grade_with_local_model(assignment, essay_text, word_count)

#     raise Exception("No grading method available.")





# # def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
# #     """
# #     Full grading pipeline:
# #       1. Gemini API        — best quality
# #       2. HuggingFace API   — good quality, 4 model fallbacks
# #       3. Local model       — offline fallback with confidence scoring

# #     Always returns a parsed dict:
# #       score, feedback, ai_detected, off_topic, low_confidence, graded_by
# #     """
# #     max_score = assignment.max_score if assignment else 100

# #     # ── Step 1: Gemini ────────────────────────────────────────────────────────
# #     if GEMINI_API_KEY:
# #         try:
# #             print("🤖 Trying Gemini for grading...")
# #             raw    = call_gemini(prompt)
# #             parsed = parse_ai_response(raw, max_score)
# #             parsed.setdefault("low_confidence", False)
# #             parsed.setdefault("graded_by", "gemini")
# #             print("✅ Gemini graded successfully")
# #             return parsed
# #         except http_requests.exceptions.HTTPError as e:
# #             if e.response is not None and e.response.status_code == 429:
# #                 print("⏳ Gemini 429 rate limit — waiting 65s then retrying...")
# #                 time.sleep(12)
# #                 try:
# #                     raw    = call_gemini(prompt)
# #                     parsed = parse_ai_response(raw, max_score)
# #                     parsed.setdefault("low_confidence", False)
# #                     parsed.setdefault("graded_by", "gemini")
# #                     print("✅ Gemini retry succeeded")
# #                     return parsed
# #                 except Exception as retry_err:
# #                     print(f"⚠️ Gemini retry failed: {retry_err} — trying HuggingFace...")
# #             else:
# #                 print(f"⚠️ Gemini HTTP error: {e} — trying HuggingFace...")
# #         except Exception as e:
# #             print(f"⚠️ Gemini failed: {e} — trying HuggingFace...")

# #     # ── Step 2: HuggingFace API ───────────────────────────────────────────────
# #     if HF_API_KEY:
# #         try:
# #             raw    = call_huggingface(prompt)
# #             parsed = parse_ai_response(raw, max_score)
# #             parsed.setdefault("low_confidence", False)
# #             parsed.setdefault("graded_by", "huggingface")
# #             return parsed
# #         except Exception as e:
# #             print(f"⚠️ All HuggingFace models failed: {e} — switching to local model...")

# #     # ── Step 3: Local model ───────────────────────────────────────────────────
# #     if assignment and essay_text:
# #         print("🖥️  Using local model as final fallback...")
# #         return grade_with_local_model(assignment, essay_text, word_count)

# #     raise Exception("All grading methods exhausted (Gemini, HuggingFace, local model).")


# # ── Response parser ───────────────────────────────────────────────────────────

# def parse_ai_response(raw_text: str, max_score: int) -> dict:
#     clean = raw_text.strip().replace("```json", "").replace("```", "").strip()

#     json_match = re.search(r'\{.*\}', clean, re.DOTALL)
#     if json_match:
#         clean = json_match.group()

#     try:
#         return json.loads(clean)
#     except json.JSONDecodeError:
#         pass

#     score_match    = re.search(r'"score"\s*:\s*(\d+)', clean)
#     feedback_match = re.search(r'"feedback"\s*:\s*"(.*?)"(?:\s*,|\s*})', clean, re.DOTALL)
#     ai_match       = re.search(r'"ai_detected"\s*:\s*(true|false)', clean)
#     topic_match    = re.search(r'"off_topic"\s*:\s*(true|false)', clean)

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
#             "ai_detected": ai_match.group(1) == "true" if ai_match else False,
#             "off_topic":   topic_match.group(1) == "true" if topic_match else False,
#         }

#     raise ValueError(f"Could not parse AI response: {clean[:200]}")


# # ── Grading prompt ────────────────────────────────────────────────────────────

# def build_grading_prompt(assignment, essay_text: str, word_count: int) -> str:
#     rubric = json.loads(assignment.rubric) if assignment.rubric else {
#         "content": 30, "structure": 25, "grammar": 20,
#         "vocabulary": 15, "argumentation": 10,
#     }
#     rubric_lines = "\n".join(
#         f"  - {k.capitalize()}: {v} points" for k, v in rubric.items()
#     )
#     reference_block = ""
#     if assignment.reference_material and assignment.reference_material.strip():
#         reference_block = (
#             f"\nREFERENCE MATERIAL (provided by teacher — use to verify accuracy):\n"
#             f"---\n{assignment.reference_material[:2500]}\n---\n"
#         )
#     max_score = assignment.max_score

#     return f"""You are a strict academic essay grader. Grade the student essay ONLY based on how well it answers the assignment question below.

# ════════════════════════════════════════
# ASSIGNMENT
# ════════════════════════════════════════
# Title: {assignment.title}
# Instructions: {assignment.instructions}
# Maximum score: {max_score} points
# {reference_block}
# ════════════════════════════════════════
# GRADING RUBRIC
# ════════════════════════════════════════
# {rubric_lines}

# ════════════════════════════════════════
# MANDATORY RULES — FOLLOW EXACTLY
# ════════════════════════════════════════

# RULE 1 — CHECK TOPIC FIRST (most important rule):
# Before scoring anything, ask: Does this essay actually answer the assignment question?
# - If the essay is about a COMPLETELY DIFFERENT SUBJECT than what is asked, set off_topic=true and score 0 to {round(max_score * 0.05)}.
# - Example: assignment asks about "Java programming for beginners" but essay is about "climate change" → off_topic=true, score={round(max_score * 0.05)}
# - A beautifully written essay on the WRONG topic still scores near 0. Writing quality cannot save an off-topic essay.

# RULE 2 — LENGTH CHECK:
# - If instructions say "five page essay" (~1200+ words) but submission is under 300 words, deduct heavily.
# - Under 100 words on any assignment → max score is {round(max_score * 0.20)}.

# RULE 3 — SCORING SCALE (only applies if essay is ON-TOPIC):
# - 90-100%: Exceptional — directly answers question, strong analysis, specific examples
# - 75-89%: Good — answers question, minor gaps
# - 60-74%: Satisfactory — partially answers, limited depth
# - 40-59%: Weak — barely addresses the question
# - 20-39%: Very poor — mostly irrelevant content
# - 0-15%: Completely off-topic or wrong subject entirely

# RULE 4 — AI DETECTION (be very conservative):
# - Default: ai_detected=false
# - Only set ai_detected=true if ALL three are true:
#   (a) zero personal voice or student perspective
#   (b) robotic, perfectly structured paragraphs with no errors at all
#   (c) contains 5 or more of these exact phrases: "it is important to note", "plays a crucial role",
#       "in today's society", "it is worth noting", "delve into", "in conclusion it is", "furthermore it is"

# ════════════════════════════════════════
# STUDENT ESSAY ({word_count} words)
# ════════════════════════════════════════
# {essay_text[:4000]}
# ════════════════════════════════════════

# THINK STEP BY STEP:
# Step 1: What topic does the assignment ask about?
# Step 2: What topic is the essay actually about?
# Step 3: Do they match? If not → off_topic=true, score very low
# Step 4: If they match → score using the rubric

# Reply ONLY with this exact JSON, nothing else:
# {{"score": <integer 0-{max_score}>, "feedback": "specific feedback explaining what the essay got right, what was wrong, and why this score was given", "off_topic": <true or false>, "ai_detected": <true or false>}}"""


# # ── Date formatter ────────────────────────────────────────────────────────────

# def fmt_date(dt):
#     if not dt: return None
#     if isinstance(dt, str): return dt
#     if hasattr(dt, 'year') and dt.year < 2000: return None
#     return dt.strftime("%Y-%m-%d %H:%M:%S")


# # ── GET /api/student/assignments ──────────────────────────────────────────────

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

#     # Save submission immediately
#     submission = models.Submission(
#         assignment_id = assignment_id,
#         student_id    = user.id,
#         essay_text    = essay_text,
#         status        = "submitted",
#     )
#     db.add(submission)
#     db.commit()
#     db.refresh(submission)

#     ai_score           = None
#     ai_feedback        = None
#     ai_detection_score = None

#     try:
#         prompt = build_grading_prompt(assignment, essay_text, word_count)

#         # Full pipeline: Gemini → HuggingFace → Local model
#         parsed = grade_with_ai(
#             prompt,
#             assignment=assignment,
#             essay_text=essay_text,
#             word_count=word_count,
#         )

#         if "score" in parsed and "feedback" in parsed:
#             off_topic      = parsed.get("off_topic",      False)
#             ai_detected    = parsed.get("ai_detected",    False)
#             low_confidence = parsed.get("low_confidence", False)
#             graded_by      = parsed.get("graded_by",      "unknown")
#             raw_score      = max(0, min(assignment.max_score, int(parsed["score"])))

#             # ── Off-topic ──────────────────────────────────────────────────────
#             if off_topic:
#                 cap_score          = round(assignment.max_score * 0.05)
#                 ai_score           = min(raw_score, cap_score)
#                 ai_detection_score = 10
#                 ai_feedback        = (
#                     f"❌ OFF-TOPIC SUBMISSION\n\n"
#                     f"The assignment asked: \"{assignment.title}\"\n"
#                     f"Your essay does not address this topic.\n\n"
#                     f"Score capped at {ai_score}/{assignment.max_score}.\n"
#                     f"Please resubmit an essay that directly answers the assignment question."
#                 )
#                 print(f"❌ Off-topic — capped at {ai_score}/{assignment.max_score} [by {graded_by}]")

#             # ── AI detected ────────────────────────────────────────────────────
#             elif ai_detected:
#                 ai_detection_score = 75
#                 ai_score           = raw_score
#                 ai_feedback        = (
#                     f"⚠️ Possible AI-generated content — flagged for teacher review.\n\n"
#                     f"{str(parsed['feedback']).strip()}"
#                 )
#                 print(f"⚠️ AI content flagged — {ai_score}/{assignment.max_score} [by {graded_by}]")

#             # ── Low confidence (local model only) ──────────────────────────────
#             elif low_confidence:
#                 ai_detection_score = 10
#                 ai_score           = raw_score
#                 ai_feedback        = str(parsed["feedback"]).strip()
#                 print(f"📉 Low confidence — {ai_score}/{assignment.max_score} [by {graded_by}] — flagged for teacher")

#             # ── Normal grade ───────────────────────────────────────────────────
#             else:
#                 ai_detection_score = 10
#                 ai_score           = raw_score
#                 ai_feedback        = str(parsed["feedback"]).strip()
#                 print(f"✅ Graded: {ai_score}/{assignment.max_score} [by {graded_by}]")

#     except Exception as e:
#         print(f"❌ All grading methods failed: {e}")

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
#         "submission": {"id": submission.id, "status": new_status},
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
#             detail="This submission has already been graded and cannot be unsubmitted",
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
#             detail="The deadline has passed — this submission can no longer be unsubmitted",
#         )

#     db.delete(submission)
#     db.commit()

#     return {
#         "success": True,
#         "message": "Essay unsubmitted successfully. You can now rewrite and resubmit before the deadline.",
#     }





"""
routes/student.py
=================
Entry point — just imports and registers sub-routers.
You should rarely need to edit this file.

File map:
  routes/ai_grader.py          → change AI model, scoring logic, HF models
  routes/grading_prompt.py     → change the prompt sent to AI, response parsing
  routes/submission_routes.py  → change submit/unsubmit behaviour
  routes/assignment_routes.py  → change what assignments/results students see
"""

from fastapi import APIRouter
from routes.submission_routes import router as submission_router
from routes.assignment_routes import router as assignment_router

router = APIRouter()

router.include_router(assignment_router)
router.include_router(submission_router)