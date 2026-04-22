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
print(f"🔑 GEMINI_API_KEY loaded: {'YES' if GEMINI_API_KEY else 'NO - KEY IS MISSING'}")
print(f"🔑 HF_API_KEY loaded: {'YES' if HF_API_KEY else 'NO - KEY IS MISSING'}")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

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
    model        = get_local_model()
    title        = (assignment.title or "").strip()
    instructions = (assignment.instructions or "").strip()
    ref_material = (assignment.reference_material or "")[:1500].strip()

    refs = [
        f"{title}. {instructions}",
        f"{title}. {title}. {instructions}",
        f"{instructions} {ref_material}" if ref_material else instructions,
    ]
    anchored_essay   = f"{title}. {essay_text[:2500]}"
    ref_embeddings   = model.encode(refs, convert_to_tensor=True)
    essay_embedding  = model.encode([anchored_essay], convert_to_tensor=True)
    scores           = st_util.cos_sim(essay_embedding, ref_embeddings)[0]
    raw_similarity   = float(scores.max().item())

    low, high = 0.30, 0.70
    scaled    = max(0.0, min(1.0, (raw_similarity - low) / (high - low)))

    max_score      = assignment.max_score or 100
    confidence_pct = raw_similarity * 100

    expected_words = 150
    wc_ratio  = min(word_count / expected_words, 1.0) if word_count > 0 else 0.5
    wc_factor = 0.4 + (0.6 * wc_ratio)

    base_score     = scaled * max_score * wc_factor
    off_topic      = confidence_pct < 30
    low_confidence = confidence_pct < 35

    if off_topic:
        final_score = min(base_score, max_score * 0.05)
    elif low_confidence:
        final_score = base_score * 0.75
    else:
        final_score = base_score

    final_score = round(max(0, min(final_score, max_score)))

    print(f"🖥️  Local model → similarity={confidence_pct:.1f}% | score={final_score}/{max_score}")

    return {
        "score":          final_score,
        "max_score":      max_score,
        "feedback":       (
            f"Score: {final_score}/{max_score}. "
            f"{'Essay appears off-topic.' if off_topic else 'Low confidence — flagged for teacher review.' if low_confidence else 'Graded successfully.'}"
        ),
        "ai_detected":    False,
        "off_topic":      off_topic,
        "low_confidence": low_confidence,
        "graded_by":      "local_model",
    }


# ── Main grading dispatcher ───────────────────────────────────────────────────
#
# CURRENT MODE: local model only (Gemini + HuggingFace disabled)
# To switch to full pipeline: replace this function with the commented version below.

# the local model starts here 


# def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
#     if assignment and essay_text:
#         print("🖥️  Using local model...")
#         return grade_with_local_model(assignment, essay_text, word_count)
#     raise Exception("No grading method available.")

# the local model block ends here  





#api keys starts here 


def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
    from routes.grading_prompt import parse_ai_response
    max_score = assignment.max_score if assignment else 100

    # ── 1. Try Gemini first ───────────────────────────────────────────────────
    if GEMINI_API_KEY:
        try:
            print("🤖 Trying Gemini...")
            raw    = call_gemini(prompt)
            parsed = parse_ai_response(raw, max_score)
            parsed.setdefault("low_confidence", False)
            parsed.setdefault("graded_by", "gemini")
            return parsed
        except http_requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 429:
                print("⏳ Gemini rate limited — retrying in 12s...")
                time.sleep(12)
                try:
                    raw    = call_gemini(prompt)
                    parsed = parse_ai_response(raw, max_score)
                    parsed.setdefault("low_confidence", False)
                    parsed.setdefault("graded_by", "gemini")
                    return parsed
                except Exception as retry_err:
                    print(f"⚠️ Gemini retry failed: {retry_err}")
            else:
                print(f"⚠️ Gemini HTTP error: {e}")
        except Exception as e:
            print(f"⚠️ Gemini failed: {e}")

    # ── 2. Fall back to HuggingFace ───────────────────────────────────────────
    if HF_API_KEY:
        try:
            print("🤖 Trying HuggingFace...")
            raw    = call_huggingface(prompt)
            parsed = parse_ai_response(raw, max_score)
            parsed.setdefault("low_confidence", False)
            parsed.setdefault("graded_by", "huggingface")
            return parsed
        except Exception as e:
            print(f"⚠️ HuggingFace failed: {e}")

    # ── 3. Last resort: local model ───────────────────────────────────────────
    if assignment and essay_text:
        print("🖥️  Using local model as fallback...")
        return grade_with_local_model(assignment, essay_text, word_count)

    raise Exception("All grading methods exhausted.")





# api keys ends here 

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