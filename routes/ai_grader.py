# routes/ai_grader.py
"""
ENSEMBLE GRADING CHAIN v5.0 — RUBRIC-AWARE DYNAMIC ROUTING
═══════════════════════════════════════════════════════════

The pipeline now reads the teacher's actual marking key / rubric and
dynamically routes each criterion to the most appropriate model:

  LINGUISTICS BUCKET  → Qwen 2.5 72B (IELTS examiner mode)
    Covers: grammar, vocabulary, terminology, spelling, language mechanics

  COHERENCE BUCKET    → Qwen 2.5 72B (structural coherence mode)
    Covers: structure, coherence, flow, organisation, format, clarity

  CONTENT BUCKET      → Groq llama-3.3-70b
    Covers: everything else — accuracy, arguments, evidence, frameworks,
            critical thinking, examples, depth, analysis

Stage weights are computed FROM the rubric, not hardcoded.
If no rubric is provided, fallback weights are 15 / 10 / 75.

NEW IN v5.0:
  - parse_marking_key_from_reference(): extracts structured criteria from
    free-text reference material (handles the "marking key pasted into
    referenceMaterial" pattern)
  - classify_rubric_criteria(): routes each criterion to the right bucket
  - Dynamic stage weight computation from actual rubric weights
  - Stage 3 prompt is now fully rubric-driven — NO hardcoded biology caps
  - Stage 2 coherence prompt updated to use rubric-defined coherence criteria
  - Stage 1 IELTS prompt updated to use rubric-defined linguistics criteria
  - _build_score_breakdown() now shows actual criterion names, not generic labels

PRESERVED FROM v4.2:
  - FIX 1: Band-to-score linear scale (no compression for Band 7+)
  - FIX 2: Partial-topic cap at 6.0 only (no 0.75 multiplier)
  - FIX 3: Second-pass takes HIGHER score, not average
  - FIX 4: Per-stage breakdown at top of feedback
  - FIX 5: Minimum floor scores per stage
"""

import os
import re
import json
import requests as http_requests
from dotenv import load_dotenv

from routes.grading_prompt import parse_ai_response

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

# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD SETS FOR BUCKET CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

_LINGUISTICS_KEYWORDS = {
    "grammar", "vocabulary", "vocab", "terminology", "spelling", "language",
    "punctuation", "syntax", "lexical", "word choice", "diction", "mechanics",
    "fluency", "sentence", "expression", "professional vocabulary",
    "technical vocabulary", "professional terminology",
}

_COHERENCE_KEYWORDS = {
    "structure", "coherence", "flow", "organisation", "organization",
    "format", "clarity", "paragraphing", "transitions", "layout",
    "logical order", "sequencing", "structure & coherence",
    # Note: "introduction" and "conclusion" removed — too broad,
    # would incorrectly catch "introduction of examples" etc.
    # "Real-World Examples", "evidence", "examples" → content bucket
}

# Everything else → content/logic bucket (Groq)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def clean_and_parse_json(raw_str: str) -> dict:
    cleaned = raw_str.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$",     "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if json_match:
        cleaned = json_match.group(0)
    return json.loads(cleaned)


def _strip_markdown(text: str) -> str:
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


# ─────────────────────────────────────────────────────────────────────────────
# RUBRIC PARSING & DYNAMIC ROUTING  (NEW in v5.0)
# ─────────────────────────────────────────────────────────────────────────────

def parse_marking_key_from_reference(reference_material: str) -> list[dict]:
    """
    Attempts to extract a structured list of grading criteria from the
    teacher's free-text reference material. This handles the common case
    where the marking key is pasted into the referenceMaterial field.

    Returns a list of dicts:
        [{"name": str, "weight": float, "descriptor": str}, ...]

    If extraction fails or no criteria are found, returns [].

    Strategy:
      1. Look for explicit weight patterns:  "Content & Accuracy   25%"
      2. Look for table rows with | separators (markdown tables)
      3. If neither found, return [] so caller uses the assignment.rubric dict
    """
    if not reference_material or not reference_material.strip():
        return []

    criteria = []

    # ── Strategy 1: Markdown table rows ──────────────────────────────────────
    # Matches rows like:  | Content & Accuracy | 25% | description |
    table_pattern = re.compile(
        r"\|\s*([^|]+?)\s*\|\s*(\d+(?:\.\d+)?)\s*%\s*\|([^|]*)\|?",
        re.MULTILINE
    )
    for m in table_pattern.finditer(reference_material):
        name       = m.group(1).strip()
        weight     = float(m.group(2))
        descriptor = m.group(3).strip()
        # Skip separator rows like |---|---|
        if re.match(r"^[-: ]+$", name):
            continue
        if weight > 0:
            criteria.append({"name": name, "weight": weight, "descriptor": descriptor})

    if criteria:
        print(f"📋 [RubricParser] Extracted {len(criteria)} criteria from markdown table")
        return criteria

    # ── Strategy 2: "Criterion   Weight%   Description" lines ────────────────
    # Handles formats like:
    #   Content & Accuracy   25%   Correct identification and explanation...
    #   Professional Terminology 15% Accurate use of consulting vocabulary
    line_pattern = re.compile(
        r"^([A-Za-z][^\n%]{4,55}?)\s{1,}(\d+(?:\.\d+)?)\s*%\s*(.*)?$",
        re.MULTILINE
    )
    for m in line_pattern.finditer(reference_material):
        name       = m.group(1).strip().rstrip("-|:").strip()
        weight     = float(m.group(2))
        descriptor = (m.group(3) or "").strip()
        # Skip header-like rows or rows with implausible weights
        if weight <= 0 or weight > 100:
            continue
        if len(name) < 3 or re.match(r"^[-= ]+$", name):
            continue
        # Skip if name looks like a number or very short code
        if re.match(r"^\d+$", name):
            continue
        criteria.append({"name": name, "weight": weight, "descriptor": descriptor})

    if criteria:
        print(f"📋 [RubricParser] Extracted {len(criteria)} criteria from line patterns")
        return criteria

    # ── Strategy 3: Simple "Name: XX%" patterns ──────────────────────────────
    simple_pattern = re.compile(
        r"([A-Za-z &/,\-]{4,60}?)[:\-–]\s*(\d+(?:\.\d+)?)\s*%",
        re.MULTILINE
    )
    for m in simple_pattern.finditer(reference_material):
        name   = m.group(1).strip()
        weight = float(m.group(2))
        if weight > 0 and weight <= 100:
            criteria.append({"name": name, "weight": weight, "descriptor": ""})

    if criteria:
        print(f"📋 [RubricParser] Extracted {len(criteria)} criteria from simple patterns")
        return criteria

    print("📋 [RubricParser] No structured criteria found in reference material")
    return []


