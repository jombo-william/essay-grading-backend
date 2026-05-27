"""
# routes/ai_grader.py
# Bridge file — keeps the old grade_with_ai() interface that submission_routes.py
# expects, but delegates all actual grading to services/grader.py
To switch AI model         → edit grade_with_ai()
To add/remove HF models    → edit HF_MODELS list
To tune local model        → edit grade_with_local_model()
To tune Phi-3 IELTS model  → edit call_phi3_ielts()

GRADING CHAIN (in order):
  1. Gemini          → primary (best quality)
  2. Phi-3 IELTS     → your fine-tuned model (YOUR MODEL 🎓)
  3. HuggingFace     → Llama / Qwen / Mistral fallbacks
  4. Local fallback  → word-count basic scorer
"""

import os
import time
import requests as http_requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HF_API_KEY     = os.getenv("HF_API_KEY", "")
print(f"🔑 GEMINI_API_KEY loaded: {'YES' if GEMINI_API_KEY else 'NO - KEY IS MISSING'}")
print(f"🔑 HF_API_KEY loaded: {'YES' if HF_API_KEY else 'NO - KEY IS MISSING'}")

GEMINI_MODELS = [
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent",
]

# ── Your fine-tuned Phi-3 IELTS model on Hugging Face ────────────────────────
PHI3_IELTS_URL   = "https://api-inference.huggingface.co/models/kill-switch/phi3-essay-grader"
PHI3_IELTS_URL_2 = "https://api-inference.huggingface.co/models/AshishGx/phi3-essay-grader"

ALPACA_PROMPT = """Below is an instruction that describes a task. Write a response that appropriately completes the request.

### Instruction:
{}

### Input:
{}

### Response:
{}"""

# ── Local model path ──────────────────────────────────────────────────────────
LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "essay-grader-finetuned")

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
]


def call_gemini(prompt: str) -> str:
    last_error = None
    for model_url in GEMINI_MODELS:
        try:
            resp = http_requests.post(
                f"{model_url}?key={GEMINI_API_KEY}",
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
            if resp.status_code == 429:
                print(f"⏳ {model_url.split('models/')[1].split(':')[0]} rate limited — trying next...")
                last_error = resp
                continue
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except http_requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 429:
                print(f"⏳ Rate limited — trying next Gemini model...")
                last_error = e
                continue
            raise
    raise http_requests.exceptions.HTTPError("All Gemini models rate limited") if last_error else Exception("All Gemini models failed")


# ── Phi-3 IELTS callers ───────────────────────────────────────────────────────

def call_phi3_ielts(essay_text: str, assignment=None) -> str:
    """
    Calls your fine-tuned Phi-3 Mini IELTS grader on Hugging Face.
    Uses the same Alpaca prompt format it was trained on.
    Tries multiple model endpoints in sequence.
    """
    task_type  = 2
    task_label = "Task 2 (Essay/Opinion)"
    question   = ""

    if assignment:
        question = getattr(assignment, "instructions", "") or getattr(assignment, "title", "") or ""
        title    = (getattr(assignment, "title", "") or "").lower()
        instruct = (getattr(assignment, "instructions", "") or "").lower()
        combined = title + " " + instruct
        if any(kw in combined for kw in ["task 1", "chart", "graph", "table", "diagram", "describe the",
                                          "bar chart", "pie chart", "line graph"]):
            task_type  = 1
            task_label = "Task 1 (Data Description)"

    instruction = f"""You are an expert IELTS examiner. Grade the following IELTS Writing {task_label} essay.

Question:
{question}

Essay:
{essay_text[:3000]}

Provide:
1. An overall IELTS Band Score (1.0-9.0 in 0.5 increments)
2. Sub-scores for: Task Response, Coherence & Cohesion, Lexical Resource, Grammatical Range & Accuracy
3. A brief justification (2-4 sentences) explaining the grade"""

    prompt = ALPACA_PROMPT.format(instruction, "", "")

    urls_to_try = [
        ("kill-switch/phi3-essay-grader", PHI3_IELTS_URL),
        ("AshishGx/phi3-essay-grader",    PHI3_IELTS_URL_2),
    ]

    for model_name, url in urls_to_try:
        try:
            print(f"🎓 Trying Phi-3 IELTS grader: {model_name}...")
            resp = http_requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {HF_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens":     512,
                        "temperature":        0.1,
                        "repetition_penalty": 1.1,
                        "return_full_text":   False,
                    },
                },
                timeout=120,
            )

            if resp.status_code == 503:
                print(f"⏳ {model_name} is loading (503) — trying next model...")
                continue

            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, list) and data:
                result = data[0].get("generated_text", "").strip()
                if result:
                    print(f"✅ Phi-3 IELTS graded successfully with {model_name}")
                    return result
            if isinstance(data, dict):
                result = data.get("generated_text", "").strip()
                if result:
                    print(f"✅ Phi-3 IELTS graded successfully with {model_name}")
                    return result
            print(f"⚠️ {model_name} returned empty — trying next model...")
        except Exception as e:
            print(f"⚠️ {model_name} failed: {e} — trying next model...")
            continue

    raise Exception("All Phi-3 IELTS models failed")


