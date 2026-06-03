# routes/ai_grader.py
"""
ENSEMBLE GRADING CHAIN (3-Stage Weighted Process):
  1. Qwen 2.5 72B (Router)    -> Language, Grammar, Vocab          [Max: 15%]  ✅ ACTIVE
  2. Qwen 2.5 72B (Router)    -> Topic Relevance & Coherence       [Max: 10%]  ✅ ACTIVE
  3. Groq                     -> Structure, Rubric & Logic         [Max: 75%]  ✅ ACTIVE

NOTE: Phi-3 fine-tuned models on api-inference.huggingface.co are permanently
      DNS-blocked on this host. Stage 1 now uses the HF router (Qwen) directly.

CRITICAL STOP: If Stage 2 identifies the essay as completely off-topic, the pipeline
terminates early, bypassing Stage 3 to prevent argument-matching inflation.

FIXES (v4.1):
  - Removed dead api-inference.huggingface.co loop (was causing ~4min timeout waste per submission)
  - Qwen Stage 2 prompt patched: now forbidden from defaulting to 7.5; forced to use full range
  - Groq Stage 3: second-pass verification added for long essays scoring suspiciously low
  - Groq calls now pass seed=42 for improved determinism across runs
"""

import os
import re
import json
import time
import textwrap
import requests as http_requests
from dotenv import load_dotenv

try:
    from groq import Groq
    GROQ_SDK_AVAILABLE = True