def classify_rubric_criteria(criteria: list[dict]) -> dict:
    """
    Takes a list of criterion dicts and classifies each into one of three
    buckets: 'linguistics', 'coherence', or 'content'.

    Returns:
    {
        "linguistics": [{"name":..., "weight":..., "descriptor":...}, ...],
        "coherence":   [...],
        "content":     [...],
        "linguistics_weight": float,   # sum of weights in this bucket
        "coherence_weight":   float,
        "content_weight":     float,
    }
    """
    buckets = {"linguistics": [], "coherence": [], "content": []}

    for criterion in criteria:
        name_lower = criterion["name"].lower()
        desc_lower = criterion.get("descriptor", "").lower()
        combined   = name_lower + " " + desc_lower

        # Check linguistics keywords first (most specific)
        if any(kw in combined for kw in _LINGUISTICS_KEYWORDS):
            buckets["linguistics"].append(criterion)
        # Then coherence
        elif any(kw in combined for kw in _COHERENCE_KEYWORDS):
            buckets["coherence"].append(criterion)
        # Everything else → content/logic
        else:
            buckets["content"].append(criterion)

    result = dict(buckets)
    result["linguistics_weight"] = sum(c["weight"] for c in buckets["linguistics"])
    result["coherence_weight"]   = sum(c["weight"] for c in buckets["coherence"])
    result["content_weight"]     = sum(c["weight"] for c in buckets["content"])

    total = result["linguistics_weight"] + result["coherence_weight"] + result["content_weight"]
    print(
        f"📊 [RubricRouter] Linguistics={result['linguistics_weight']}% | "
        f"Coherence={result['coherence_weight']}% | "
        f"Content={result['content_weight']}% | Total={total}%"
    )
    return result


def resolve_stage_weights(assignment) -> tuple[float, float, float, dict]:
    """
    Determines the actual point budgets for each stage based on:
      1. Criteria parsed from reference_material (marking key)
      2. Falling back to assignment.rubric dict
      3. Falling back to hardcoded defaults (15 / 10 / 75)

    Returns:
        (s1_max, s2_max, s3_max, classified_criteria)

    classified_criteria keys: linguistics[], coherence[], content[],
        linguistics_weight, coherence_weight, content_weight
    """
    reference = getattr(assignment, "reference_material", "") or ""
    rubric    = getattr(assignment, "rubric", None)

    # ── Try marking key from reference material first ─────────────────────────
    criteria = parse_marking_key_from_reference(reference)

    # ── Fall back to rubric dict if reference had nothing ─────────────────────
    if not criteria and rubric:
        if isinstance(rubric, str):
            try:
                rubric = json.loads(rubric)
            except Exception:
                rubric = None
        if isinstance(rubric, dict):
            criteria = [
                {"name": k, "weight": float(v), "descriptor": ""}
                for k, v in rubric.items()
                if float(v) > 0
            ]

    # ── No criteria at all → use hardcoded defaults ───────────────────────────
    if not criteria:
        print("📊 [RubricRouter] No criteria found — using default weights 15/10/75")
        return 15.0, 10.0, 75.0, {
            "linguistics": [], "coherence": [], "content": [],
            "linguistics_weight": 15.0, "coherence_weight": 10.0, "content_weight": 75.0,
        }

    classified = classify_rubric_criteria(criteria)

    lw = classified["linguistics_weight"]
    cw = classified["coherence_weight"]
    co = classified["content_weight"]
    total = lw + cw + co

    # Normalise to 100 if weights don't sum to 100 (defensive)
    if total > 0 and abs(total - 100.0) > 1.0:
        lw = round(lw / total * 100, 1)
        cw = round(cw / total * 100, 1)
        co = round(100.0 - lw - cw, 1)
        print(f"📊 [RubricRouter] Weights normalised to 100: {lw}/{cw}/{co}")

    # Enforce minimum 5pts per stage so no stage becomes meaningless
    lw = max(5.0, lw)
    cw = max(5.0, cw)
    co = max(5.0, co)

    # Re-normalise after floor enforcement
    total2 = lw + cw + co
    lw = round(lw / total2 * 100, 1)
    cw = round(cw / total2 * 100, 1)
    co = round(100.0 - lw - cw, 1)

    return lw, cw, co, classified


def _format_criteria_for_prompt(criteria_list: list[dict]) -> str:
    """Formats a list of criterion dicts into a readable block for prompts."""
    if not criteria_list:
        return "Standard requirements for this category."
    lines = []
    for c in criteria_list:
        desc = f" — {c['descriptor']}" if c.get("descriptor") else ""
        lines.append(f"  • {c['name']} ({c['weight']}%){desc}")
    return "\n".join(lines)