def parse_phi3_response(raw: str, max_score: int) -> dict:
    """
    Converts the Phi-3 free-text IELTS response into the same dict
    format that parse_ai_response() returns.

    Band 9 → 100% of max_score
    Band 1 →  11% of max_score
    Linear interpolation in between.
    """
    import re

    band = None
    for pattern in [
        r"Overall\s*Band\s*Score[:\s]+(\d+\.?\d*)",
        r"Band\s*Score[:\s]+(\d+\.?\d*)",
        r"Overall[:\s]+(\d+\.?\d*)",
        r"(\d+\.?\d*)\s*/\s*9",
        r"Band\s+(\d+\.?\d*)",
    ]:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            band = float(m.group(1))
            break

    if band is not None:
        band        = max(1.0, min(9.0, band))
        score_ratio = (band - 1.0) / 8.0
        score       = round(score_ratio * max_score)
    else:
        score = round(max_score * 0.50)

    score = max(0, min(max_score, score))

    feedback_lines = ["🎓 IELTS Grader (Phi-3)\n"]
    if band:
        feedback_lines.append(f"Overall Band Score: {band}/9.0")
    feedback_lines.append("")
    feedback_lines.append(raw.strip())
    feedback_lines.append(f"\n📊 Converted to project score: {score}/{max_score}")

    return {
        "score":          score,
        "feedback":       "\n".join(feedback_lines).strip(),
        "off_topic":      False,
        "ai_detected":    False,
        "low_confidence": band is None,
        "graded_by":      "phi3_ielts",
    }


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
                    "Content-Type":  "application/json",
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


def _similarity_fallback(assignment, essay_text: str) -> dict:
    """Use the similarity grader from services/grader.py — always returns real scores."""
    from services.grader import grade_essay
    rubric = None
    if hasattr(assignment, "rubric") and assignment.rubric:
        rubric = assignment.rubric if isinstance(assignment.rubric, dict) else None

    result    = grade_essay(essay_text, rubric=rubric)
    max_score = getattr(assignment, "max_score", None) or 100
    raw_score = result.get("total_score", 50)

    # Scale 0-100 → 0-max_score
    scaled = round(raw_score / 100 * max_score)
    scaled = max(0, min(max_score, scaled))

    # BUG FIX: was missing the return statement entirely
    return {
        "score":          scaled,
        "feedback":       result.get("overall_feedback", "Graded automatically."),
        "ai_detected":    False,
        "off_topic":      "off_topic" in result.get("graded_by", ""),
        "low_confidence": "low" in result.get("graded_by", ""),
        "graded_by":      result.get("graded_by", "similarity-model"),
    }


def grade_with_local_model(assignment, essay_text: str, word_count: int = 0) -> dict:
    # BUG FIX: this function was mixing in variables (scaled, result) from
    # _similarity_fallback that don't exist in this scope. Corrected to use
    # only local word_count logic as intended.
    max_score = assignment.max_score or 100

    if word_count >= 400:
        score    = round(max_score * 0.70)
        feedback = "Essay submitted successfully. Awaiting teacher review for final grade."
    elif word_count >= 200:
        score    = round(max_score * 0.55)
        feedback = "Essay is somewhat short. Consider expanding your arguments. Awaiting teacher review."
    elif word_count >= 50:
        score    = round(max_score * 0.35)
        feedback = "Essay is too short. Please expand your response. Awaiting teacher review."
    else:
        score    = 0
        feedback = "Essay does not meet minimum length requirements."

    return {
        "score":          score,
        "feedback":       feedback,
        "ai_detected":    False,
        "off_topic":      False,
        "low_confidence": True,
        "graded_by":      "word-count-local",
    }


