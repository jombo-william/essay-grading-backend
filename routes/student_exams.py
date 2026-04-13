# # routes/student_exams.py
# import json
# import os
# import re
# import requests as http_requests
# from datetime import datetime, timezone
# from zoneinfo import ZoneInfo
# from fastapi import APIRouter, Depends, HTTPException, Header
# from sqlalchemy.orm import Session
# from pydantic import BaseModel
# from typing import Optional, List
# from auth_utils import require_student, validate_csrf
# import models

# router = APIRouter()

# BLANTYRE       = ZoneInfo("Africa/Blantyre")
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# GEMINI_URL     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "essay-grader-finetuned")
# _local_model = None


# # ═══════════════════════════════════════════════════════════════════════════════
# #  PYDANTIC SCHEMAS
# # ═══════════════════════════════════════════════════════════════════════════════

# class AnswerSchema(BaseModel):
#     question_id:     int
#     selected_option: Optional[str] = None   # MCQ only
#     answer_text:     Optional[str] = None   # structured only


# class SubmitExamRequest(BaseModel):
#     exam_id:    int
#     answers:    List[AnswerSchema]
#     csrf_token: Optional[str] = None


# # ═══════════════════════════════════════════════════════════════════════════════
# #  AI GRADING  (mirrors student.py pipeline)
# # ═══════════════════════════════════════════════════════════════════════════════

# def get_local_model():
#     global _local_model
#     if _local_model is None:
#         from sentence_transformers import SentenceTransformer
#         print("📦 Loading local model...")
#         _local_model = SentenceTransformer(LOCAL_MODEL_PATH)
#         print("✅ Local model ready")
#     return _local_model


# def _grade_structured_local(question: models.ExamQuestion, answer_text: str) -> dict:
#     """Grade a single structured answer with the local sentence-transformer."""
#     model     = get_local_model()
#     max_marks = question.marks or 1
#     prompt    = (question.prompt        or "").strip()
#     guide     = (question.marking_guide or "").strip()

#     refs = [
#         prompt,
#         f"{prompt} {guide}" if guide else prompt,
#     ]
#     anchored = f"{prompt}. {answer_text[:2000]}"

#     from sentence_transformers import util as st_util
#     ref_emb    = model.encode(refs,      convert_to_tensor=True)
#     ans_emb    = model.encode([anchored], convert_to_tensor=True)
#     similarity = float(st_util.cos_sim(ans_emb, ref_emb)[0].max().item())

#     # Scale 0.2–0.9 → 0–1
#     scaled    = max(0.0, min(1.0, (similarity - 0.20) / 0.70))
#     wc_factor = 0.7 + 0.3 * min(len(answer_text.split()) / 50, 1.0)
#     score     = max(0, min(round(scaled * max_marks * wc_factor), max_marks))
#     low_conf  = (similarity * 100) < 35

#     print(f"🖥️  Local Q{question.id} → sim={similarity*100:.1f}% | {score}/{max_marks}")
#     return {
#         "score":          score,
#         "feedback":       f"Score: {score}/{max_marks}. " + ("Low confidence — flagged for teacher review." if low_conf else "Graded by AI."),
#         "low_confidence": low_conf,
#     }


# def _call_gemini(prompt: str) -> str:
#     resp = http_requests.post(
#         f"{GEMINI_URL}?key={GEMINI_API_KEY}",
#         headers={"Content-Type": "application/json"},
#         json={
#             "contents": [{"parts": [{"text": prompt}]}],
#             "generationConfig": {"temperature": 0.0, "maxOutputTokens": 500},
#         },
#         timeout=60,
#     )
#     resp.raise_for_status()
#     return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


# def _grade_structured_with_ai(question: models.ExamQuestion, answer_text: str) -> dict:
#     """Gemini first, local model as fallback — same pattern as student.py."""
#     max_marks = question.marks or 1

#     if GEMINI_API_KEY:
#         try:
#             prompt = (
#                 f"You are a strict exam marker.\n\n"
#                 f"QUESTION: {question.prompt}\n"
#                 f"MARKING GUIDE: {question.marking_guide or 'Grade on relevance and correctness.'}\n"
#                 f"MAX MARKS: {max_marks}\n\n"
#                 f"STUDENT ANSWER:\n{answer_text[:2000]}\n\n"
#                 f"Reply ONLY with this JSON:\n"
#                 f'{{ "score": <int 0-{max_marks}>, "feedback": "brief specific feedback" }}'
#             )
#             raw   = _call_gemini(prompt)
#             clean = raw.strip().replace("```json", "").replace("```", "").strip()
#             m     = re.search(r'\{.*\}', clean, re.DOTALL)
#             if m:
#                 parsed = json.loads(m.group())
#                 score  = max(0, min(max_marks, int(parsed.get("score", 0))))
#                 print(f"✅ Gemini Q{question.id} → {score}/{max_marks}")
#                 return {"score": score, "feedback": parsed.get("feedback", ""), "low_confidence": False}
#         except Exception as e:
#             print(f"⚠️ Gemini failed for Q{question.id}: {e} — using local model")