def _format_stage0_checklist(checklist: list[str]) -> str:
    if not checklist:
        return "No explicit checklist could be derived from the marking key."
    return "\n".join([f"  - {item}" for item in checklist])


def _build_stage0_checklist_prompt(assignment, content_criteria: list[dict], reference_material: str) -> str:
    title        = getattr(assignment, "title", "Untitled")
    instructions = getattr(assignment, "instructions", "")
    rubric_text  = _format_assignment_rubric(assignment)
    ref_block    = f"\nREFERENCE MATERIAL / MARKING KEY:\n{reference_material[:3000]}\n\n" if reference_material.strip() else ""
    criteria_block = _format_criteria_for_prompt(content_criteria or [])

    return (
        "You are an expert academic rubric analyst.\n"
        "Extract an explicit checklist of required essay elements from the marking key and rubric.\n"
        "Produce concrete, testable checklist items that the essay must contain or demonstrate.\n"
        "Do not invent requirements not implied by the marking key or rubric.\n\n"
        f"Assignment Title: {title}\n"
        f"Assignment Instructions: {instructions}\n\n"
        f"RUBRIC / MARKING KEY:\n{rubric_text}\n\n"
        f"{ref_block}"
        f"CONTENT CRITERIA:\n{criteria_block}\n\n"
        "Output ONLY valid JSON with a single key 'checklist':\n"
        "{\n"
        "  \"checklist\": [\n"
        "    \"Item 1\",\n"
        "    \"Item 2\"\n"
        "  ]\n"
        "}"
    )


def extract_stage0_checklist(assignment, content_criteria: list[dict], reference_material: str) -> list[str]:
    if not reference_material and not content_criteria:
        return []

    prompt = _build_stage0_checklist_prompt(assignment, content_criteria, reference_material)
    raw = None
    try:
        if GROQ_SDK_AVAILABLE and GROQ_API_KEY:
            groq_client = Groq(api_key=GROQ_API_KEY)
            print("🧭 [Stage 0] Extracting rubric checklist via Groq")
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=700,
                temperature=0.0,
                seed=42,
            )
            raw = response.choices[0].message.content.strip()
        else:
            print("🧭 [Stage 0] Groq unavailable; extracting rubric checklist via HF fallback")
            raw = call_huggingface(prompt)

        parsed = clean_and_parse_json(raw)
        checklist = [str(item).strip() for item in parsed.get("checklist", []) if str(item).strip()]
        if checklist:
            print(f"🧭 [Stage 0] Checklist extracted ({len(checklist)} items)")
            return checklist
    except Exception as e:
        print(f"⚠️ [Stage 0] Checklist extraction failed: {e}")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: GRAMMAR & VOCAB  (weight = linguistics_weight)
# ─────────────────────────────────────────────────────────────────────────────

def call_phi3_ielts(essay_text: str, assignment=None, linguistics_criteria: list = None) -> tuple:
    """
    Grades linguistics criteria only. Max contribution = linguistics_weight pts.
    """
    question = ""
    if assignment:
        question = getattr(assignment, "instructions", "") or getattr(assignment, "title", "") or ""

    criteria_block = _format_criteria_for_prompt(linguistics_criteria or [])

    print("🎓 [Stage 1] Calling Qwen router (IELTS examiner mode)...")

    router_prompt = f"""You are an expert IELTS examiner and linguistics specialist.
Evaluate ONLY the language mechanics of this essay — grammar, vocabulary, and professional terminology.
Do NOT assess content accuracy, argument quality, or topic relevance.

Assignment Question: {question}

Linguistics criteria being assessed:
{criteria_block}

Student Essay:
{essay_text[:3000]}

Respond in EXACTLY this format (no extra text):
Band Score: X.X
Justification: [2-4 sentences covering: grammatical range and accuracy, lexical resource and precision, appropriate use of professional/technical vocabulary for the subject]"""

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
            print("✅ [Stage 1] Grammar graded via Qwen router")
            return result, "Qwen2.5-72B-grammar-specialist"
        raise Exception("Empty response from router")
    except Exception as e:
        raise Exception(f"Stage 1 router call failed: {e}")


def parse_phi3_stage1_response(raw: str, s1_max: float) -> tuple:
    """
    Extracts band score and converts to a score out of s1_max points.
    Band 9 → s1_max, Band 1 → 0, linear scale.
    Floor at 20% of s1_max to prevent API hiccup from destroying submission.
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
        stage1_score = score_ratio * s1_max
    else:
        band         = 5.0
        stage1_score = s1_max * 0.5

    floor = round(s1_max * 0.20, 1)
    stage1_score = max(floor, stage1_score)

    feedback = (
        f"### Stage 1: Vocabulary & Grammar\n"
        f"**Model**: Qwen 2.5 72B — IELTS Examiner Mode\n"
        f"**Score**: {round(stage1_score, 2)} / {s1_max} pts\n"
        f"**IELTS Band Equivalent**: {band} / 9.0\n\n"
        f"**Evaluator Comments**:\n{raw.strip()}\n\n"
        f"---\n\n"
    )
    return round(stage1_score, 2), feedback


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: TOPIC ALIGNMENT & COHERENCE  (weight = coherence_weight)
# ─────────────────────────────────────────────────────────────────────────────

def run_stage2_qwen(
    essay_text: str,
    assignment_title: str,
    assignment_instructions: str,
    s2_max: float,
    coherence_criteria: list = None,
) -> dict:
    """
    Topic guard + structural coherence scoring.
    Max contribution = s2_max points.
    Coherence criteria injected from the actual rubric.
    """
    clean_title    = assignment_title.strip()
    clean_instruct = assignment_instructions.strip() if assignment_instructions else "Follow core title theme."

    criteria_block = _format_criteria_for_prompt(coherence_criteria or [])

    # Compute score description for this assignment's scale
    excellent_floor = round(s2_max * 0.80, 1)
    good_floor      = round(s2_max * 0.60, 1)
    avg_floor       = round(s2_max * 0.40, 1)

    prompt = f"""You are a strict academic auditor assessing topic alignment and structural coherence.

