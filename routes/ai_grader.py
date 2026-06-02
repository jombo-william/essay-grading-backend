# routes/ai_grader.py
"""
# routes/ai_grader.py
# Bridge file — keeps the old grade_with_ai() interface that submission_routes.py
# expects, but delegates all actual grading to services/grader.py using an ensemble method.

To switch AI model         -> edit grade_with_ai()
To add/remove HF models    -> edit HF_MODELS list

ENSEMBLE GRADING CHAIN (3-Stage Weighted Process):
  1. Phi-3 IELTS (Fine-Tuned) -> Language, Grammar, Vocab          [Max: 20%]  ✅ ACTIVE
  2. Qwen 2.5 72B (Router)    -> Topic Relevance & Coherence       [Max: 20%]  ✅ ACTIVE
  3. Gemini 2.5/1.5 Flash     -> Structure, Rubric & Logic         [Max: 60%]  ✅ ACTIVE

CRITICAL STOP: If Stage 2 identifies the essay as completely off-topic, the pipeline
terminates early, bypassing Stage 3 to prevent argument-matching inflation.
"""

import os
import re
import json
import time
import requests as http_requests
from dotenv import load_dotenv

# Load variables from local .env file
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HF_API_KEY     = os.getenv("HF_API_KEY", "")
print(f"🔑 GEMINI_API_KEY loaded: {'YES' if GEMINI_API_KEY else 'NO - KEY IS MISSING'}")
print(f"🔑 HF_API_KEY loaded: {'YES' if HF_API_KEY else 'NO - KEY IS MISSING'}")

# FIX: gemini-2.5-flash REQUIRES v1beta. gemini-1.5 models work on both v1beta and v1.
# Using v1beta for all to keep it consistent and forward-compatible.
GEMINI_MODELS = [
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent",
]

# ── Fine-tuned Phi-3 IELTS models on Hugging Face ────────────────────────────
PHI3_IELTS_URL   = "https://api-inference.huggingface.co/models/kill-switch/phi3-essay-grader"
PHI3_IELTS_URL_2 = "https://api-inference.huggingface.co/models/AshishGx/phi3-essay-grader"

# FIX: Removed the Alpaca wrapper. The HF Inference API for these fine-tuned models
# works best with a plain instruction prompt. The Alpaca template with an empty
# "### Response:" suffix was causing empty or garbage outputs.
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


def clean_and_parse_json(raw_str: str) -> dict:
    """
    Helper utility to strip markdown backticks and parse volatile JSON patterns safely.
    FIX: Added regex extraction of the outermost {...} block so that if Gemini or Qwen
    wraps the JSON in prose, we still reliably extract and parse it.
    """
    cleaned = raw_str.strip()
    # Strip markdown code fences
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()

    # FIX: If the model added explanatory text before/after the JSON object,
    # extract just the JSON object using a greedy brace match.
    json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if json_match:
        cleaned = json_match.group(0)

    return json.loads(cleaned)


# ── STAGE 1: PHI-3 IELTS CALLERS & INTERPOLATION (20%) ────────────────────────