#     return _grade_structured_local(question, answer_text)


# # ═══════════════════════════════════════════════════════════════════════════════
# #  HELPERS
# # ═══════════════════════════════════════════════════════════════════════════════

# def fmt_date(dt):
#     if not dt: return None
#     if hasattr(dt, 'year') and dt.year < 2000: return None
#     if isinstance(dt, str): return dt
#     return dt.strftime("%Y-%m-%d %H:%M:%S")


# def _fmt_submission(sub: models.ExamSubmission) -> dict:
#     if not sub: return None
#     return {
#         "id":          sub.id,
#         "status":      sub.status,
#         "total_score": sub.total_score,
#         "submitted_at":fmt_date(sub.submitted_at),
#         "graded_at":   fmt_date(sub.graded_at),
#     }


# # ═══════════════════════════════════════════════════════════════════════════════
# #  GET /api/student/exams
# # ═══════════════════════════════════════════════════════════════════════════════

# @router.get("/exams")
# def get_student_exams(ctx: dict = Depends(require_student)):
#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     enrolled_class_ids = [
#         e.class_id
#         for e in db.query(models.ClassEnrollment)
#                    .filter(models.ClassEnrollment.student_id == user.id)
#                    .all()
#     ]

#     if not enrolled_class_ids:
#         return {"success": True, "exams": []}

#     exams = (
#         db.query(models.Exam)
#         .filter(
#             models.Exam.class_id.in_(enrolled_class_ids),
#             models.Exam.is_active == True,
#         )
#         .order_by(models.Exam.due_date.asc())
#         .all()
#     )

#     result = []
#     for exam in exams:
#         questions = (
#             db.query(models.ExamQuestion)
#             .filter_by(exam_id=exam.id)
#             .order_by(models.ExamQuestion.order_index)
#             .all()
#         )
#         submission  = db.query(models.ExamSubmission).filter_by(exam_id=exam.id, student_id=user.id).first()
#         total_marks = sum(q.marks for q in questions)

#         result.append({
#             "id":           exam.id,
#             "title":        exam.title,
#             "description":  exam.description,
#             "instructions": exam.instructions,
#             "due_date":     fmt_date(exam.due_date),
#             "time_limit":   exam.time_limit,
#             "total_marks":  total_marks,
#             "questions": [
#                 {
#                     "id":      q.id,
#                     "type":    q.type,
#                     "prompt":  q.prompt,
#                     "marks":   q.marks,
#                     "options": json.loads(q.options) if q.options else None,
#                     # correct_option and marking_guide intentionally hidden from student
#                 }
#                 for q in questions
#             ],
#             "my_submission": _fmt_submission(submission),
#         })

#     return {"success": True, "exams": result}


# # ═══════════════════════════════════════════════════════════════════════════════
# #  POST /api/student/exams/submit
# #  MCQ        → auto-graded instantly (correct_option comparison)
# #  Structured → AI-graded instantly   (Gemini → local model fallback)
# # ═══════════════════════════════════════════════════════════════════════════════

# @router.post("/exams/submit")
# def submit_exam(
#     body: SubmitExamRequest,
#     x_csrf_token: Optional[str] = Header(default=None),
#     ctx: dict = Depends(require_student),
# ):
#     user: models.User           = ctx["user"]
#     session: models.UserSession = ctx["session"]
#     db: Session                 = ctx["db"]

#     validate_csrf(session, x_csrf_token, body.csrf_token)

#     # ── Validate exam ──────────────────────────────────────────────────────────
#     exam = db.query(models.Exam).filter_by(id=body.exam_id, is_active=True).first()
#     if not exam:
#         raise HTTPException(status_code=404, detail="Exam not found.")

#     if not db.query(models.ClassEnrollment).filter_by(class_id=exam.class_id, student_id=user.id).first():
#         raise HTTPException(status_code=403, detail="You are not enrolled in this class.")

#     # ── Deadline ───────────────────────────────────────────────────────────────
#     now = datetime.now(timezone.utc)
#     due = exam.due_date
#     if due.tzinfo is None:
#         due = due.replace(tzinfo=BLANTYRE)
#     if now > due:
#         raise HTTPException(status_code=422, detail="This exam is past its due date.")

#     # ── Duplicate submission ───────────────────────────────────────────────────
#     if db.query(models.ExamSubmission).filter_by(exam_id=exam.id, student_id=user.id).first():
#         raise HTTPException(status_code=409, detail="You have already submitted this exam.")

#     # ── Load questions ─────────────────────────────────────────────────────────
#     questions  = db.query(models.ExamQuestion).filter_by(exam_id=exam.id).all()
#     answer_map = {a.question_id: a for a in body.answers}

#     # ── Create submission row (flush to get ID) ────────────────────────────────
#     exam_submission = models.ExamSubmission(
#         exam_id    = exam.id,
#         student_id = user.id,
#         status     = "submitted",
#     )
#     db.add(exam_submission)
#     db.flush()

#     # ── Grade each question ────────────────────────────────────────────────────
#     total_score = 0
#     has_pending = False   # True only if AI completely failed on a structured answer