[ASSIGNMENT]
TITLE/TOPIC: {clean_title}
INSTRUCTIONS: {clean_instruct}

[COHERENCE CRITERIA FROM MARKING KEY]
{criteria_block}

[STUDENT SUBMISSION — First 2500 characters]
{essay_text[:2500]}

[EVALUATION RULES]

TOPIC CLASSIFICATION:
1. Essay is about a completely different topic → "completely_off_topic", score = 0.0
2. Essay addresses the topic but with major gaps → "partially_on_topic"
3. Essay directly and fully addresses the topic → "completely_on_topic"

COHERENCE SCORING (out of {s2_max}):
Apply these MANDATORY DEDUCTIONS before assigning any score:

- Paragraphs repeat the same idea with different subjects → DEDUCT {round(s2_max * 0.2, 1)} points
- Topic sentences absent, vague, or just restate "X is important" → DEDUCT {round(s2_max * 0.15, 1)} points
- Conclusion merely restates the introduction with no synthesis → DEDUCT {round(s2_max * 0.10, 1)} points
- Essay well-organised but content is empty or wrong → MUST score no higher than {round(s2_max * 0.50, 1)}/{s2_max}

SCORING SCALE (out of {s2_max}):
  Weak (vague, no relevant terms, repetitive):         0 – {avg_floor}
  Average (on-topic, some gaps, basic structure):      {avg_floor} – {good_floor}
  Good (accurate, organised, uses correct terms):      {good_floor} – {excellent_floor}
  Excellent (precise, deep, synthesised conclusion):   {excellent_floor} – {s2_max}

⚠️ "completely_on_topic" does NOT automatically mean high coherence.
   Content quality inside paragraphs determines the coherence score.

Respond in EXACT JSON — no prose outside the object:
{{
    "is_on_topic": true,
    "relevance_classification": "completely_on_topic",
    "coherence_score_out_of_10": {round(s2_max * 0.70, 1)},
    "justification": "Detailed rationale with specific deductions applied."
}}
Note: coherence_score_out_of_10 must be a float between 0.0 and {s2_max}
Note: classification choices: "completely_on_topic", "partially_on_topic", "completely_off_topic" """

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
        print(f"⚠️ [Stage 2 Bypass] {e} — deploying defensive bypass.")
        return {
            "is_on_topic":               True,
            "relevance_classification":  "completely_on_topic",
            "coherence_score_out_of_10": round(s2_max * 0.55, 1),
            "justification":             "Relevance validation bypassed; conservative baseline applied."
        }


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 3: GROQ — CONTENT, LOGIC & RUBRIC  (weight = content_weight)
# ─────────────────────────────────────────────────────────────────────────────

def _format_assignment_rubric(assignment) -> str:
    rubric = getattr(assignment, "rubric", None)
    if not rubric:
        return "Standard logical structure, argumentation soundness, and text requirements."
    if isinstance(rubric, dict):
        return "\n".join([f"- {k}: {v}%" for k, v in rubric.items()])
    try:
        parsed = json.loads(rubric)
        if isinstance(parsed, dict):
            return "\n".join([f"- {k}: {v}%" for k, v in parsed.items()])
    except Exception:
        pass
    return str(rubric)


def _build_stage3_fallback_prompt(essay_text: str, assignment, s3_max: float) -> str:
    title        = getattr(assignment, "title", "Untitled")
    instructions = getattr(assignment, "instructions", "")
    rubric_text  = _format_assignment_rubric(assignment)
    reference    = getattr(assignment, "reference_material", "") or ""
    reference_block = f"REFERENCE / MARKING KEY:\n{reference[:2000]}\n\n" if reference.strip() else ""

    return (
        f"You are a strict academic grader. Grade this essay out of {s3_max} points.\n\n"
        f"Assignment Title: {title}\n"
        f"Assignment Instructions: {instructions}\n\n"
        f"Rubric:\n{rubric_text}\n\n"
        f"{reference_block}"
        f"Student Essay:\n{essay_text[:3000]}\n\n"
        f"Respond in valid JSON only:\n"
        f"{{\n"
        f"  \"score\": <0-{s3_max}>,\n"
        f"  \"feedback\": \"<detailed critique>\"\n"
        f"}}"
    )


def _call_groq_once(groq_client, model_name: str, prompt: str) -> dict:
    response = groq_client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.0,
        seed=42,
    )
    return clean_and_parse_json(response.choices[0].message.content.strip())


def call_gemini_stage3(
    essay_text: str,
    assignment,
    word_count: int,
    s3_max: float,
    content_criteria: list = None,
    reference_material: str = "",
    stage0_checklist: list[str] = None,
) -> dict:
    """
    Stage 3: Grades content criteria from the rubric.
    Max contribution = s3_max points.
    Caps are DERIVED from the rubric, not hardcoded to any subject.
    """
    title        = getattr(assignment, "title", "Untitled")
    instructions = getattr(assignment, "instructions", "")

    # Build criteria block from actual rubric
    criteria_block = _format_criteria_for_prompt(content_criteria or [])

    checklist_block = "No explicit checklist items available from Stage 0."
    if stage0_checklist:
        checklist_block = "\n".join([f"  • {item}" for item in stage0_checklist])

    # Reference material / marking key (trimmed to fit context)
    ref_block = ""
    if reference_material and reference_material.strip():
        ref_block = f"\nMARKING KEY / REFERENCE MATERIAL (use to assess against expected content):\n{reference_material[:3000]}\n"

    # Derive sub-score maxima: split s3_max ~50/50 between thesis and evidence
    s3_thesis_max   = round(s3_max * 0.507, 0)  # ~51%
    s3_evidence_max = round(s3_max - s3_thesis_max, 0)  # ~49%

    # Derive quality benchmarks proportionally
    excellent_pct = round(s3_max * 0.90, 0)
    good_pct      = round(s3_max * 0.70, 0)
    moderate_pct  = round(s3_max * 0.50, 0)
    weak_pct      = round(s3_max * 0.30, 0)

    prompt = f"""You are a strict senior academic professor grading against the exact criteria below.