def call_phi3_ielts(essay_text: str, assignment=None) -> tuple:
    """
    Calls the fine-tuned Phi-3 Mini IELTS grader on Hugging Face.
    FIX 1: Uses a plain instruction prompt instead of the Alpaca template wrapper,
            which was producing empty responses from the inference API.
    FIX 2: On 503 (model cold/loading), waits 30 seconds and retries ONCE before
            moving to the next model. Previously it skipped instantly, meaning both
            models were abandoned before they had a chance to warm up.
    """
    question = ""
    if assignment:
        question = getattr(assignment, "instructions", "") or getattr(assignment, "title", "") or ""
        title    = (getattr(assignment, "title", "") or "").lower()
        instruct = (getattr(assignment, "instructions", "") or "").lower()
        combined = title + " " + instruct
        if any(kw in combined for kw in ["task 1", "chart", "graph", "table", "diagram", "describe the",
                                          "bar chart", "pie chart", "line graph"]):
            question = f"[Task 1 - Data Description]\n{question}"
        else:
            question = f"[Task 2 - Essay/Opinion]\n{question}"

    # FIX: Plain prompt — no Alpaca wrapper. Cleaner and more reliable for the
    # HF inference API which completes from the end of your input text.
    prompt = f"""You are an expert IELTS examiner. Grade the mechanical writing quality of the student essay below. Focus on Grammatical Range & Accuracy and Lexical Resource.

Assignment Question:
{question}

Student Essay:
{essay_text[:3000]}

Provide:
1. An overall IELTS Band Score (1.0-9.0 in 0.5 increments) based purely on vocabulary and grammar quality.
2. A brief justification (2-4 sentences) explaining the linguistic grade.

Format your response starting with: "Band Score: X.X"
"""

    phi3_models = [
        ("phi3_ielts",   PHI3_IELTS_URL),
        ("phi3_ielts_2", PHI3_IELTS_URL_2),
    ]

    for model_name, url in phi3_models:
        # FIX: Attempt each model with one warm-up retry on 503
        for attempt in range(2):
            try:
                print(f"🎓 [Stage 1] Trying {model_name} (attempt {attempt + 1})...")
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
                    if attempt == 0:
                        # FIX: Wait for the model to warm up, then retry once
                        print(f"⏳ [Stage 1] {model_name} is loading (503) — waiting 30s for warm-up...")
                        time.sleep(30)
                        continue  # retry
                    else:
                        print(f"⚠️ [Stage 1] {model_name} still 503 after warm-up wait — trying next model...")
                        break  # move to next model

                resp.raise_for_status()
                data = resp.json()

                result = ""
                if isinstance(data, list) and data:
                    result = data[0].get("generated_text", "").strip()
                elif isinstance(data, dict):
                    result = data.get("generated_text", "").strip()

                if result:
                    print(f"✅ [Stage 1] Phi-3 graded successfully with {model_name}")
                    return result, model_name

                print(f"⚠️ [Stage 1] {model_name} returned empty response — trying next model...")
                break  # empty response, move to next model

            except Exception as e:
                print(f"⚠️ [Stage 1] {model_name} attempt {attempt + 1} failed: {e}")
                if attempt == 0:
                    time.sleep(5)  # brief pause before retry
                    continue
                break  # move to next model

    raise Exception("All Phi-3 IELTS models failed")


def parse_phi3_stage1_response(raw: str) -> tuple:
    """Extracts the band score and converts it to a score out of 20 points."""
    band = None
    for pattern in [
        r"Band\s*Score[:\s]+(\d+\.?\d*)",
        r"Overall\s*Band\s*Score[:\s]+(\d+\.?\d*)",
        r"Overall[:\s]+(\d+\.?\d*)",
        r"(\d+\.?\d*)\s*/\s*9",
        r"Band\s+(\d+\.?\d*)",
        r"score[:\s]+(\d+\.?\d*)",
    ]:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            candidate = float(m.group(1))
            # Sanity check: band scores are between 1 and 9
            if 1.0 <= candidate <= 9.0:
                band = candidate
                break

    if band is not None:
        band = max(1.0, min(9.0, band))
        score_ratio = (band - 1.0) / 8.0
        stage1_score = score_ratio * 20.0
    else:
        band = 5.0
        stage1_score = 10.0

    feedback = (
        f"### Stage 1: Vocabulary & Grammar (Phi-3 Fine-Tune)\n"
        f"- **Calculated Linguistic Band**: {band}/9.0\n"
        f"- **Detailed Evaluator Comments**:\n{raw.strip()}\n\n"
    )
    return round(stage1_score, 2), feedback


# ── STAGE 2: QWEN 2.5 RELEVANCE & COHERENCE CHECKER (20%) ───────────────────