def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
    """
    Main grading dispatcher.
    Fallback chain:
      1. Gemini 2.5-flash
      2. Phi-3 IELTS fine-tuned models
      3. HuggingFace (Llama / Qwen — only accepted if score > 0)
      4. Similarity model (training_data.csv) — always gives real scores
      5. Word-count estimate (absolute last resort)
    """
    from routes.grading_prompt import build_grading_prompt, parse_ai_response
    max_score = getattr(assignment, "max_score", None) or 100

    # ── 1. Gemini ──────────────────────────────────────────────────────────────
    if GEMINI_API_KEY and assignment and essay_text:
        try:
            print("🤖 Trying Gemini...")
            built_prompt = build_grading_prompt(assignment, essay_text, word_count)
            # BUG FIX: was calling call_gemini(built_prompt) three times in a row,
            # discarding the first two results. Now called exactly once.
            raw    = call_gemini(built_prompt)
            print(f"🔍 GEMINI RAW: {raw[:300]}")
            parsed = parse_ai_response(raw, max_score)
            print(f"🔍 PARSED: score={parsed.get('score')} off_topic={parsed.get('off_topic')} relevance={parsed.get('relevance_label')}")
            parsed.setdefault("low_confidence", False)
            parsed.setdefault("graded_by", "gemini")
            if parsed.get("score", 0) > 0 or parsed.get("off_topic"):
                print(f"✅ Gemini graded — score: {parsed.get('score')}")
                return parsed
            print("⚠️ Gemini returned score=0 — trying fallback...")
        except http_requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 429:
                print("⏳ Gemini rate limited — retrying in 12s...")
                time.sleep(12)
                try:
                    built_prompt = build_grading_prompt(assignment, essay_text, word_count)
                    raw          = call_gemini(built_prompt)
                    parsed       = parse_ai_response(raw, max_score)
                    parsed.setdefault("low_confidence", False)
                    parsed.setdefault("graded_by", "gemini")
                    if parsed.get("score", 0) > 0 or parsed.get("off_topic"):
                        return parsed
                except Exception as retry_err:
                    print(f"⚠️ Gemini retry failed: {retry_err}")
            else:
                print(f"⚠️ Gemini HTTP error: {e}")
        except Exception as e:
            print(f"⚠️ Gemini failed: {e}")

    # ── 2. Phi-3 IELTS fine-tuned models ──────────────────────────────────────
    if HF_API_KEY and essay_text:
        try:
            print("🎓 Trying Phi-3 IELTS graders (multiple models)...")
            raw = call_phi3_ielts(essay_text, assignment)
            if raw:
                parsed = parse_phi3_response(raw, max_score)
                print(f"✅ Phi-3 IELTS graded successfully → {parsed['score']}/{max_score} [{parsed.get('graded_by')}]")
                return parsed
            else:
                print("⚠️ Phi-3 IELTS returned empty — trying HuggingFace fallbacks...")
        except Exception as e:
            print(f"⚠️ Phi-3 IELTS failed: {e} — trying HuggingFace fallbacks...")

    # ── 3. Generic HuggingFace models ─────────────────────────────────────────
    if HF_API_KEY and assignment and essay_text:
        try:
            print("🤖 Trying HuggingFace fallback models...")
            built_prompt = build_grading_prompt(assignment, essay_text, word_count)
            raw          = call_huggingface(built_prompt)
            parsed       = parse_ai_response(raw, max_score)
            parsed.setdefault("low_confidence", False)
            parsed.setdefault("graded_by", "huggingface")
            if parsed.get("score", 0) > 0 or parsed.get("off_topic"):
                parsed["score"] = min(parsed.get("score", 0), round(max_score * 0.65))
                print(f"✅ HuggingFace graded — score: {parsed.get('score')} (capped at 65%)")
                return parsed
            else:
                print("⚠️ HuggingFace returned score=0 — not trustworthy, falling through...")
        except Exception as e:
            print(f"⚠️ HuggingFace failed: {e}")

    # ── 4. Similarity model ────────────────────────────────────────────────────
    if assignment and essay_text:
        print("🤖 Using similarity model (training_data.csv)...")
        try:
            result = _similarity_fallback(assignment, essay_text)
            print(f"✅ Similarity model graded — score: {result['score']}/{max_score}")
            return result
        except Exception as e:
            print(f"⚠️ Similarity model failed: {e}")

    # ── 5. Word-count estimate (absolute last resort) ──────────────────────────
    print("🖥️  Falling back to word-count estimate...")
    if word_count >= 400:
        score    = round(max_score * 0.70)
        feedback = "Essay submitted. Awaiting teacher review for final grade."
    elif word_count >= 200:
        score    = round(max_score * 0.55)
        feedback = "Essay is somewhat short. Consider expanding your arguments."
    elif word_count >= 50:
        score    = round(max_score * 0.35)
        feedback = "Essay is too short. Please expand your response."
    else:
        score    = 5
        feedback = "Essay does not meet minimum length requirements."

    return {
        "score":          score,
        "feedback":       feedback,
        "ai_detected":    False,
        "off_topic":      False,
        "low_confidence": True,
        "graded_by":      "word-count-fallback",
    }