"""
ai_grader.py
============
ALL AI GRADING LOGIC LIVES HERE.

To switch AI model         → edit grade_with_ai()
To add/remove HF models    → edit HF_MODELS list
To tune local model        → edit grade_with_local_model()
To re-enable Gemini/HF     → uncomment the full grade_with_ai() at the bottom
"""

import os
import time
import requests as http_requests
from sentence_transformers import SentenceTransformer, util as st_util

# ── API keys (set in your .env) ───────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HF_API_KEY     = os.getenv("HF_API_KEY", "")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

# ── Local model path ──────────────────────────────────────────────────────────
LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "essay-grader-finetuned")

# Similarity below this = low confidence → flagged for teacher review
CONFIDENCE_THRESHOLD = 0.60

_local_model = None  # lazy-loaded on first use


# ── HuggingFace fallback models ───────────────────────────────────────────────
HF_MODELS = [
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


# ── Gemini caller ─────────────────────────────────────────────────────────────

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


# ── HuggingFace caller ────────────────────────────────────────────────────────

def call_huggingface(prompt: str) -> str:
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
            print(f"⚠️ {model['name']} returned empty — trying next...")
        except Exception as e:
            print(f"⚠️ {model['name']} failed: {e} — trying next...")
            last_error = e
    raise Exception(f"All HuggingFace models failed. Last error: {last_error}")


# ── Local model ───────────────────────────────────────────────────────────────

def get_local_model():
    """Load local SentenceTransformer from disk (cached after first load)."""
    global _local_model
    if _local_model is None:
        print("📦 Loading local model from disk...")
        _local_model = SentenceTransformer(LOCAL_MODEL_PATH)
        print("✅ Local model ready")
    return _local_model


def grade_with_local_model(assignment, essay_text: str, word_count: int = 0) -> dict:
    max_score = assignment.max_score or 100

    # Simple word-count based scoring as last resort
    if word_count >= 400:
        score = round(max_score * 0.70)
        feedback = "Essay submitted successfully. Awaiting teacher review for final grade."
    elif word_count >= 200:
        score = round(max_score * 0.55)
        feedback = "Essay is somewhat short. Consider expanding your arguments. Awaiting teacher review."
    elif word_count >= 50:
        score = round(max_score * 0.35)
        feedback = "Essay is too short. Please expand your response. Awaiting teacher review."
    else:
        score = 0
        feedback = "Essay does not meet minimum length requirements."

    return {
        "score":          score,
        "feedback":       feedback,
        "ai_detected":    False,
        "off_topic":      False,
        "low_confidence": True,
        "graded_by":      "basic_fallback",
    }


# ── Main grading dispatcher ───────────────────────────────────────────────────
#
# CURRENT MODE: local model only (Gemini + HuggingFace disabled)
# To switch to full pipeline: replace this function with the commented version below.

def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
    # ── Step 1: Gemini ────────────────────────────────────────────────────────
    if GEMINI_API_KEY:
        try:
            print("🤖 Trying Gemini for grading...")
            raw    = call_gemini(prompt)
            parsed = parse_ai_response(raw, assignment.max_score if assignment else 100)
            parsed.setdefault("low_confidence", False)
            parsed.setdefault("graded_by", "gemini")
            print(f"✅ Gemini graded: {parsed.get('score')}")
            return parsed
        except http_requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                print("⏳ Gemini rate limit — waiting 15s then retrying...")
                time.sleep(15)
                try:
                    raw    = call_gemini(prompt)
                    parsed = parse_ai_response(raw, assignment.max_score if assignment else 100)
                    parsed.setdefault("low_confidence", False)
                    parsed.setdefault("graded_by", "gemini")
                    return parsed
                except Exception as retry_err:
                    print(f"⚠️ Gemini retry failed: {retry_err} — trying local model...")
            else:
                print(f"⚠️ Gemini HTTP error: {e} — trying local model...")
        except Exception as e:
            print(f"⚠️ Gemini failed: {e} — trying local model...")

    # ── Step 2: Local model fallback ──────────────────────────────────────────
    if assignment and essay_text:
        print("🖥️  Falling back to local model...")
        return grade_with_local_model(assignment, essay_text, word_count)

    raise Exception("All grading methods exhausted.")


# ── Full pipeline (Gemini → HuggingFace → Local) — uncomment to enable ───────
#
# def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
#     from routes.grading_prompt import parse_ai_response
#     max_score = assignment.max_score if assignment else 100
#
#     if GEMINI_API_KEY:
#         try:
#             print("🤖 Trying Gemini...")
#             raw    = call_gemini(prompt)
#             parsed = parse_ai_response(raw, max_score)
#             parsed.setdefault("low_confidence", False)
#             parsed.setdefault("graded_by", "gemini")
#             return parsed
#         except http_requests.exceptions.HTTPError as e:
#             if e.response and e.response.status_code == 429:
#                 print("⏳ Gemini rate limited — retrying in 12s...")
#                 time.sleep(12)
#                 try:
#                     raw    = call_gemini(prompt)
#                     parsed = parse_ai_response(raw, max_score)
#                     parsed.setdefault("low_confidence", False)
#                     parsed.setdefault("graded_by", "gemini")
#                     return parsed
#                 except Exception as retry_err:
#                     print(f"⚠️ Gemini retry failed: {retry_err}")
#             else:
#                 print(f"⚠️ Gemini HTTP error: {e}")
#         except Exception as e:
#             print(f"⚠️ Gemini failed: {e}")
#
#     if HF_API_KEY:
#         try:
#             raw    = call_huggingface(prompt)
#             parsed = parse_ai_response(raw, max_score)
#             parsed.setdefault("low_confidence", False)
#             parsed.setdefault("graded_by", "huggingface")
#             return parsed
#         except Exception as e:
#             print(f"⚠️ HuggingFace failed: {e}")
#
#     if assignment and essay_text:
#         print("🖥️  Using local model as fallback...")
#         return grade_with_local_model(assignment, essay_text, word_count)
#
#     raise Exception("All grading methods exhausted.")