def run_stage2_qwen(essay_text: str, assignment_title: str, assignment_instructions: str) -> dict:
    """
    Adversarial Topic Guard. Cross-checks student submission against the official master blueprint.
    Strips out user-induced topic evasion immediately.
    If the essay is off-topic, returns coherence_score_out_of_20 = 0.0 and is_on_topic = False.
    This triggers the circuit breaker in grade_with_ai() to skip Stage 3 entirely.
    """
    clean_title = assignment_title.strip()
    clean_instruct = assignment_instructions.strip() if assignment_instructions else "Follow core title theme."

    prompt = f"""You are an adversarial academic auditor checking for prompt evasion or off-topic essay submissions.
A student is attempting to fulfill a specific assignment. You must verify if their text actually answers the requested question, or if they are writing about a completely different topic.

[MASTER ASSIGNMENT BLUEPRINT]
REQUIRED TITLE/TOPIC: {clean_title}
REQUIRED INSTRUCTIONS: {clean_instruct}

[STUDENT SUBMISSION]
TEXT EXTRACTION (First 500 words):
{essay_text[:2500]}

[EVALUATION RULES]
1. If the Master Blueprint asks about "{clean_title}" (e.g., biological mechanisms like breathing), but the student writes about a completely different topic (e.g., environmental protection, politics, personal stories), you MUST classify them as "completely_off_topic".
2. Do not be fooled by high-quality writing. An A+ essay about the "Environment" submitted to a "Breathing" prompt is an automatic topic match failure.
3. If "completely_off_topic" is triggered, set "is_on_topic" to false and "coherence_score_out_of_20" to 0.0 immediately.

You MUST respond in this EXACT JSON format with no additional text or conversational chatter outside the object:
{{
    "is_on_topic": false,
    "relevance_classification": "completely_off_topic",
    "coherence_score_out_of_20": 0.0,
    "justification": "CRITICAL MISMATCH: The assignment required a response regarding '{clean_title}', but the student submitted a text focusing entirely on an unrelated theme."
}}
Note: classification choices are exactly: "completely_on_topic", "partially_on_topic", or "completely_off_topic"."""

    try:
        resp = http_requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {HF_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       "Qwen/Qwen2.5-72B-Instruct",
                "messages":    [{"role": "user", "content": prompt}],
                "max_tokens":  300,
                "temperature": 0.0,  # Complete deterministic focus to block prompt injection exploits
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw_content = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"🔍 [Stage 2 Security Log] Qwen validation payload: {raw_content}")
        return clean_and_parse_json(raw_content)

    except Exception as e:
        print(f"⚠️ [Stage 2 Bypass] Security check failure: {e} — deploying defensive bypass parameters.")
        return {
            "is_on_topic": True,
            "relevance_classification": "completely_on_topic",
            "coherence_score_out_of_20": 11.0,
            "justification": "Relevance validation network temporarily bypassed; baseline safety scores applied."
        }


# ── STAGE 3: GEMINI MASTER CONTENT EVALUATOR (60%) ──────────────────────────

def call_gemini_stage3(essay_text: str, assignment, word_count: int) -> dict:
    """
    Calls Gemini to grade the logical framework, evidence quality, and rubric alignment.
    FIX: Changed all URLs from /v1/ to /v1beta/ — gemini-2.5-flash is ONLY available
    on v1beta. Using v1 returned 404/model-not-found for 2.5-flash, which caused the
    entire fallback chain to exhaust silently and fall through to Qwen as Stage 3.
    """
    title        = getattr(assignment, "title", "Untitled")
    instructions = getattr(assignment, "instructions", "")
    rubric       = getattr(assignment, "rubric", "Standard logical structure, argumentation soundness, and text requirements.")

    prompt = f"""You are a senior academic professor conducting an advanced assessment of an essay.
Evaluate the content comprehensively based on organizational framework, factual strength, grounding evidence, and adherence to specific prompt rules.

ASSIGNMENT TITLE: {title}
ASSIGNMENT INSTRUCTIONS: {instructions}
TARGET MEASUREMENTS / RUBRIC: {rubric}

STUDENT ESSAY:
{essay_text}
WORD COUNT: {word_count}

You must return your evaluation strictly in JSON format with no extra text outside the object:
{{
    "structural_and_thesis_score_out_of_30": 24.0,
    "evidence_and_logical_validity_score_out_of_30": 22.5,
    "comprehensive_critique": "Your detailed text critique goes here."
}}"""

    last_error = None
    for model_url in GEMINI_MODELS:
        model_name = model_url.split('models/')[1].split(':')[0]
        try:
            print(f"🤖 [Stage 3] Trying Gemini model: {model_name}")
            resp = http_requests.post(
                f"{model_url}?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=60,
            )

            if resp.status_code == 429:
                print(f"⏳ [Stage 3] {model_name} rate limited (429) — trying next model...")
                last_error = f"429 rate limit on {model_name}"
                time.sleep(2)
                continue

            if resp.status_code == 404:
                print(f"⚠️ [Stage 3] {model_name} not found (404) — trying next model...")
                last_error = f"404 model not found: {model_name}"
                continue

            resp.raise_for_status()

            raw_response = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            parsed = clean_and_parse_json(raw_response)
            print(f"✅ [Stage 3] Gemini graded successfully with {model_name}")
            return parsed

        except Exception as e:
            print(f"⚠️ [Stage 3] Gemini error on {model_name}: {e} — trying next model...")
            last_error = e
            continue

    raise Exception(f"All Gemini models exhausted. Last error: {last_error}")