Grade ONLY the content, argument quality, and subject-matter accuracy.
Do NOT re-assess grammar, vocabulary, or basic topic relevance — those are handled by other stages.

ASSIGNMENT TITLE: {title}
ASSIGNMENT INSTRUCTIONS: {instructions}
{ref_block}
CONTENT CRITERIA TO GRADE (these are the ONLY criteria that matter for this stage):
{criteria_block}

CHECKLIST ITEMS FOR VERIFICATION:
{checklist_block}

For each checklist item above, state whether it is Present, Partially Present, or Missing.
Missing checklist items should be treated as important deductions.

STUDENT ESSAY:
{essay_text}
WORD COUNT: {word_count}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING (total {s3_max} points):
  structural_and_thesis_score:   max {s3_thesis_max}   (thesis clarity, argument development, logical flow, conclusion synthesis)
  evidence_and_content_score:    max {s3_evidence_max}   (accuracy against marking key, use of required frameworks/theory, evidence quality, examples)

QUALITY BENCHMARKS (total out of {s3_max}):
  Excellent  (≥{excellent_pct}): All criteria met at distinction level. Specific frameworks named and explained. Critical analysis present. Conclusion synthesises rather than restates.
  Good       (≥{good_pct}):     Most criteria met. Some frameworks used. Minor gaps in depth or evidence.
  Moderate   (≥{moderate_pct}):  Some criteria met. Descriptive rather than analytical. Basic frameworks only. Thin evidence.
  Weak       (≥{weak_pct}):     Few criteria met. Missing key required content. No named frameworks. No evidence.
  Very Weak  (<{weak_pct}):     Criteria largely unmet. Fundamental errors. No analytical engagement.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY DEDUCTIONS (apply these BEFORE scoring):

1. MISSING REQUIRED TERMINOLOGY: If the essay is missing subject-specific vocabulary
   that the marking key or criteria explicitly require → CAP evidence_and_content_score
   at 50% of its maximum ({round(s3_evidence_max * 0.5, 0)} pts)

2. NO EVIDENCE / EXAMPLES: If zero concrete examples, case studies, named theories,
   or quantifiable facts are present → CAP evidence_and_content_score
   at 60% of its maximum ({round(s3_evidence_max * 0.6, 0)} pts)

3. VAGUE / ABSENT THESIS: Introduction contains no clear arguable claim
   (just "this essay will discuss X") → CAP structural_and_thesis_score
   at 50% of its maximum ({round(s3_thesis_max * 0.5, 0)} pts)

4. SHALLOW CONCLUSION: Conclusion only restates introduction with no synthesis
   → DEDUCT {round(s3_thesis_max * 0.08, 1)} points from structural_and_thesis_score

5. REPETITIVE BODY PARAGRAPHS: 3+ paragraphs making the same point with different
   subjects → DEDUCT {round(s3_thesis_max * 0.18, 1)} points from structural_and_thesis_score

NOTE: These caps are proportional and subject-neutral. Do NOT apply biology-specific
or subject-specific penalties not present in the criteria above.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your comprehensive_critique MUST:
1. State which of the content criteria were met, partially met, or missed
2. State which deductions/caps were triggered and why
3. Reference specific passages or lack thereof as evidence
4. State what the essay would need to reach the next quality band

