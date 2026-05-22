"""
exam_grader.py
==============
ALL EXAM GRADING LOGIC LIVES HERE.

To switch AI model for exams     → edit grade_structured_answer()
To tune local model scoring      → edit _grade_structured_local()
To re-enable Gemini for exams    → set GEMINI_API_KEY in .env
"""

import os
import re
import json
import requests as http_requests
from sentence_transformers import SentenceTransformer, util as st_util

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "essay-grader-finetuned")
_local_model = None


def get_local_model() -> SentenceTransformer:
    global _local_model
    if _local_model is None:
        print("📦 Loading local exam grading model...")
        _local_model = SentenceTransformer(LOCAL_MODEL_PATH)
        print("✅ Local model ready")
    return _local_model


def _grade_structured_local(question, answer_text: str) -> dict:
    """
    Grade a single structured exam answer.

    KEY FIX: Compare student answer against the MARKING GUIDE (not the question).
    This means similarity measures closeness to the EXPECTED answer, not just topic.

    To tune:
      low / high           → similarity range mapped to 0-max_marks
      off_topic_thresh     → % below which answer gets near-zero
      low_conf_thresh      → % below which score is reduced
      expected_words       → word count target (raise = harsher on short answers)
      min_wc_factor        → minimum multiplier for very short answers
    """
    model     = get_local_model()
    max_marks = question.marks or 1
    prompt    = (question.prompt        or "").strip()
    guide     = (question.marking_guide or "").strip()

    # Compare against MARKING GUIDE as primary reference
    refs = [
        guide if guide else prompt,
        f"{prompt} {guide}" if guide else prompt,
        f"{guide} {guide}" if guide else prompt,  # double-weight the guide
    ]
    # Student answer compared directly — no question prefix
    ref_emb    = model.encode(refs,                    convert_to_tensor=True)
    ans_emb    = model.encode([answer_text[:2000]],    convert_to_tensor=True)
    similarity = float(st_util.cos_sim(ans_emb, ref_emb)[0].max().item())

    # Tighter range than essay grader
    low,  high       = 0.55, 0.78
    off_topic_thresh = 30
    low_conf_thresh  = 58

    # Word count differentiates levels strongly
    # Level 1 ~ 30w, Level 2 ~ 70w, Level 3 ~ 110w, Level 4 ~ 150w
    expected_words = 100
    min_wc_factor  = 0.15   # very short answers get at most 15% of marks

    confidence_pct = similarity * 100
    scaled         = max(0.0, min(1.0, (similarity - low) / (high - low)))

    word_count = len(answer_text.split())
    wc_ratio   = min(word_count / expected_words, 1.0)
    wc_factor  = min_wc_factor + ((1.0 - min_wc_factor) * wc_ratio)

    off_topic = confidence_pct < off_topic_thresh
    low_conf  = confidence_pct < low_conf_thresh

    base_score = scaled * max_marks * wc_factor

    if off_topic:
        final_score = min(base_score, max_marks * 0.05)
    elif low_conf:
        final_score = base_score * 0.55
    else:
        final_score = base_score

    final_score = max(0, min(round(final_score), max_marks))

    print(f"🖥️  Local Q{question.id} → sim={confidence_pct:.1f}% | wc={word_count}w | wc_f={wc_factor:.2f} | {final_score}/{max_marks}")

    if off_topic:
        feedback = f"Score: {final_score}/{max_marks}. Your answer does not appear to address the question."
    elif low_conf:
        feedback = f"Score: {final_score}/{max_marks}. Answer is too brief or lacks depth — flagged for teacher review."
    else:
        feedback = f"Score: {final_score}/{max_marks}. Graded by AI."

    return {
        "score":          final_score,
        "feedback":       feedback,
        "low_confidence": low_conf,
        "off_topic":      off_topic,
        "graded_by":      "local_model",
    }


def _call_gemini_exam(question, answer_text: str) -> dict:
    max_marks = question.marks or 1
    prompt = (
        f"You are a strict exam marker.\n\n"
        f"QUESTION: {question.prompt}\n"
        f"MARKING GUIDE: {question.marking_guide or 'Grade on relevance and correctness.'}\n"
        f"MAX MARKS: {max_marks}\n\n"
        f"STUDENT ANSWER:\n{answer_text[:2000]}\n\n"
        f"Reply ONLY with this JSON (no extra text):\n"
        f'{{ "score": <int 0-{max_marks}>, "feedback": "brief specific feedback" }}'
    )
    resp = http_requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 500},
        },
        timeout=60,
    )
    resp.raise_for_status()
    raw   = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    clean = raw.strip().replace("```json", "").replace("```", "").strip()
    m     = re.search(r'\{.*\}', clean, re.DOTALL)
    if not m:
        raise ValueError("No JSON found in Gemini response")
    parsed = json.loads(m.group())
    score  = max(0, min(max_marks, int(parsed.get("score", 0))))
    print(f"✅ Gemini Q{question.id} → {score}/{max_marks}")
    return {
        "score":          score,
        "feedback":       parsed.get("feedback", ""),
        "low_confidence": False,
        "off_topic":      False,
        "graded_by":      "gemini",
    }


# CURRENT MODE: local model only
# To enable Gemini + local fallback: comment this out and uncomment below

def grade_structured_answer(question, answer_text: str) -> dict:
    if not answer_text or not answer_text.strip():
        return {
            "score":          0,
            "feedback":       "No answer provided.",
            "low_confidence": False,
            "off_topic":      True,
            "graded_by":      "local_model",
        }
    return _grade_structured_local(question, answer_text)


# Full pipeline (Gemini -> Local fallback) - uncomment to enable:
#
# def grade_structured_answer(question, answer_text: str) -> dict:
#     if not answer_text or not answer_text.strip():
#         return {"score": 0, "feedback": "No answer provided.",
#                 "low_confidence": False, "off_topic": True, "graded_by": "local_model"}
#     if GEMINI_API_KEY:
#         try:
#             return _call_gemini_exam(question, answer_text)
#         except Exception as e:
#             print(f"⚠️ Gemini failed for Q{question.id}: {e} — falling back to local model")
#     return _grade_structured_local(question, answer_text)