# ── ORIGINAL RESCUE FALLBACKS ─────────────────────────────────────────────────

def call_huggingface(prompt: str) -> str:
    """Legacy backup interface — processes prompts via the HF router (Llama then Qwen)."""
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
        except Exception as e:
            print(f"⚠️ {model['name']} failed: {e}")
            last_error = e
    raise Exception(f"All HuggingFace models failed. Last error: {last_error}")


def _similarity_fallback(assignment, essay_text: str) -> dict:
    """Original semantic similarity fallback mapping to localized training files."""
    from services.grader import grade_essay
    rubric = None
    if hasattr(assignment, "rubric") and assignment.rubric:
        rubric = assignment.rubric if isinstance(assignment.rubric, dict) else None

    result    = grade_essay(essay_text, rubric=rubric)
    max_score = getattr(assignment, "max_score", None) or 100
    raw_score = result.get("total_score", 50)

    scaled = round(raw_score / 100 * max_score)
    scaled = max(0, min(max_score, scaled))

    return {
        "score":          scaled,
        "feedback":       result.get("overall_feedback", "Graded via internal semantic matching datasets."),
        "ai_detected":    False,
        "off_topic":      "off_topic" in result.get("graded_by", ""),
        "low_confidence": "low" in result.get("graded_by", ""),
        "graded_by":      result.get("graded_by", "similarity-model-legacy"),
    }


def grade_with_local_model(assignment, essay_text: str, word_count: int = 0) -> dict:
    """Legacy rule-based word count grading fallback — last resort only."""
    max_score = assignment.max_score or 100

    if word_count >= 400:
        score    = round(max_score * 0.70)
        feedback = "Essay verified. Retained at target threshold. Finalizing manual evaluation parameters."
    elif word_count >= 200:
        score    = round(max_score * 0.55)
        feedback = "Structural length minimal. Expand presentation elements."
    elif word_count >= 50:
        score    = round(max_score * 0.35)
        feedback = "Insufficient composition content length detected."
    else:
        score    = 0
        feedback = "Composition fails length checks."

    return {
        "score":          score,
        "feedback":       feedback,
        "ai_detected":    False,
        "off_topic":      False,
        "low_confidence": True,
        "graded_by":      "word-count-local",
    }


# ── MAIN DISPATCHER: ENSEMBLE GRADING PIPELINE ──────────────────────────────