Respond with ONLY a valid JSON object:
{{
    "structural_and_thesis_score_out_of_38": {round(s3_thesis_max * 0.50, 1)},
    "evidence_and_logical_validity_score_out_of_37": {round(s3_evidence_max * 0.50, 1)},
    "comprehensive_critique": "Detailed critique here."
}}
Note: structural_and_thesis_score_out_of_38 must be between 0 and {s3_thesis_max}
Note: evidence_and_logical_validity_score_out_of_37 must be between 0 and {s3_evidence_max}"""

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

                s3_thesis   = max(0.0, min(s3_thesis_max,   float(parsed.get("structural_and_thesis_score_out_of_38",   s3_thesis_max   * 0.5))))
                s3_evidence = max(0.0, min(s3_evidence_max, float(parsed.get("evidence_and_logical_validity_score_out_of_37", s3_evidence_max * 0.5))))
                s3_total    = s3_thesis + s3_evidence

                # Second-pass for long essays scoring unexpectedly low
                if word_count >= 600 and s3_total < (s3_max * 0.53):
                    print(f"⚠️ [Stage 3] Long essay scored low ({s3_total}/{s3_max}) — verification pass...")
                    try:
                        parsed2     = _call_groq_once(groq_client, model_name, prompt)
                        s3_thesis2  = max(0.0, min(s3_thesis_max,   float(parsed2.get("structural_and_thesis_score_out_of_38",   s3_thesis_max   * 0.5))))
                        s3_evidence2= max(0.0, min(s3_evidence_max, float(parsed2.get("evidence_and_logical_validity_score_out_of_37", s3_evidence_max * 0.5))))
                        s3_total2   = s3_thesis2 + s3_evidence2

                        if s3_total2 > s3_total:
                            s3_thesis   = s3_thesis2
                            s3_evidence = s3_evidence2
                            s3_total    = s3_total2
                            parsed["comprehensive_critique"] = parsed2.get(
                                "comprehensive_critique",
                                parsed.get("comprehensive_critique", "")
                            )
                            print(f"✅ [Stage 3] Pass 2 higher ({s3_total}/{s3_max}) — using Pass 2")
                        else:
                            print(f"✅ [Stage 3] Pass 1 held ({s3_total}/{s3_max})")
                    except Exception as ve:
                        print(f"⚠️ [Stage 3] Verification pass failed: {ve}")

                parsed["structural_and_thesis_score_out_of_38"]         = s3_thesis
                parsed["evidence_and_logical_validity_score_out_of_37"] = s3_evidence
                print(f"✅ [Stage 3] Groq graded with {model_name} — {s3_total}/{s3_max}")
                return parsed

            except Exception as e:
                print(f"⚠️ [Stage 3] Groq error on {model_name}: {e} — next...")
                continue

        print("⚠️ [Stage 3] All Groq models failed — HF fallback...")
    else:
        print("⚠️ [Stage 3] Groq unavailable — HF fallback...")

    # ── FALLBACK: HF router ───────────────────────────────────────────────────
    fallback_prompt = (
        f"You are a strict academic grader. Grade the content, argument quality, and rubric "
        f"adherence of this essay out of {s3_max} points. Respond with: 'Score: X/{s3_max}'\n\n"
        f"Essay:\n{essay_text}"
    )
    raw_hf   = call_huggingface(fallback_prompt)
    s3_total = s3_max * 0.50
    num_match = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s3_max)}", raw_hf)
    if num_match:
        s3_total = min(s3_max, max(0.0, float(num_match.group(1))))
    return {
        "structural_and_thesis_score_out_of_38":         round(s3_total * (s3_thesis_max / s3_max), 1),
        "evidence_and_logical_validity_score_out_of_37": round(s3_total * (s3_evidence_max / s3_max), 1),
        "comprehensive_critique": raw_hf[:800],
    }


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY FALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

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
        except Exception as e:
            print(f"⚠️ {model['name']} failed: {e}")
            last_error = e
    raise Exception(f"All HuggingFace models failed. Last error: {last_error}")


def grade_with_custom_prompt(prompt: str, max_score: int) -> dict:
    raw = call_huggingface(prompt)
    parsed = parse_ai_response(raw, max_score)
    parsed["graded_by"] = "custom-rubric-hf"
    return parsed


def _similarity_fallback(assignment, essay_text: str) -> dict:
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
    max_score = assignment.max_score or 100
    if word_count >= 400:
        score, feedback = round(max_score * 0.70), "Essay verified. Retained at target threshold."
    elif word_count >= 200:
        score, feedback = round(max_score * 0.55), "Structural length minimal. Expand presentation elements."
    elif word_count >= 50:
        score, feedback = round(max_score * 0.35), "Insufficient composition content length detected."
    else:
        score, feedback = 0, "Composition fails length checks."

    return {
        "score": score, "feedback": feedback,
        "ai_detected": False, "off_topic": False,
        "low_confidence": True, "graded_by": "word-count-local",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCORE BREAKDOWN HEADER
# ─────────────────────────────────────────────────────────────────────────────

def _build_score_breakdown(
    s1_points: float,
    s1_max: float,
    s1_label: str,
    s2_coherence: float,
    s2_max: float,
    s2_label: str,
    s3_total: float,
    s3_max: float,
    s3_label: str,
    final_score: int,
    max_score: int,
    classification: str,
) -> str:
    def bar(score, maximum):
        if maximum <= 0:
            return "░" * 10
        filled = int((score / maximum) * 10)
        return "█" * filled + "░" * (10 - filled)

    total_pts = round(s1_points + s2_coherence + s3_total, 1)

    return (
        f"##  Academic Evaluation Report\n\n"
        f"### Overall Score: {final_score} / {max_score}\n\n"
        f"| Stage | Criteria | Score | Max | Bar |\n"
        f"|-------|----------|-------|-----|-----|\n"
        f"| **Stage 1** | {s1_label} | **{round(s1_points, 1)}** | {s1_max} | `{bar(s1_points, s1_max)}` |\n"
        f"| **Stage 2** | {s2_label} | **{round(s2_coherence, 1)}** | {s2_max} | `{bar(s2_coherence, s2_max)}` |\n"
        f"| **Stage 3** | {s3_label} | **{round(s3_total, 1)}** | {s3_max} | `{bar(s3_total, s3_max)}` |\n"
        f"| | **Total (weighted)** | **{total_pts}** | **100** | |\n\n"
        f"**Topic Classification**: {classification.replace('_', ' ').title()}\n\n"
        f"---\n\n"
        f"## Detailed Stage Feedback\n\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────

def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0, enable_stage0: bool = True) -> dict:
    """
    v5.0 Rubric-aware ensemble grading pipeline.

    Stage weights are computed from the teacher's actual marking key / rubric.
    Each model grades only the criteria that belong to its specialty:
      Stage 1 (Qwen IELTS)    → linguistics criteria  (grammar, vocabulary, terminology)
      Stage 2 (Qwen coherence) → coherence criteria   (structure, flow, organisation)
      Stage 3 (Groq)          → content criteria      (accuracy, arguments, frameworks, evidence)

    Stage 0 optionally extracts an explicit rubric checklist that Stage 3 verifies.
    If no rubric/marking key is provided, defaults to 15 / 10 / 75 weights.
    """
    max_score = getattr(assignment, "max_score", None) or 100

    master_title        = getattr(assignment, "title", "") or ""
    master_instructions = getattr(assignment, "instructions", "") or ""
    reference_material  = getattr(assignment, "reference_material", "") or ""

    if not master_title and prompt:
        master_title = prompt

    stage0_checklist = []

    if not essay_text or word_count < 15:
        return {
            "score": 0,
            "feedback": "❌ Submission content unreadable or below minimum grading length.",
            "off_topic": False, "ai_detected": False,
            "low_confidence": True, "graded_by": "pipeline-pre-rejection",
        }

    # ── STEP 0: Resolve dynamic stage weights from marking key / rubric ───────
    s1_max, s2_max, s3_max, classified = resolve_stage_weights(assignment)
    linguistics_criteria = classified.get("linguistics", [])
    coherence_criteria   = classified.get("coherence", [])
    content_criteria     = classified.get("content", [])

    cumulative_feedback = []
    if enable_stage0 and not stage0_checklist:
        stage0_checklist = extract_stage0_checklist(assignment, content_criteria, reference_material)

    if stage0_checklist:
        cumulative_feedback.append(
            "### Stage 0: Rubric Checklist\n"
            "The following explicit checklist items were derived from the marking key and will be verified by Stage 3:\n"
            f"{_format_stage0_checklist(stage0_checklist)}\n\n"
            "---\n\n"
        )
    elif enable_stage0:
        cumulative_feedback.append(
            "### Stage 0: Rubric Checklist\n"
            "No explicit checklist items could be extracted from the marking key or rubric.\n\n"
            "---\n\n"
        )

    # Build human-readable labels for score breakdown
    s1_label = " & ".join(c["name"] for c in linguistics_criteria) if linguistics_criteria else "Language, Grammar & Vocabulary"
    s2_label = " & ".join(c["name"] for c in coherence_criteria)   if coherence_criteria   else "Topic Relevance & Coherence"
    s3_label = " & ".join(c["name"] for c in content_criteria)     if content_criteria     else "Thesis, Structure & Evidence"

    print(f"🎯 [Pipeline v5.0] Stage weights: S1={s1_max}pts | S2={s2_max}pts | S3={s3_max}pts")

    cumulative_feedback    = []
    running_points         = 0.0
    hard_off_topic_tripped = False
    s1_points              = 0.0
    s2_coherence_final     = 0.0
    s3_total_final         = 0.0
    classification         = "completely_on_topic"
    qwen_data              = {}

    # ══════════════════════════════════════════════════════════════
    # STAGE 1 — LINGUISTICS  (s1_max pts)
    # ══════════════════════════════════════════════════════════════
    try:
        raw_phi3, model_name = call_phi3_ielts(essay_text, assignment, linguistics_criteria)
        s1_points, s1_fb     = parse_phi3_stage1_response(raw_phi3, s1_max)
        running_points      += s1_points
        cumulative_feedback.append(s1_fb)
        print(f"✅ Stage 1 complete — {s1_points}/{s1_max} pts via {model_name}")
    except Exception as s1_err:
        print(f"⚠️ Stage 1 failed: {s1_err}. Trying fallback router...")
        try:
            fallback_prompt = (
                f"Analyze ONLY grammar and vocabulary quality of this essay. "
                f"Give a score out of {s1_max} using exactly: 'Score: X/{s1_max}'\n\nEssay:\n{essay_text}"
            )
            raw_hf    = call_huggingface(fallback_prompt)
            floor     = round(s1_max * 0.20, 1)
            s1_points = floor
            num_match = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s1_max)}", raw_hf)
            if num_match:
                s1_points = min(s1_max, max(floor, float(num_match.group(1))))
            running_points += s1_points
            cumulative_feedback.append(
                f"### Stage 1: {s1_label} (Router Fallback)\n"
                f"**Score**: {s1_points} / {s1_max} pts\n\n"
                f"**Comments**: {raw_hf[:400]}\n\n---\n\n"
            )
        except Exception:
            floor     = round(s1_max * 0.20, 1)
            s1_points = max(floor, min(s1_max * 0.67, (word_count / 400) * s1_max) if word_count >= 350 else s1_max * 0.33)
            running_points += s1_points
            cumulative_feedback.append(
                f"### Stage 1: {s1_label} (Emergency Baseline)\n"
                f"**Score**: {s1_points} / {s1_max} pts\n\n"
                f"System timed out. Conservative score applied.\n\n---\n\n"
            )

    # ══════════════════════════════════════════════════════════════
    # STAGE 2 — COHERENCE  (s2_max pts)
    # ══════════════════════════════════════════════════════════════
    try:
        qwen_data      = run_stage2_qwen(
            essay_text, master_title, master_instructions,
            s2_max, coherence_criteria
        )
        s2_coherence   = float(qwen_data.get("coherence_score_out_of_10", s2_max * 0.5))
        # Clamp to actual s2_max (model was told the max in the prompt)
        s2_coherence   = max(0.0, min(s2_max, s2_coherence))
        classification = str(qwen_data.get("relevance_classification", "completely_on_topic")).lower().strip()

        if (classification == "completely_off_topic" or
                qwen_data.get("is_on_topic") is False or
                s2_coherence <= (s2_max * 0.20)):
            s2_coherence           = 0.0
            hard_off_topic_tripped = True
            classification         = "completely_off_topic"
        elif "partially_on_topic" in classification:
            # Cap at 60% of s2_max for partially on-topic
            s2_coherence = min(s2_coherence, round(s2_max * 0.60, 1))

        s2_coherence       = max(0.0, min(s2_max, s2_coherence))
        s2_coherence_final = s2_coherence
        running_points    += s2_coherence

        s2_fb = (
            f"### Stage 2: {s2_label}\n"
            f"**Model**: Qwen 2.5 72B\n"
            f"**Score**: {s2_coherence} / {s2_max} pts\n"
            f"**Topic Classification**: {classification.replace('_', ' ').title()}\n\n"
            f"**Evaluator Rationale**: {qwen_data.get('justification')}\n\n"
            f"---\n\n"
        )
        cumulative_feedback.append(s2_fb)
        print(f"✅ Stage 2 complete — {s2_coherence}/{s2_max} pts | {classification}")

    except Exception as s2_err:
        s2_coherence        = round(s2_max * 0.55, 1)
        s2_coherence_final  = s2_coherence
        running_points     += s2_coherence
        cumulative_feedback.append(
            f"### Stage 2: {s2_label}\n"
            f"**Score**: {s2_coherence} / {s2_max} pts\n\n"
            f"System timeout. Conservative baseline applied.\n\n---\n\n"
        )

    # ── CIRCUIT BREAKER: Off-topic → skip Stage 3 ────────────────────────────
    if hard_off_topic_tripped:
        final_score = round((running_points / 100.0) * max_score)
        final_score = max(0, min(max_score, final_score))

        breakdown_header = _build_score_breakdown(
            s1_points, s1_max, s1_label,
            0.0, s2_max, s2_label,
            0.0, s3_max, s3_label,
            final_score, max_score, "completely_off_topic"
        )

        fail_rationale = qwen_data.get(
            "justification",
            f"Submission dropped. Content lacks thematic relationship to: '{master_title}'."
        )
        cumulative_feedback.append(
            f"### Stage 3: {s3_label}\n"
            f"**Score**: 0 / {s3_max} pts — BYPASSED\n\n"
            f"Grading halted — essay topic does not match the assignment prompt.\n\n"
            f"❌ **Pipeline Stopped**: {fail_rationale}\n\n---\n\n"
        )

        return {
            "score":          final_score,
            "feedback":       breakdown_header + "".join(cumulative_feedback).strip(),
            "off_topic":      True,
            "ai_detected":    False,
            "low_confidence": False,
            "graded_by":      "stage2-adversarial-abort",
        }

    # ══════════════════════════════════════════════════════════════
    # STAGE 3 — CONTENT & LOGIC  (s3_max pts)
    # ══════════════════════════════════════════════════════════════
    try:
        gemini_data = call_gemini_stage3(
            essay_text, assignment, word_count,
            s3_max, content_criteria, reference_material,
            stage0_checklist=stage0_checklist,
        )

        s3_thesis_max   = round(s3_max * 0.507, 0)
        s3_evidence_max = round(s3_max - s3_thesis_max, 0)

        s3_thesis   = max(0.0, min(s3_thesis_max,   float(gemini_data.get("structural_and_thesis_score_out_of_38",   s3_thesis_max   * 0.5))))
        s3_evidence = max(0.0, min(s3_evidence_max, float(gemini_data.get("evidence_and_logical_validity_score_out_of_37", s3_evidence_max * 0.5))))
        s3_total    = s3_thesis + s3_evidence
        s3_total_final = s3_total
        running_points += s3_total

        s3_fb = (
            f"### Stage 3: {s3_label}\n"
            f"**Model**: Groq (llama-3.3-70b-versatile)\n"
            f"**Thesis Development & Flow**: {s3_thesis} / {s3_thesis_max} pts\n"
            f"**Argument Quality & Evidence**: {s3_evidence} / {s3_evidence_max} pts\n"
            f"**Stage 3 Total**: {round(s3_total, 1)} / {s3_max} pts\n\n"
            f"**Detailed Critique**:\n{gemini_data.get('comprehensive_critique')}\n\n"
            f"---\n\n"
        )
        cumulative_feedback.append(s3_fb)
        print(f"✅ Stage 3 complete — {s3_total}/{s3_max} pts")

    except Exception as s3_err:
        print(f"⚠️ Stage 3 failed: {s3_err}. Falling back to Qwen-72B router...")
        try:
            fallback_prompt = prompt or _build_stage3_fallback_prompt(essay_text, assignment, s3_max)
            raw_hf    = call_huggingface(fallback_prompt)
            s3_total  = round(s3_max * 0.50, 1)
            feedback_text = raw_hf.strip()

            try:
                parsed = clean_and_parse_json(raw_hf)
                if parsed:
                    s3_total = min(s3_max, max(0.0, float(parsed.get("score") or parsed.get("total_score") or s3_total)))
                    feedback_text = parsed.get("feedback", feedback_text)
            except Exception:
                num_match = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s3_max)}", raw_hf)
                if num_match:
                    s3_total = min(s3_max, max(0.0, float(num_match.group(1))))

            s3_total_final  = s3_total
            running_points += s3_total
            cumulative_feedback.append(
                f"### Stage 3: {s3_label} (Qwen Router Fallback)\n"
                f"**Score**: {s3_total} / {s3_max} pts\n\n"
                f"**Feedback**: {feedback_text[:700]}\n\n---\n\n"
            )
        except Exception:
            s3_total_final  = round(s3_max * 0.40, 1)
            running_points += s3_total_final
            cumulative_feedback.append(
                f"### Stage 3: {s3_label} (Emergency Default)\n"
                f"**Score**: {s3_total_final} / {s3_max} pts\n\n"
                f"System unavailable. Conservative score applied.\n\n---\n\n"
            )

    # ── Final score assembly ──────────────────────────────────────────────────
    final_scaled_score = round((running_points / 100.0) * max_score)
    final_scaled_score = max(0, min(max_score, final_scaled_score))

    breakdown_header = _build_score_breakdown(
        s1_points, s1_max, s1_label,
        s2_coherence_final, s2_max, s2_label,
        s3_total_final, s3_max, s3_label,
        final_scaled_score, max_score, classification
    )

    return {
        "score":          final_scaled_score,
        "feedback":       breakdown_header + "".join(cumulative_feedback).strip(),
        "off_topic":      False,
        "ai_detected":    False,
        "low_confidence": False,
        "graded_by":      "ensemble-pipeline-engine-v5.0",
    }