#     for question in questions:
#         student_ans = answer_map.get(question.id)

#         # ── MCQ: instant correct/wrong ────────────────────────────────────────
#         if question.type == "mcq":
#             selected      = student_ans.selected_option if student_ans else None
#             is_correct    = bool(selected and selected == question.correct_option)
#             score_awarded = question.marks if is_correct else 0
#             total_score  += score_awarded

#             db.add(models.ExamAnswer(
#                 submission_id   = exam_submission.id,
#                 question_id     = question.id,
#                 selected_option = selected,
#                 is_correct      = is_correct,
#                 score_awarded   = score_awarded,
#             ))

#         # ── Structured: AI graded ─────────────────────────────────────────────
#         elif question.type == "structured":
#             answer_text = (
#                 student_ans.answer_text.strip()
#                 if student_ans and student_ans.answer_text
#                 else ""
#             )

#             ai_score    = None
#             ai_feedback = None

#             if answer_text:
#                 try:
#                     graded      = _grade_structured_with_ai(question, answer_text)
#                     ai_score    = graded["score"]
#                     ai_feedback = graded["feedback"]
#                     total_score += ai_score
#                     print(f"✅ Structured Q{question.id} graded → {ai_score}/{question.marks}")
#                 except Exception as e:
#                     print(f"❌ AI grading failed Q{question.id}: {e} — pending teacher review")
#                     has_pending = True

#             db.add(models.ExamAnswer(
#                 submission_id = exam_submission.id,
#                 question_id   = question.id,
#                 answer_text   = answer_text,
#                 score_awarded = ai_score,
#                 ai_feedback   = ai_feedback,
#             ))

#     # ── Set final status ───────────────────────────────────────────────────────
#     if has_pending:
#         # AI failed on at least one question — teacher must review
#         exam_submission.total_score = None
#         exam_submission.status      = "submitted"
#     else:
#         exam_submission.total_score = total_score
#         exam_submission.status      = "graded"
#         exam_submission.graded_at   = datetime.now(timezone.utc)

#     db.commit()
#     db.refresh(exam_submission)

#     return {
#         "success": True,
#         "message": (
#             "Exam submitted and graded!"
#             if not has_pending
#             else "Exam submitted. Some answers are pending teacher review."
#         ),
#         "submission": _fmt_submission(exam_submission),
#     }


# # ═══════════════════════════════════════════════════════════════════════════════
# #  GET /api/student/exams/results
# # ═══════════════════════════════════════════════════════════════════════════════

# @router.get("/exams/results")
# def get_exam_results(ctx: dict = Depends(require_student)):
#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     submissions = (
#         db.query(models.ExamSubmission)
#         .filter_by(student_id=user.id)
#         .order_by(models.ExamSubmission.submitted_at.desc())
#         .all()
#     )

#     results = []
#     for sub in submissions:
#         exam = db.query(models.Exam).filter_by(id=sub.exam_id).first()
#         if not exam:
#             continue

#         questions   = (
#             db.query(models.ExamQuestion)
#             .filter_by(exam_id=exam.id)
#             .order_by(models.ExamQuestion.order_index)
#             .all()
#         )
#         q_map       = {q.id: q for q in questions}
#         total_marks = sum(q.marks for q in questions)
#         answers     = db.query(models.ExamAnswer).filter_by(submission_id=sub.id).all()

#         answer_details = []
#         for ans in answers:
#             q = q_map.get(ans.question_id)
#             if not q:
#                 continue
#             detail = {
#                 "question_id":   q.id,
#                 "type":          q.type,
#                 "prompt":        q.prompt,
#                 "marks":         q.marks,
#                 "score_awarded": ans.score_awarded,
#                 "ai_feedback":   ans.ai_feedback,
#             }
#             if q.type == "mcq":
#                 detail["selected_option"] = ans.selected_option
#                 detail["correct_option"]  = q.correct_option
#                 detail["is_correct"]      = ans.is_correct
#                 detail["options"]         = json.loads(q.options) if q.options else None
#             else:
#                 detail["answer_text"] = ans.answer_text

#             answer_details.append(detail)

#         results.append({
#             "submission_id": sub.id,
#             "exam_id":       exam.id,
#             "exam_title":    exam.title,
#             "total_marks":   total_marks,
#             "total_score":   sub.total_score,
#             "status":        sub.status,
#             "submitted_at":  fmt_date(sub.submitted_at),
#             "graded_at":     fmt_date(sub.graded_at),
#             "answers":       answer_details,
#         })

#     return {"success": True, "results": results}







"""
student_exams.py
================
ENTRY POINT ONLY — wires exam sub-routers together.
Do NOT add endpoint logic here.

  List exams / results  →  routes/exam_routes.py
  Submit exam           →  routes/exam_submit.py
  AI grading logic      →  routes/exam_grader.py
"""

from fastapi import APIRouter
from routes.exam_routes import router as exam_routes_router
from routes.exam_submit import router as exam_submit_router

router = APIRouter()
router.include_router(exam_routes_router)
router.include_router(exam_submit_router)