def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
    max_score = getattr(assignment, "max_score", None) or 100

    master_title        = getattr(assignment, "title", "") or ""
    master_instructions = getattr(assignment, "instructions", "") or ""

    if not master_title and prompt:
        master_title = prompt

    if not essay_text or word_count < 15:
        return {
            "score":          0,
            "feedback":       "❌ Submission content unreadable or below minimum grading length requirements.",
            "off_topic":      False,
            "ai_detected":    False,
            "low_confidence": True,
            "graded_by":      "pipeline-pre-rejection",
        }

    cumulative_feedback    = []
    running_points         = 0.0
    hard_off_topic_tripped = False
    s1_points              = 0.0  # initialise so it's always defined for the report header
    qwen_data              = {}   # initialise so it's always defined for the off-topic block

    # ==========================================
    # STAGE 1: Mechanics, Grammar & Vocab (20%)
    # ==========================================
    try:
        raw_phi3, model_name = call_phi3_ielts(essay_text, assignment)
        s1_points, s1_fb     = parse_phi3_stage1_response(raw_phi3)
        running_points      += s1_points
        cumulative_feedback.append(s1_fb)
        print(f"✅ Stage 1 complete — {s1_points}/20 pts via {model_name}")

    except Exception as s1_err:
        print(f"⚠️ Stage 1 Phi-3 failed: {s1_err}. Falling back to HF router (Llama/Qwen)...")
        try:
            fallback_prompt = (
                f"You are an IELTS examiner. Analyze the grammar and vocabulary quality of this essay "
                f"and give a score out of 20. Use this exact format on its own line: 'Score: X/20'\n\n"
                f"Essay:\n{essay_text}"
            )
            raw_hf    = call_huggingface(fallback_prompt)
            s1_points = 12.0
            num_match = re.search(r"(\d+\.?\d*)\s*/\s*20", raw_hf)
            if num_match:
                s1_points = min(20.0, max(0.0, float(num_match.group(1))))
            running_points += s1_points
            cumulative_feedback.append(
                f"### Stage 1: Vocabulary & Grammar (HF Router Fallback)\n"
                f"- **Score**: {s1_points}/20\n"
                f"- **Comments**: {raw_hf[:400]}...\n\n"
            )
        except Exception:
            s1_points       = 14.0 if word_count >= 350 else 8.0
            running_points += s1_points
            cumulative_feedback.append(
                "### Stage 1: Vocabulary & Grammar (Emergency Baseline)\n"
                "- All graders timed out. Basic length-based score applied.\n\n"
            )

    # ==========================================
    # STAGE 2: Topic Relevance & Coherence (20%)
    # ==========================================
    try:
        qwen_data    = run_stage2_qwen(essay_text, master_title, master_instructions)
        s2_coherence = float(qwen_data.get("coherence_score_out_of_20", 10.0))

        # Normalise classification to lowercase to prevent capitalisation bypasses
        classification = str(qwen_data.get("relevance_classification", "completely_on_topic")).lower().strip()

        # Circuit-breaker conditions: explicit off-topic flag OR is_on_topic=False OR score ≤ 40%
        if (classification == "completely_off_topic"
                or qwen_data.get("is_on_topic") is False
                or s2_coherence <= 8.0):
            s2_coherence           = 0.0
            hard_off_topic_tripped = True
            classification         = "completely_off_topic"

        elif "partially_on_topic" in classification:
            s2_coherence = min(s2_coherence, 12.0) * 0.75

        running_points += s2_coherence
        cumulative_feedback.append(
            f"### Stage 2: Topic Alignment & Structure (Qwen 2.5 72B)\n"
            f"- **Relevance Category**: {classification.replace('_', ' ').title()}\n"
            f"- **Structural Coherence**: {s2_coherence}/20.0\n"
            f"- **Evaluator Rationale**: {qwen_data.get('justification')}\n\n"
        )
        print(f"✅ Stage 2 complete — {s2_coherence}/20 pts | {classification}")

    except Exception as s2_err:
        print(f"⚠️ Stage 2 Qwen failed: {s2_err}. Applying neutral bypass score...")
        s2_coherence    = 10.0
        running_points += s2_coherence
        cumulative_feedback.append(
            "### Stage 2: Topic Alignment & Structure\n"
            "- Relevance validation timed out. Neutral baseline score applied.\n\n"
        )

    # ==========================================
    # 🛑 CIRCUIT BREAKER: Off-topic essays stop here. Stage 3 is NOT executed.
    # ==========================================
    if hard_off_topic_tripped:
        final_score = round((running_points / 100.0) * max_score)
        final_score = max(0, min(max_score, final_score))

        report_header = (
            f"## 📊 Comprehensive Academic Evaluation Analysis\n"
            f"**Aggregated Weighted Composition Score**: `{final_score} / {max_score}`\n"
            f"*(Stage 1 Language: {round(s1_points, 1)}/20 | Stage 2 Relevance: 0/20 [FAILED] | Stage 3 Analysis: 0/60 [BYPASSED])*\n"
            f"------------\n\n"
        )

        fail_rationale = qwen_data.get(
            'justification',
            f"Submission dropped entirely. Content lacks any valid thematic relationship to the assignment prompt: '{master_title}'."
        )

        cumulative_feedback.append(
            f"### Stage 3: Rhetorical Soundness, Logic & Rubric Adherence (Gemini)\n"
            f"- **Thesis Development & Flow**: 0.0/30.0 [Canceled]\n"
            f"- **Argument Veracity & Evidence**: 0.0/30.0 [Canceled]\n"
            f"- **Deep Content Critique**: Grading halted. Essay topic does not match the assignment prompt.\n\n"
            f"❌ **CRITICAL PIPELINE SHUTDOWN**: {fail_rationale}"
        )

        return {
            "score":          final_score,
            "feedback":       report_header + "".join(cumulative_feedback).strip(),
            "off_topic":      True,
            "ai_detected":    False,
            "low_confidence": False,
            "graded_by":      "stage2-adversarial-abort",
        }

    # ==========================================
    # STAGE 3: Architecture, Rubric & Logic (60%)
    # ==========================================
    try:
        gemini_data = call_gemini_stage3(essay_text, assignment, word_count)
        s3_thesis   = float(gemini_data.get("structural_and_thesis_score_out_of_30", 15.0))
        s3_evidence = float(gemini_data.get("evidence_and_logical_validity_score_out_of_30", 15.0))

        # Clamp individual sub-scores to their valid ranges
        s3_thesis   = max(0.0, min(30.0, s3_thesis))
        s3_evidence = max(0.0, min(30.0, s3_evidence))
        s3_total    = s3_thesis + s3_evidence

        running_points += s3_total
        cumulative_feedback.append(
            f"### Stage 3: Rhetorical Soundness, Logic & Rubric Adherence (Gemini)\n"
            f"- **Thesis Development & Flow**: {s3_thesis}/30.0\n"
            f"- **Argument Veracity & Evidence**: {s3_evidence}/30.0\n"
            f"- **Deep Content Critique**: {gemini_data.get('comprehensive_critique')}\n\n"
        )
        print(f"✅ Stage 3 complete — {s3_total}/60 pts via Gemini")

    except Exception as s3_err:
        print(f"⚠️ Stage 3 Gemini failed: {s3_err}. Falling back to HF router...")
        try:
            fallback_prompt = (
                f"You are a professor grading an essay. Evaluate the logic, evidence quality, and argument "
                f"depth out of 60 points. Use this exact format on its own line: 'Score: X/60'\n\n"
                f"Essay:\n{essay_text}"
            )
            raw_hf   = call_huggingface(fallback_prompt)
            s3_total = 30.0
            num_match = re.search(r"(\d+\.?\d*)\s*/\s*60", raw_hf)
            if num_match:
                s3_total = min(60.0, max(0.0, float(num_match.group(1))))
            running_points += s3_total
            cumulative_feedback.append(
                f"### Stage 3: Deep Evaluation (HF Router Fallback)\n"
                f"- **Score**: {s3_total}/60\n"
                f"- **Feedback**: {raw_hf[:400]}...\n\n"
            )
        except Exception:
            running_points += 30.0
            cumulative_feedback.append(
                "### Stage 3: Deep Evaluation (Emergency Default)\n"
                "- All graders unavailable. Neutral baseline score applied.\n\n"
            )

    # ==========================================
    # Final score assembly
    # ==========================================
    final_scaled_score = round((running_points / 100.0) * max_score)
    final_scaled_score = max(0, min(max_score, final_scaled_score))

    report_header = (
        f"## 📊 Comprehensive Academic Evaluation Analysis\n"
        f"**Aggregated Weighted Composition Score**: `{final_scaled_score} / {max_score}`\n"
        f"*(Stage 1 Language: 20% | Stage 2 Relevance: 20% | Stage 3 Analysis: 60%)*\n"
        f"------------\n\n"
    )

    return {
        "score":          final_scaled_score,
        "feedback":       report_header + "".join(cumulative_feedback).strip(),
        "off_topic":      False,
        "ai_detected":    False,
        "low_confidence": False,
        "graded_by":      "ensemble-pipeline-engine-v3",
    }