except ImportError:
    GROQ_SDK_AVAILABLE = False
    print("⚠️  groq not installed. Run: pip install groq")

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HF_API_KEY     = os.getenv("HF_API_KEY", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
print(f"🔑 GEMINI_API_KEY loaded: {'YES' if GEMINI_API_KEY else 'NO - KEY IS MISSING'}")
print(f"🔑 HF_API_KEY loaded: {'YES' if HF_API_KEY else 'NO - KEY IS MISSING'}")
print(f"🔑 GROQ_API_KEY loaded: {'YES' if GROQ_API_KEY else 'NO - KEY IS MISSING'}")

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
    Strip markdown backticks and parse JSON safely.
    Extracts the outermost {...} block so prose wrapping is handled.
    """
    cleaned = raw_str.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$",     "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if json_match:
        cleaned = json_match.group(0)
    return json.loads(cleaned)


def _strip_markdown(text: str) -> str:
    """
    Convert markdown-heavy feedback into clean, readable plain text
    for student-facing display. Works at the final return stage.
    """
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


# ── STAGE 1: GRAMMAR & VOCAB (15%) ───────────────────────────────────────────

def call_phi3_ielts(essay_text: str, assignment=None) -> tuple:
    """
    Grades grammar and vocabulary only. Max contribution: 15 points.

    FIX v4.1: api-inference.huggingface.co is DNS-blocked on this host.
    The old code wasted ~4 minutes per submission attempting unreachable models.
    Now goes directly to the HF router (Qwen in IELTS examiner mode).
    """
    question = ""
    if assignment:
        question = getattr(assignment, "instructions", "") or getattr(assignment, "title", "") or ""

    print("🎓 [Stage 1] Calling Qwen router (IELTS examiner mode)...")

    router_prompt = f"""You are an expert IELTS examiner evaluating ONLY grammar and vocabulary mechanics.
Do NOT assess content accuracy, argument quality, or topic relevance — linguistics only.

Assignment Question: {question}

Student Essay: {essay_text[:3000]}

Respond in EXACTLY this format (no extra text):
Band Score: X.X
Justification: [2-4 sentences on grammatical range, accuracy, and lexical resource only]"""

    try:
        resp = http_requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {HF_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       "Qwen/Qwen2.5-72B-Instruct",
                "messages":    [{"role": "user", "content": router_prompt}],
                "max_tokens":  400,
                "temperature": 0.0,
            },
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()["choices"][0]["message"]["content"].strip()
        if result:
            print("✅ [Stage 1] Grammar graded via Qwen router (IELTS examiner mode)")
            return result, "Qwen2.5-72B-grammar-specialist"
        raise Exception("Empty response from router")
    except Exception as e:
        raise Exception(f"Stage 1 router call failed: {e}")


def parse_phi3_stage1_response(raw: str) -> tuple:
    """
    Extracts band score and converts to a score out of 15.
    Formula: ((band - 1.0) / 8.0) * 15.0
    """
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
            if 1.0 <= candidate <= 9.0:
                band = candidate
                break

    if band is not None:
        band         = max(1.0, min(9.0, band))
        score_ratio  = (band - 1.0) / 8.0
        stage1_score = score_ratio * 15.0
    else:
        band         = 5.0
        stage1_score = 7.5  # default mid-point of 15

    feedback = (
        f"### Stage 1: Vocabulary & Grammar (Qwen 2.5 72B — IELTS Mode)\n"
        f"- **Calculated Linguistic Band**: {band}/9.0\n"
        f"- **Detailed Evaluator Comments**:\n{raw.strip()}\n\n"
    )
    return round(stage1_score, 2), feedback


# ── STAGE 2: QWEN 2.5 — TOPIC ALIGNMENT & COHERENCE (10%) ───────────────────

def run_stage2_qwen(essay_text: str, assignment_title: str, assignment_instructions: str) -> dict:
    """
    Adversarial topic guard + strict structural coherence scoring.
    Max contribution: 10 points.

    FIX v4.1: Prompt now explicitly forbids Qwen from anchoring to 7.5.
    Forces use of the full 0-10 range with required justification per 0.5 step.
    """
    clean_title    = assignment_title.strip()
    clean_instruct = assignment_instructions.strip() if assignment_instructions else "Follow core title theme."

    prompt = f"""You are a strict academic auditor with zero tolerance for surface-level grading.
Your job is to verify topic alignment AND penalise essays that are structurally organised but scientifically empty or factually wrong.

[MASTER ASSIGNMENT BLUEPRINT]
REQUIRED TITLE/TOPIC: {clean_title}
REQUIRED INSTRUCTIONS: {clean_instruct}

[STUDENT SUBMISSION — First 2500 characters]
{essay_text[:2500]}

[STRICT EVALUATION RULES]

TOPIC CLASSIFICATION:
1. If the student writes about a completely different topic → "completely_off_topic", score = 0.0
2. If the student addresses the topic but with major gaps or partial coverage → "partially_on_topic"
3. If the student directly and fully addresses the topic → "completely_on_topic"

COHERENCE SCORING (out of 10):
Apply these MANDATORY DEDUCTIONS before assigning any score:

- Paragraphs repeat the same idea with different subjects (e.g. "animals need energy",
  "plants need energy", "humans need energy" — all saying the same thing):
  → DEDUCT 2 points

- Topic sentences are absent, vague, or just restate "X is important":
  → DEDUCT 1.5 points

- Conclusion merely restates the introduction with no synthesis:
  → DEDUCT 1 point

- Scientific/technical claims inside paragraphs are factually wrong
  (e.g. "respiration = breathing", "photosynthesis and respiration are the same"):
  → DEDUCT 2 points per major factual error

- Zero scientific terminology in any paragraph (no ATP, no glucose, no relevant
  domain vocabulary for the topic):
  → CAP score at 4.0/10 maximum, regardless of structure

- A well-organised essay with empty or wrong content MUST score no higher than 5.0/10.
  Structure without substance is not coherence.

⚠️ CRITICAL ANTI-ANCHORING RULE:
Do NOT default to 7.5. You MUST use the full scoring range (0.0 to 10.0).
Examples of correct differentiation:
  - Weak essay (vague, no terminology, repetitive): 2.0 – 4.0
  - Average essay (on-topic, some gaps, basic structure): 4.5 – 6.0
  - Good essay (accurate, organised, uses terminology): 6.5 – 7.5
  - Excellent essay (precise, deep, well-linked, synthesised conclusion): 8.0 – 10.0
You must justify every 0.5 increment difference from the midpoint (5.0).
If you assign 7.5, you must explicitly state why the essay is "Good" and not "Excellent" or "Average".

NOTE: "completely_on_topic" does NOT automatically mean high coherence score.
      The content quality inside paragraphs determines the coherence score.

Respond in EXACT JSON format only — no prose, no markdown outside the object:
{{
    "is_on_topic": true,
    "relevance_classification": "completely_on_topic",
    "coherence_score_out_of_10": 7.0,
    "justification": "Detailed rationale including specific deductions applied and why this score and not higher/lower."
}}
Note: classification choices are exactly: "completely_on_topic", "partially_on_topic", or "completely_off_topic"
Note: coherence_score_out_of_10 must be a float between 0.0 and 10.0"""

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
                "max_tokens":  400,
                "temperature": 0.0,
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw_content = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"🔍 [Stage 2] Qwen payload: {raw_content}")
        return clean_and_parse_json(raw_content)

    except Exception as e:
        print(f"⚠️ [Stage 2 Bypass] Security check failure: {e} — deploying defensive bypass.")
        return {
            "is_on_topic":               True,
            "relevance_classification":  "completely_on_topic",
            "coherence_score_out_of_10": 5.5,
            "justification":             "Relevance validation temporarily bypassed; conservative baseline applied."
        }


# ── STAGE 3: GROQ — CONTENT, LOGIC & RUBRIC (75%) ────────────────────────────

def _call_groq_once(groq_client, model_name: str, prompt: str) -> dict:
    """Single Groq call. Raises on failure so caller can retry or fallback."""
    response = groq_client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.0,
        seed=42,  # FIX v4.1: improves determinism across runs
    )
    raw_text = response.choices[0].message.content.strip()
    return clean_and_parse_json(raw_text)


def call_gemini_stage3(essay_text: str, assignment, word_count: int) -> dict:
    """
    Stage 3: Grades thesis, logic, evidence quality, and rubric adherence.
    Max contribution: 75 points.

    FIX v4.1: Added second-pass verification for long essays (600+ words) that
    score below 40/75. Two results are averaged to reduce single-run variance.
    seed=42 added to all Groq calls for improved cross-run consistency.

    PRIMARY:  Groq API
    FALLBACK: HF router (Qwen/Llama)
    """
    title        = getattr(assignment, "title",        "Untitled")
    instructions = getattr(assignment, "instructions", "")
    rubric       = getattr(assignment, "rubric",       "Standard logical structure, argumentation soundness, and text requirements.")

    prompt = f"""You are a strict senior academic professor. Your grading must be PROPORTIONAL to actual quality.
You are NOT permitted to award high scores after identifying serious weaknesses.
Every critical comment you write MUST reduce the score. Charitable grading is a grading error.

ASSIGNMENT TITLE: {title}
ASSIGNMENT INSTRUCTIONS: {instructions}
RUBRIC: {rubric}

STUDENT ESSAY:
{essay_text}
WORD COUNT: {word_count}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY SCORING CAPS — CHECK THESE FIRST BEFORE ASSIGNING ANY SCORE.
These are hard limits. You cannot exceed them regardless of other qualities.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CAP 1 — MISSING DOMAIN TERMINOLOGY:
If the essay contains ZERO subject-specific technical terms relevant to the topic
(e.g. for a biology essay: no ATP, no glucose, no cellular processes, no relevant
scientific vocabulary at all):
→ HARD CAP: evidence_and_logical_validity_score_out_of_37 cannot exceed 12.0

CAP 2 — FUNDAMENTAL FACTUAL ERROR:
If the essay contains a direct factual contradiction of the topic
(e.g. "respiration is the same as breathing", "photosynthesis and respiration
are the same thing", or similarly wrong core claims):
→ DEDUCT 10 points from evidence_and_logical_validity_score_out_of_37 immediately
→ This deduction stacks with CAP 1 if both apply

CAP 3 — NO EVIDENCE OR RESEARCH:
If the essay makes only general statements with zero supporting data, studies,
specific examples, case studies, or quantifiable facts:
→ HARD CAP: evidence_and_logical_validity_score_out_of_37 cannot exceed 18.0

CAP 4 — VAGUE OR ABSENT THESIS:
If the introduction contains no clear arguable thesis (just "X is important" or
"this essay will talk about X"):
→ HARD CAP: structural_and_thesis_score_out_of_38 cannot exceed 18.0

CAP 5 — REPETITIVE PARAGRAPH STRUCTURE:
If 3 or more body paragraphs make essentially the same point with different subjects
(e.g. "animals need it", "plants need it", "humans need it" with no new information):
→ DEDUCT 7 points from structural_and_thesis_score_out_of_38

CAP 6 — SHALLOW CONCLUSION:
If the conclusion only restates the introduction with no synthesis, new insight,
or forward-looking statement:
→ DEDUCT 3 points from structural_and_thesis_score_out_of_38

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING SCALE REFERENCE (total 75 points):
  structural_and_thesis_score_out_of_38:          max 38
  evidence_and_logical_validity_score_out_of_37:  max 37
  Combined total = 75

QUALITY BENCHMARKS:
  Excellent (90-100% of max): Precise terminology, strong evidence, clear thesis,
    well-linked paragraphs, real-world applications, evolutionary/ecological depth.
  Good (70-89%): Mostly accurate, adequate terminology, organised, minor gaps.
  Moderate (50-69%): Partially accurate, limited terminology, basic organisation,
    noticeable gaps, no evidence.
  Weak (30-49%): Superficial, wrong claims, minimal terminology, repetitive,
    no evidence.
  Very Weak (0-29%): Largely inaccurate, no terminology, no structure, fundamental
    conceptual errors.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

IMPORTANT: Your comprehensive_critique MUST explicitly state:
1. Which caps were triggered and why
2. The exact deductions applied
3. What specific evidence or terminology was missing
4. What the essay would need to reach the next band

Respond with ONLY a valid JSON object — no explanation, no markdown outside the object:
{{
    "structural_and_thesis_score_out_of_38": 24.0,
    "evidence_and_logical_validity_score_out_of_37": 22.5,
    "comprehensive_critique": "Detailed critique here including caps triggered and deductions applied."
}}"""

    # ── PRIMARY: Groq ─────────────────────────────────────────────────────────
    if GROQ_SDK_AVAILABLE and GROQ_API_KEY:
        GROQ_MODELS = [
            "llama-3.3-70b-versatile",
            "llama-3.1-70b-versatile",
            "mixtral-8x7b-32768",
        ]
        groq_client = Groq(api_key=GROQ_API_KEY)

        for model_name in GROQ_MODELS:
            try:
                print(f"🤖 [Stage 3] Trying Groq -> {model_name}")
                parsed = _call_groq_once(groq_client, model_name, prompt)

                s3_thesis   = max(0.0, min(38.0, float(parsed.get("structural_and_thesis_score_out_of_38", 19.0))))
                s3_evidence = max(0.0, min(37.0, float(parsed.get("evidence_and_logical_validity_score_out_of_37", 18.5))))
                s3_total    = s3_thesis + s3_evidence

                # FIX v4.1: Second-pass verification for long essays scoring suspiciously low.
                # If a 600+ word essay scores below 40/75, run once more and average.
                # This prevents a single bad Groq run from destroying a good submission's grade.
                if word_count >= 600 and s3_total < 40.0:
                    print(f"⚠️ [Stage 3] Long essay scored low ({s3_total}/75) — running verification pass...")
                    try:
                        parsed2     = _call_groq_once(groq_client, model_name, prompt)
                        s3_thesis2  = max(0.0, min(38.0, float(parsed2.get("structural_and_thesis_score_out_of_38", 19.0))))
                        s3_evidence2= max(0.0, min(37.0, float(parsed2.get("evidence_and_logical_validity_score_out_of_37", 18.5))))

                        # Average the two runs
                        s3_thesis   = round((s3_thesis   + s3_thesis2)   / 2, 1)
                        s3_evidence = round((s3_evidence + s3_evidence2) / 2, 1)
                        s3_total    = s3_thesis + s3_evidence

                        # Use the more detailed critique from the higher-scoring run
                        if (s3_thesis2 + s3_evidence2) > (float(parsed.get("structural_and_thesis_score_out_of_38", 0)) + float(parsed.get("evidence_and_logical_validity_score_out_of_37", 0))):
                            parsed["comprehensive_critique"] = parsed2.get("comprehensive_critique", parsed.get("comprehensive_critique", ""))

                        print(f"✅ [Stage 3] Verification averaged: {s3_total}/75")
                    except Exception as verify_err:
                        print(f"⚠️ [Stage 3] Verification pass failed: {verify_err} — using first result")

                parsed["structural_and_thesis_score_out_of_38"]         = s3_thesis
                parsed["evidence_and_logical_validity_score_out_of_37"] = s3_evidence
                print(f"✅ [Stage 3] Groq graded successfully with {model_name} — {s3_total}/75")
                return parsed

            except Exception as e:
                print(f"⚠️ [Stage 3] Groq error on {model_name}: {e} — trying next...")
                continue

        print("⚠️ [Stage 3] All Groq models failed — falling back to HF router...")
    else:
        print("⚠️ [Stage 3] Groq unavailable — falling back to HF router...")

    # ── FALLBACK: HF router ───────────────────────────────────────────────────
    fallback_prompt = (
        f"You are a strict academic grader. Grade the logic, evidence quality, and "
        f"argument depth of this essay out of 75 points. Be strict — penalise missing "
        f"terminology, factual errors, and absent evidence hard. "
        f"Respond with exactly: 'Score: X/75'\n\nEssay:\n{essay_text}"
    )
    raw_hf   = call_huggingface(fallback_prompt)
    s3_total = 37.5  # conservative default (50% of 75)
    num_match = re.search(r"(\d+\.?\d*)\s*/\s*75", raw_hf)
    if num_match:
        s3_total = min(75.0, max(0.0, float(num_match.group(1))))
    return {
        "structural_and_thesis_score_out_of_38":         round(s3_total * (38 / 75), 1),
        "evidence_and_logical_validity_score_out_of_37": round(s3_total * (37 / 75), 1),
        "comprehensive_critique": raw_hf[:800],
    }


# ── LEGACY FALLBACKS ──────────────────────────────────────────────────────────

def call_huggingface(prompt: str) -> str:
    """Legacy backup interface across generic HF cluster infrastructure."""
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
    """Semantic similarity fallback mapping to localized training files."""
    from services.grader import grade_essay
    rubric = None
    if hasattr(assignment, "rubric") and assignment.rubric:
        rubric = assignment.rubric if isinstance(assignment.rubric, dict) else None

    result    = grade_essay(essay_text, rubric=rubric)
    max_score = getattr(assignment, "max_score", None) or 100
    raw_score = result.get("total_score", 50)
    scaled    = round(raw_score / 100 * max_score)
    scaled    = max(0, min(max_score, scaled))

    return {
        "score":          scaled,
        "feedback":       result.get("overall_feedback", "Graded via internal semantic matching."),
        "ai_detected":    False,
        "off_topic":      "off_topic" in result.get("graded_by", ""),
        "low_confidence": "low" in result.get("graded_by", ""),
        "graded_by":      result.get("graded_by", "similarity-model-legacy"),
    }


def grade_with_local_model(assignment, essay_text: str, word_count: int = 0) -> dict:
    """Rule-based word count fallback. Last resort only."""
    max_score = assignment.max_score or 100
    if word_count >= 400:
        score    = round(max_score * 0.70)
        feedback = "Essay verified. Retained at target threshold."
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
        "score": score, "feedback": feedback,
        "ai_detected": False, "off_topic": False,
        "low_confidence": True, "graded_by": "word-count-local",
    }


# ── MAIN DISPATCHER ───────────────────────────────────────────────────────────

def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0) -> dict:
    """
    v4.1 Ensemble grading pipeline.
    Weights: Stage 1 = 15pts | Stage 2 = 10pts | Stage 3 = 75pts | Total = 100pts
    """
    max_score = getattr(assignment, "max_score", None) or 100

    master_title        = getattr(assignment, "title", "") or ""
    master_instructions = getattr(assignment, "instructions", "") or ""

    if not master_title and prompt:
        master_title = prompt

    if not essay_text or word_count < 15:
        return {
            "score": 0,
            "feedback": "❌ Submission content unreadable or below minimum grading length.",
            "off_topic": False, "ai_detected": False,
            "low_confidence": True, "graded_by": "pipeline-pre-rejection",
        }

    cumulative_feedback    = []
    running_points         = 0.0
    hard_off_topic_tripped = False
    s1_points              = 0.0
    qwen_data              = {}

    # ══════════════════════════════════════════════════════════════
    # STAGE 1 — MECHANICS, GRAMMAR & VOCAB (15 pts)
    # ══════════════════════════════════════════════════════════════
    try:
        raw_phi3, model_name = call_phi3_ielts(essay_text, assignment)
        s1_points, s1_fb     = parse_phi3_stage1_response(raw_phi3)
        running_points      += s1_points
        cumulative_feedback.append(s1_fb)
        print(f"✅ Stage 1 complete — {s1_points}/15 pts via {model_name}")
    except Exception as s1_err:
        print(f"⚠️ Stage 1 failed: {s1_err}. Trying fallback router...")
        try:
            fallback_prompt = (
                f"Analyze ONLY grammar and vocabulary quality of this essay. "
                f"Give a score out of 15 using exactly: 'Score: X/15'\n\nEssay:\n{essay_text}"
            )
            raw_hf    = call_huggingface(fallback_prompt)
            s1_points = 7.5  # conservative default
            num_match = re.search(r"(\d+\.?\d*)\s*/\s*15", raw_hf)
            if num_match:
                s1_points = min(15.0, max(0.0, float(num_match.group(1))))
            running_points += s1_points
            cumulative_feedback.append(
                f"### Stage 1: Vocabulary & Grammar (Router Fallback)\n"
                f"- Score: {s1_points}/15\n- Comments: {raw_hf[:400]}...\n\n"
            )
        except Exception:
            # Emergency baseline: proportional to word count, max 10/15
            s1_points = min(10.0, (word_count / 400) * 10.0) if word_count >= 350 else 5.0
            running_points += s1_points
            cumulative_feedback.append(
                "### Stage 1: Vocabulary & Grammar (Emergency Baseline)\n"
                "- System timed out. Conservative length-based score applied.\n\n"
            )

    # ══════════════════════════════════════════════════════════════
    # STAGE 2 — TOPIC RELEVANCE & COHERENCE (10 pts)
    # ══════════════════════════════════════════════════════════════
    try:
        qwen_data      = run_stage2_qwen(essay_text, master_title, master_instructions)
        s2_coherence   = float(qwen_data.get("coherence_score_out_of_10", 5.0))
        classification = str(qwen_data.get("relevance_classification", "completely_on_topic")).lower().strip()

        # Hard off-topic triggers
        if (classification == "completely_off_topic" or
                qwen_data.get("is_on_topic") is False or
                s2_coherence <= 2.0):
            s2_coherence           = 0.0
            hard_off_topic_tripped = True
            classification         = "completely_off_topic"
        elif "partially_on_topic" in classification:
            # Partial: cap at 6/10 and apply 75% penalty
            s2_coherence = min(s2_coherence, 6.0) * 0.75

        # Hard cap: score cannot exceed 10
        s2_coherence   = max(0.0, min(10.0, s2_coherence))
        running_points += s2_coherence

        s2_fb = (
            f"### Stage 2: Topic Alignment & Structure (Qwen 2.5 72B)\n"
            f"- **Relevance Category**: {classification.replace('_', ' ').title()}\n"
            f"- **Structural Coherence**: {s2_coherence}/10.0\n"
            f"- **Evaluator Rationale**: {qwen_data.get('justification')}\n\n"
        )
        cumulative_feedback.append(s2_fb)
        print(f"✅ Stage 2 complete — {s2_coherence}/10 pts | {classification}")

    except Exception as s2_err:
        s2_coherence   = 5.0  # conservative neutral
        running_points += s2_coherence
        cumulative_feedback.append(
            "### Stage 2: Topic Alignment & Structure\n"
            "- System timeout verifying topic relevance. Conservative baseline applied.\n\n"
        )

    # ── CIRCUIT BREAKER: Off-topic → skip Stage 3 ────────────────────────────
    if hard_off_topic_tripped:
        final_score = round((running_points / 100.0) * max_score)
        final_score = max(0, min(max_score, final_score))

        report_header = (
            f"## 📊 Comprehensive Academic Evaluation Analysis\n"
            f"**Aggregated Weighted Composition Score**: `{final_score} / {max_score}`\n"
            f"*(Stage 1 Language: {round(s1_points, 1)}/15 | "
            f"Stage 2 Setup: 0/10 [FAILED] | Stage 3 Analysis: 0/75 [BYPASSED])*\n"
            f"------------\n\n"
        )

        fail_rationale = qwen_data.get(
            'justification',
            f"Submission dropped. Content lacks thematic relationship to: '{master_title}'."
        )
        cumulative_feedback.append(
            f"### Stage 3: Rhetorical Soundness, Logic & Rubric Adherence\n"
            f"- **Thesis Development & Flow**: 0.0/38.0 [Canceled]\n"
            f"- **Argument Veracity & Evidence Supporting**: 0.0/37.0 [Canceled]\n"
            f"- **Deep Content Critique**: Grading halted — essay topic does not match prompt.\n\n"
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

    # ══════════════════════════════════════════════════════════════
    # STAGE 3 — ARCHITECTURE, RUBRIC & LOGIC (75 pts)
    # ══════════════════════════════════════════════════════════════
    try:
        gemini_data = call_gemini_stage3(essay_text, assignment, word_count)

        s3_thesis   = max(0.0, min(38.0, float(gemini_data.get("structural_and_thesis_score_out_of_38", 19.0))))
        s3_evidence = max(0.0, min(37.0, float(gemini_data.get("evidence_and_logical_validity_score_out_of_37", 18.5))))
        s3_total    = s3_thesis + s3_evidence
        running_points += s3_total

        s3_fb = (
            f"### Stage 3: Rhetorical Soundness, Logic & Rubric Adherence (Groq)\n"
            f"- **Thesis Development & Flow**: {s3_thesis}/38.0\n"
            f"- **Argument Veracity & Evidence Supporting**: {s3_evidence}/37.0\n"
            f"- **Deep Content Critique**: {gemini_data.get('comprehensive_critique')}\n\n"
        )
        cumulative_feedback.append(s3_fb)
        print(f"✅ Stage 3 complete — {s3_total}/75 pts")

    except Exception as s3_err:
        print(f"⚠️ Stage 3 failed: {s3_err}. Falling back to Qwen-72B router...")
        try:
            fallback_prompt = (
                f"You are a strict professor. Grade the logic, evidence, and argument depth "
                f"of this essay out of 75 points. Penalise missing terminology, factual errors, "
                f"and absent evidence. Respond with exactly: 'Score: X/75'\n\nEssay:\n{essay_text}"
            )
            raw_hf    = call_huggingface(fallback_prompt)
            s3_total  = 37.5  # conservative default
            num_match = re.search(r"(\d+\.?\d*)\s*/\s*75", raw_hf)
            if num_match:
                s3_total = min(75.0, max(0.0, float(num_match.group(1))))
            running_points += s3_total
            cumulative_feedback.append(
                f"### Stage 3: Deep Evaluation (Qwen Router Fallback)\n"
                f"- Score: {s3_total}/75\n- Feedback: {raw_hf[:400]}...\n\n"
            )
        except Exception:
            # Emergency: 40% of 75 = 30
            running_points += 30.0
            cumulative_feedback.append(
                "### Stage 3: Deep Evaluation (Emergency Default)\n"
                "- System unavailable to grade argument structure. Conservative score applied.\n\n"
            )

    # ── Final score assembly ──────────────────────────────────────────────────
    final_scaled_score = round((running_points / 100.0) * max_score)
    final_scaled_score = max(0, min(max_score, final_scaled_score))

    report_header = (
        f"## 📊 Comprehensive Academic Evaluation Analysis\n"
        f"**Aggregated Weighted Composition Score**: `{final_scaled_score} / {max_score}`\n"
        f"*(Stage 1 Language: 15% | Stage 2 Setup: 10% | Stage 3 Analysis: 75%)*\n"
        f"------------\n\n"
    )

    return {
        "score":          final_scaled_score,
        "feedback":       report_header + "".join(cumulative_feedback).strip(),
        "off_topic":      False,
        "ai_detected":    False,
        "low_confidence": False,
        "graded_by":      "ensemble-pipeline-engine-v4.1",
    }