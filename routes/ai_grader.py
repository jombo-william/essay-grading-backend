# routes/ai_grader.py
"""
ENSEMBLE GRADING CHAIN v5.3 — PER-CRITERION STAGE 3 SCORING
═══════════════════════════════════════════════════════════

NEW IN v5.3:
  - Stage 3 now scores EACH content criterion individually by name and weight.
    The model returns {"criterion_scores": {"Criterion Name": <pts>, ...}, ...}
    and the pipeline computes Stage 3 total as a weighted sum of those values.
    This replaces the old fixed thesis_score / evidence_score two-bucket split.
  - Deductions (missing terminology, no evidence, vague thesis, shallow
    conclusion, repetitive paragraphs) are now applied per-criterion based on
    each criterion's semantic role, not globally to one of two buckets.
  - _build_stage3_fallback_prompt updated to request per-criterion JSON.
  - Score breakdown header updated: Stage 3 row now shows per-criterion detail.
  - call_gemini_stage3 signature change: s3_thesis_max / s3_evidence_max removed
    (no longer needed — weights come from the rubric criteria directly).
  - grade_with_ai: s3_thesis_max / s3_evidence_max computation removed.

FIXED IN v5.1 (preserved):
  - FIX 1: cumulative_feedback double-init bug removed
  - FIX 2: Stage 3 JSON keys now use generic names
  - FIX 3: coherence_score field renamed (no "out_of_10" suffix)
  - FIX 4: stage split computed once in grade_with_ai()
  - FIX 5: Partial-topic cap applied in Stage 3 as well as Stage 2
  - FIX 6: _strip_markdown() applied to all AI free-text
  - FIX 7: linguistics-first priority comment in classify_rubric_criteria()

NEW IN v5.2 (preserved):
  - rubric_content (marking key) and reference_material (study docs) are
    read as SEPARATE fields matching the AssignmentForm layout.
  - Stage 0 uses ONLY rubric_content for checklist extraction.
  - Stage 3 receives rubric_content as primary anchor and reference_material
    as secondary context, labelled separately in prompt.
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
    print("WARNING: groq not installed. Run: pip install groq")

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HF_API_KEY     = os.getenv("HF_API_KEY", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
print(f"GEMINI_API_KEY loaded: {'YES' if GEMINI_API_KEY else 'NO - KEY IS MISSING'}")
print(f"HF_API_KEY loaded: {'YES' if HF_API_KEY else 'NO - KEY IS MISSING'}")
print(f"GROQ_API_KEY loaded: {'YES' if GROQ_API_KEY else 'NO - KEY IS MISSING'}")

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
    # Note: "introduction" and "conclusion" removed — too broad.
    # "Real-World Examples", "evidence", "examples" -> content bucket
}

# Everything else -> content/logic bucket (Groq)


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
    """
    Converts markdown-formatted text to plain prose.
    Applied to all AI-generated free-text before embedding in feedback.
    """
    if not text:
        return ""
    result = text
    result = re.sub(r"^#{1,6}\s+", "", result, flags=re.MULTILINE)
    result = re.sub(r"\*\*(.+?)\*\*", r"\1", result)
    result = re.sub(r"\*(.+?)\*",     r"\1", result)
    result = re.sub(r"^[ \t]*[-*]\s+", "- ", result, flags=re.MULTILINE)
    result = re.sub(r"`{1,3}(.*?)`{1,3}", r"\1", result)
    result = re.sub(r"^---+\s*$",     "",    result, flags=re.MULTILINE)
    result = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", result)
    result = re.sub(r"\n{3,}",        "\n\n", result)
    return result.strip()


# ─────────────────────────────────────────────────────────────────────────────
# RUBRIC PARSING & DYNAMIC ROUTING
# ─────────────────────────────────────────────────────────────────────────────

def parse_marking_key_from_reference(reference_material: str) -> list[dict]:
    """
    Attempts to extract a structured list of grading criteria from the
    teacher's free-text reference material.

    Returns a list of dicts:
        [{"name": str, "weight": float, "descriptor": str}, ...]

    If extraction fails or no criteria are found, returns [].

    Strategy:
      1. Look for markdown table rows:  | Criterion | 25% | description |
      2. Look for "Criterion   Weight%   Description" lines
      3. Simple "Name: XX%" patterns
    """
    if not reference_material or not reference_material.strip():
        return []

    criteria = []

    # Strategy 1: Markdown table rows
    table_pattern = re.compile(
        r"\|\s*([^|]+?)\s*\|\s*(\d+(?:\.\d+)?)\s*%\s*\|([^|]*)\|?",
        re.MULTILINE
    )
    for m in table_pattern.finditer(reference_material):
        name       = m.group(1).strip()
        weight     = float(m.group(2))
        descriptor = m.group(3).strip()
        if re.match(r"^[-: ]+$", name):
            continue
        if weight > 0:
            criteria.append({"name": name, "weight": weight, "descriptor": descriptor})

    if criteria:
        print(f"[RubricParser] Extracted {len(criteria)} criteria from markdown table")
        return criteria

    # Strategy 2: "Criterion   Weight%   Description" lines
    line_pattern = re.compile(
        r"^([A-Za-z][^\n%]{4,55}?)\s{1,}(\d+(?:\.\d+)?)\s*%\s*(.*)?$",
        re.MULTILINE
    )
    for m in line_pattern.finditer(reference_material):
        name       = m.group(1).strip().rstrip("-|:").strip()
        weight     = float(m.group(2))
        descriptor = (m.group(3) or "").strip()
        if weight <= 0 or weight > 100:
            continue
        if len(name) < 3 or re.match(r"^[-= ]+$", name):
            continue
        if re.match(r"^\d+$", name):
            continue
        criteria.append({"name": name, "weight": weight, "descriptor": descriptor})

    if criteria:
        print(f"[RubricParser] Extracted {len(criteria)} criteria from line patterns")
        return criteria

    # Strategy 3: Simple "Name: XX%" patterns
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
        print(f"[RubricParser] Extracted {len(criteria)} criteria from simple patterns")
        return criteria

    print("[RubricParser] No structured criteria found in reference material")
    return []


def classify_rubric_criteria(criteria: list[dict]) -> dict:
    """
    Classifies each criterion into 'linguistics', 'coherence', or 'content'.

    Priority order: linguistics > coherence > content.
    If a criterion matches both linguistics and coherence keywords,
    it is assigned to linguistics (more specific bucket). This is intentional
    and silent — no warning is raised.

    Returns:
    {
        "linguistics": [...],
        "coherence":   [...],
        "content":     [...],
        "linguistics_weight": float,
        "coherence_weight":   float,
        "content_weight":     float,
    }
    """
    buckets = {"linguistics": [], "coherence": [], "content": []}

    for criterion in criteria:
        name_lower = criterion["name"].lower()
        desc_lower = criterion.get("descriptor", "").lower()
        combined   = name_lower + " " + desc_lower

        # Linguistics checked first (most specific); coherence second;
        # anything unmatched falls to content.
        if any(kw in combined for kw in _LINGUISTICS_KEYWORDS):
            buckets["linguistics"].append(criterion)
        elif any(kw in combined for kw in _COHERENCE_KEYWORDS):
            buckets["coherence"].append(criterion)
        else:
            buckets["content"].append(criterion)

    result = dict(buckets)
    result["linguistics_weight"] = sum(c["weight"] for c in buckets["linguistics"])
    result["coherence_weight"]   = sum(c["weight"] for c in buckets["coherence"])
    result["content_weight"]     = sum(c["weight"] for c in buckets["content"])

    total = result["linguistics_weight"] + result["coherence_weight"] + result["content_weight"]
    print(
        f"[RubricRouter] Linguistics={result['linguistics_weight']}% | "
        f"Coherence={result['coherence_weight']}% | "
        f"Content={result['content_weight']}% | Total={total}%"
    )
    return result


def resolve_stage_weights(assignment) -> tuple[float, float, float, dict]:
    """
    Determines point budgets for each stage from:
      1. Criteria parsed from rubric_content (dedicated marking key field)
      2. Falling back to assignment.rubric dict
      3. Falling back to hardcoded defaults (15 / 10 / 75)

    Returns:
        (s1_max, s2_max, s3_max, classified_criteria)
    """
    rubric_content = getattr(assignment, "rubric_content", "") or ""
    rubric    = getattr(assignment, "rubric", None)

    criteria = parse_marking_key_from_reference(rubric_content)

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

    if not criteria:
        print("[RubricRouter] No criteria found — using default weights 15/10/75")
        return 15.0, 10.0, 75.0, {
            "linguistics": [], "coherence": [], "content": [],
            "linguistics_weight": 15.0, "coherence_weight": 10.0, "content_weight": 75.0,
        }

    classified = classify_rubric_criteria(criteria)

    lw = classified["linguistics_weight"]
    cw = classified["coherence_weight"]
    co = classified["content_weight"]
    total = lw + cw + co

    if total > 0 and abs(total - 100.0) > 1.0:
        lw = round(lw / total * 100, 1)
        cw = round(cw / total * 100, 1)
        co = round(100.0 - lw - cw, 1)
        print(f"[RubricRouter] Weights normalised to 100: {lw}/{cw}/{co}")

    # Enforce minimum 5pts per stage
    lw = max(5.0, lw)
    cw = max(5.0, cw)
    co = max(5.0, co)

    total2 = lw + cw + co
    lw = round(lw / total2 * 100, 1)
    cw = round(cw / total2 * 100, 1)
    co = round(100.0 - lw - cw, 1)

    return lw, cw, co, classified


def _format_criteria_for_prompt(criteria_list: list[dict]) -> str:
    if not criteria_list:
        return "Standard requirements for this category."
    lines = []
    for c in criteria_list:
        desc = f" — {c['descriptor']}" if c.get("descriptor") else ""
        lines.append(f"  - {c['name']} ({c['weight']}%){desc}")
    return "\n".join(lines)


def _format_stage0_checklist(checklist: list[str]) -> str:
    if not checklist:
        return "No explicit checklist could be derived from the marking key."
    return "\n".join([f"  - {item}" for item in checklist])


def _build_stage0_checklist_prompt(assignment, content_criteria: list[dict], rubric_content: str) -> str:
    """
    Builds the Stage 0 prompt using ONLY rubric_content (the marking key).
    Reference material (books/notes) is intentionally excluded here.
    """
    title          = getattr(assignment, "title", "Untitled")
    instructions   = getattr(assignment, "instructions", "")
    rubric_text    = _format_assignment_rubric(assignment)
    criteria_block = _format_criteria_for_prompt(content_criteria or [])

    rubric_key_block = (
        f"\nMARKING KEY / RUBRIC DOCUMENT:\n{rubric_content[:4000]}\n\n"
        if rubric_content and rubric_content.strip()
        else ""
    )

    return (
        "You are an expert academic rubric analyst.\n"
        "Extract an explicit checklist of required essay elements from the marking key and rubric below.\n"
        "Produce concrete, testable checklist items that the essay must contain or demonstrate.\n"
        "Do not invent requirements not implied by the marking key or rubric.\n\n"
        f"Assignment Title: {title}\n"
        f"Assignment Instructions: {instructions}\n\n"
        f"RUBRIC CRITERIA (with weights):\n{rubric_text}\n\n"
        f"{rubric_key_block}"
        f"CONTENT CRITERIA TO FOCUS ON:\n{criteria_block}\n\n"
        "Output ONLY valid JSON with a single key 'checklist':\n"
        "{\n"
        "  \"checklist\": [\n"
        "    \"Item 1\",\n"
        "    \"Item 2\"\n"
        "  ]\n"
        "}"
    )


def extract_stage0_checklist(assignment, content_criteria: list[dict], rubric_content: str) -> list[str]:
    """
    Extracts the marking key checklist from rubric_content ONLY.
    Does not use reference_material (study notes).
    """
    if not rubric_content and not content_criteria:
        return []

    prompt = _build_stage0_checklist_prompt(assignment, content_criteria, rubric_content)
    raw = None
    try:
        if GROQ_SDK_AVAILABLE and GROQ_API_KEY:
            groq_client = Groq(api_key=GROQ_API_KEY)
            print("[Stage 0] Extracting rubric checklist via Groq")
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=700,
                temperature=0.0,
                seed=42,
            )
            raw = response.choices[0].message.content.strip()
        else:
            print("[Stage 0] Groq unavailable; extracting rubric checklist via HF fallback")
            raw = call_huggingface(prompt)

        parsed = clean_and_parse_json(raw)
        checklist = [str(item).strip() for item in parsed.get("checklist", []) if str(item).strip()]
        if checklist:
            print(f"[Stage 0] Checklist extracted ({len(checklist)} items)")
            return checklist
    except Exception as e:
        print(f"[Stage 0] Checklist extraction failed: {e}")
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

    print("[Stage 1] Calling Qwen router (IELTS examiner mode)...")

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
            print("[Stage 1] Grammar graded via Qwen router")
            return result, "Qwen2.5-72B-grammar-specialist"
        raise Exception("Empty response from router")
    except Exception as e:
        raise Exception(f"Stage 1 router call failed: {e}")


def parse_phi3_stage1_response(raw: str, s1_max: float) -> tuple:
    """
    Extracts band score and converts to a score out of s1_max points.
    Band 9 -> s1_max, Band 1 -> 0, linear scale.
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

    clean_raw = _strip_markdown(raw.strip())

    feedback = (
        f"Stage 1: Vocabulary & Grammar\n"
        f"Model: Qwen 2.5 72B (IELTS Examiner Mode)\n"
        f"Score: {round(stage1_score, 2)} / {s1_max} pts\n"
        f"IELTS Band Equivalent: {band} / 9.0\n\n"
        f"Evaluator Comments:\n{clean_raw}\n\n"
        f"{'—' * 60}\n\n"
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
    """
    clean_title    = assignment_title.strip()
    clean_instruct = assignment_instructions.strip() if assignment_instructions else "Follow core title theme."

    criteria_block = _format_criteria_for_prompt(coherence_criteria or [])

    excellent_floor = round(s2_max * 0.80, 1)
    good_floor      = round(s2_max * 0.60, 1)
    avg_floor       = round(s2_max * 0.40, 1)

    prompt = f"""You are a strict academic auditor assessing topic alignment and structural coherence.

[ASSIGNMENT]
TITLE/TOPIC: {clean_title}
INSTRUCTIONS: {clean_instruct}

[COHERENCE CRITERIA FROM MARKING KEY]
{criteria_block}

[STUDENT SUBMISSION - First 2500 characters]
{essay_text[:2500]}

[EVALUATION RULES]

TOPIC CLASSIFICATION:
1. Essay is about a completely different topic -> "completely_off_topic", score = 0.0
2. Essay addresses the topic but with major gaps -> "partially_on_topic"
3. Essay directly and fully addresses the topic -> "completely_on_topic"

COHERENCE SCORING (out of {s2_max}):
Apply these MANDATORY DEDUCTIONS before assigning any score:

- Paragraphs repeat the same idea with different subjects -> DEDUCT {round(s2_max * 0.2, 1)} points
- Topic sentences absent, vague, or just restate "X is important" -> DEDUCT {round(s2_max * 0.15, 1)} points
- Conclusion merely restates the introduction with no synthesis -> DEDUCT {round(s2_max * 0.10, 1)} points
- Essay well-organised but content is empty or wrong -> MUST score no higher than {round(s2_max * 0.50, 1)}/{s2_max}

SCORING SCALE (out of {s2_max}):
  Weak (vague, no relevant terms, repetitive):         0 - {avg_floor}
  Average (on-topic, some gaps, basic structure):      {avg_floor} - {good_floor}
  Good (accurate, organised, uses correct terms):      {good_floor} - {excellent_floor}
  Excellent (precise, deep, synthesised conclusion):   {excellent_floor} - {s2_max}

"completely_on_topic" does NOT automatically mean high coherence.
Content quality inside paragraphs determines the coherence score.

Respond in EXACT JSON - no prose outside the object:
{{
    "is_on_topic": true,
    "relevance_classification": "completely_on_topic",
    "coherence_score": {round(s2_max * 0.70, 1)},
    "justification": "Detailed rationale with specific deductions applied."
}}
Note: coherence_score must be a float between 0.0 and {s2_max}
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
        print(f"[Stage 2] Qwen payload: {raw_content}")
        return clean_and_parse_json(raw_content)

    except Exception as e:
        print(f"[Stage 2 Bypass] {e} — deploying defensive bypass.")
        return {
            "is_on_topic":              True,
            "relevance_classification": "completely_on_topic",
            "coherence_score":          round(s2_max * 0.55, 1),
            "justification":            "Relevance validation bypassed; conservative baseline applied."
        }


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 3: GROQ — PER-CRITERION CONTENT SCORING  (weight = content_weight)
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


def _build_criterion_scoring_schema(content_criteria: list[dict], s3_max: float) -> tuple[str, dict]:
    """
    Builds the JSON schema description and per-criterion point maximums for the
    Stage 3 prompt.

    Returns:
        (schema_description: str, criterion_maxes: dict[name -> max_pts])

    Each criterion's max points = (criterion.weight / total_content_weight) * s3_max,
    rounded to 1 decimal place. The last criterion absorbs rounding remainder to
    ensure sum == s3_max exactly.
    """
    if not content_criteria:
        # No explicit criteria — fall back to two generic buckets
        half = round(s3_max / 2, 1)
        other_half = round(s3_max - half, 1)
        criterion_maxes = {
            "Thesis & Argument Development": half,
            "Evidence & Subject Accuracy":   other_half,
        }
        schema_lines = [
            f'  "criterion_scores": {{',
            f'    "Thesis & Argument Development": <0 to {half}>,',
            f'    "Evidence & Subject Accuracy": <0 to {other_half}>',
            f'  }},',
        ]
        return "\n".join(schema_lines), criterion_maxes

    total_weight = sum(c["weight"] for c in content_criteria)
    if total_weight <= 0:
        total_weight = 100.0

    criterion_maxes = {}
    allocated = 0.0
    for i, c in enumerate(content_criteria):
        if i == len(content_criteria) - 1:
            # Last criterion absorbs any rounding remainder
            pts = round(s3_max - allocated, 1)
        else:
            pts = round((c["weight"] / total_weight) * s3_max, 1)
        criterion_maxes[c["name"]] = pts
        allocated += pts

    schema_lines = ['  "criterion_scores": {']
    names = list(criterion_maxes.keys())
    for i, name in enumerate(names):
        comma = "," if i < len(names) - 1 else ""
        schema_lines.append(f'    "{name}": <0 to {criterion_maxes[name]}>{comma}')
    schema_lines.append("  },")

    return "\n".join(schema_lines), criterion_maxes


def _build_stage3_criteria_detail(content_criteria: list[dict], criterion_maxes: dict) -> str:
    """
    Formats the per-criterion scoring table for the Stage 3 prompt body.
    """
    if not content_criteria:
        lines = []
        for name, max_pts in criterion_maxes.items():
            lines.append(f"  - {name}: max {max_pts} pts")
        return "\n".join(lines)

    lines = []
    for c in content_criteria:
        max_pts = criterion_maxes.get(c["name"], 0)
        desc = f" — {c['descriptor']}" if c.get("descriptor") else ""
        lines.append(f"  - {c['name']}: max {max_pts} pts (rubric weight {c['weight']}%){desc}")
    return "\n".join(lines)


def _build_stage3_fallback_prompt(essay_text: str, assignment, s3_max: float,
                                   content_criteria: list[dict] = None) -> str:
    title              = getattr(assignment, "title", "Untitled")
    instructions       = getattr(assignment, "instructions", "")
    rubric_text        = _format_assignment_rubric(assignment)
    rubric_content     = getattr(assignment, "rubric_content", "") or ""
    reference_material = getattr(assignment, "reference_material", "") or ""

    rubric_key_block = (
        f"MARKING KEY / RUBRIC DOCUMENT (grade against this directly):\n{rubric_content[:2000]}\n\n"
        if rubric_content.strip() else ""
    )
    ref_block = (
        f"REFERENCE MATERIAL (for factual context):\n{reference_material[:1500]}\n\n"
        if reference_material.strip() else ""
    )

    schema_desc, criterion_maxes = _build_criterion_scoring_schema(content_criteria or [], s3_max)
    criteria_detail = _build_stage3_criteria_detail(content_criteria or [], criterion_maxes)

    return (
        f"You are a strict academic grader. Grade this essay against each criterion below.\n\n"
        f"Assignment Title: {title}\n"
        f"Assignment Instructions: {instructions}\n\n"
        f"Rubric criteria:\n{rubric_text}\n\n"
        f"{rubric_key_block}"
        f"{ref_block}"
        f"CRITERIA TO SCORE INDIVIDUALLY (max points shown per criterion):\n{criteria_detail}\n\n"
        f"Student Essay:\n{essay_text[:3000]}\n\n"
        f"Respond in valid JSON only — one score per criterion, then a critique:\n"
        f"{{\n"
        f"{schema_desc}\n"
        f'  "comprehensive_critique": "<detailed critique>"\n'
        f"}}"
    )


def _call_groq_once(groq_client, model_name: str, prompt: str) -> dict:
    response = groq_client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.0,
        seed=42,
    )
    return clean_and_parse_json(response.choices[0].message.content.strip())


def _sum_criterion_scores(parsed: dict, criterion_maxes: dict) -> tuple[float, dict]:
    """
    Extracts per-criterion scores from the model response, clamps each to its
    maximum, and returns (total, {name: clamped_score}).

    Handles both:
      {"criterion_scores": {"Name": 12.5, ...}, ...}
      {"Name": 12.5, ...}  (flat — older fallback format)
    """
    raw_scores = parsed.get("criterion_scores", {})
    if not raw_scores:
        # Try flat format
        raw_scores = {k: parsed[k] for k in criterion_maxes if k in parsed}

    clamped = {}
    total = 0.0
    for name, max_pts in criterion_maxes.items():
        raw = raw_scores.get(name, max_pts * 0.5)
        try:
            score = max(0.0, min(float(max_pts), float(raw)))
        except (TypeError, ValueError):
            score = max_pts * 0.5
        clamped[name] = round(score, 1)
        total += clamped[name]

    return round(total, 1), clamped


def _apply_partial_topic_cap(clamped_scores: dict, criterion_maxes: dict,
                              content_criteria: list[dict]) -> dict:
    """
    For partially-on-topic essays: cap each evidence-type criterion at 60% of
    its maximum. Evidence-type criteria are those whose name or descriptor
    contains evidence-related keywords.
    """
    EVIDENCE_KEYWORDS = {
        "evidence", "example", "case study", "research", "data", "framework",
        "theory", "application", "accuracy", "subject", "content", "knowledge",
    }
    capped = dict(clamped_scores)
    for c in content_criteria:
        name = c["name"]
        combined = (name + " " + c.get("descriptor", "")).lower()
        if any(kw in combined for kw in EVIDENCE_KEYWORDS):
            cap = round(criterion_maxes[name] * 0.60, 1)
            if capped.get(name, 0) > cap:
                print(f"[Stage 3] Partial-topic cap applied to '{name}': {capped[name]} -> {cap}")
                capped[name] = cap
    return capped


def call_gemini_stage3(
    essay_text: str,
    assignment,
    word_count: int,
    s3_max: float,
    content_criteria: list = None,
    rubric_content: str = "",
    reference_material: str = "",
    stage0_checklist: list[str] = None,
    is_partially_on_topic: bool = False,
) -> dict:
    """
    Stage 3: Scores EACH content criterion individually by name and weight.

    v5.3 change: The model now returns a "criterion_scores" dict keyed by the
    exact criterion names from the rubric. The pipeline converts these to a
    weighted sum for the Stage 3 total. This replaces the fixed
    thesis_score / evidence_score two-bucket approach.

    Returns a dict containing:
        "criterion_scores":      {name: clamped_score, ...}
        "criterion_maxes":       {name: max_pts, ...}
        "s3_total":              float
        "comprehensive_critique": str
    """
    title        = getattr(assignment, "title", "Untitled")
    instructions = getattr(assignment, "instructions", "")

    # Build per-criterion schema and point maxes from the rubric
    schema_desc, criterion_maxes = _build_criterion_scoring_schema(content_criteria or [], s3_max)
    criteria_detail = _build_stage3_criteria_detail(content_criteria or [], criterion_maxes)

    checklist_block = "No explicit checklist items available from Stage 0."
    if stage0_checklist:
        checklist_block = "\n".join([f"  - {item}" for item in stage0_checklist])

    rubric_key_block = ""
    if rubric_content and rubric_content.strip():
        rubric_key_block = (
            f"\nMARKING KEY / RUBRIC DOCUMENT (PRIMARY grading anchor — "
            f"grade against this directly):\n{rubric_content[:3000]}\n"
        )

    ref_block = ""
    if reference_material and reference_material.strip():
        ref_block = (
            f"\nREFERENCE MATERIAL (use to verify factual accuracy of essay claims):\n"
            f"{reference_material[:2000]}\n"
        )

    # Quality benchmarks
    excellent_pct = round(s3_max * 0.90, 0)
    good_pct      = round(s3_max * 0.70, 0)
    moderate_pct  = round(s3_max * 0.50, 0)
    weak_pct      = round(s3_max * 0.30, 0)

    partial_note = (
        f"\nNOTE: This essay was classified as partially on-topic. "
        f"Evidence-type criteria will be capped at 60% of their individual maximums.\n"
        if is_partially_on_topic else ""
    )

    # Build per-criterion deduction guidance
    deduction_examples = []
    for name, max_pts in criterion_maxes.items():
        deduction_examples.append(
            f"  - {name}: cap at {round(max_pts * 0.5, 1)} if the core requirement is absent or "
            f"at {round(max_pts * 0.6, 1)} if partially addressed"
        )
    deduction_block = "\n".join(deduction_examples)

    prompt = f"""You are a strict senior academic professor grading against the exact criteria below.
Grade ONLY the content, argument quality, and subject-matter accuracy.
Do NOT re-assess grammar, vocabulary, or basic topic relevance — those are handled by other stages.

ASSIGNMENT TITLE: {title}
ASSIGNMENT INSTRUCTIONS: {instructions}
{rubric_key_block}{ref_block}{partial_note}
CRITERIA TO SCORE (score each criterion individually — do NOT combine them):
{criteria_detail}

CHECKLIST ITEMS FOR VERIFICATION:
{checklist_block}

For each checklist item above, state in your critique whether it is Present, Partially Present,
or Missing. Missing checklist items must result in visible score reductions on the relevant criterion.

STUDENT ESSAY:
{essay_text}
WORD COUNT: {word_count}

OVERALL QUALITY BENCHMARKS (total out of {s3_max}):
  Excellent  (>={excellent_pct}): All criteria met at distinction level. Specific frameworks named and explained. Critical analysis present. Conclusion synthesises rather than restates.
  Good       (>={good_pct}):     Most criteria met. Some frameworks used. Minor gaps in depth or evidence.
  Moderate   (>={moderate_pct}):  Some criteria met. Descriptive rather than analytical. Basic frameworks only. Thin evidence.
  Weak       (>={weak_pct}):     Few criteria met. Missing key required content. No named frameworks. No evidence.
  Very Weak  (<{weak_pct}):     Criteria largely unmet. Fundamental errors. No analytical engagement.

MANDATORY DEDUCTIONS — apply BEFORE finalising each criterion score:

For each criterion, apply proportional caps based on what is absent:
{deduction_block}

Universal deduction rules:
1. MISSING REQUIRED TERMINOLOGY: If the essay lacks subject-specific vocabulary explicitly
   required by the marking key -> cap affected criterion at 50% of its maximum.
2. NO EVIDENCE / EXAMPLES: Zero concrete examples, named theories, or case studies
   -> cap evidence/application criteria at 60% of their maximums.
3. VAGUE / ABSENT THESIS: No clear arguable claim in the introduction
   -> cap argument/thesis criteria at 50% of their maximums.
4. SHALLOW CONCLUSION: Conclusion only restates introduction
   -> deduct 8% from thesis/argument criteria.
5. REPETITIVE BODY: 3+ paragraphs making the same point
   -> deduct 18% from thesis/argument criteria.

These caps are proportional and subject-neutral. Do NOT invent penalties not implied by the criteria.

Your comprehensive_critique MUST:
1. Address each criterion individually — state what was met, partially met, or missed
2. State which deductions/caps were triggered on which criterion and why
3. Reference specific passages or their absence as evidence
4. State what the essay would need to reach the next quality band per criterion

Respond with ONLY a valid JSON object — use the exact criterion names as keys:
{{
{schema_desc}
  "comprehensive_critique": "Detailed per-criterion critique here."
}}

Scoring constraints:
{chr(10).join(f'  - "{name}" must be between 0 and {max_pts}' for name, max_pts in criterion_maxes.items())}"""

    # PRIMARY: Groq
    if GROQ_SDK_AVAILABLE and GROQ_API_KEY:
        GROQ_MODELS = [
            "llama-3.3-70b-versatile",
            "llama-3.1-70b-versatile",
            "mixtral-8x7b-32768",
        ]
        groq_client = Groq(api_key=GROQ_API_KEY)

        for model_name in GROQ_MODELS:
            try:
                print(f"[Stage 3] Trying Groq -> {model_name}")
                parsed = _call_groq_once(groq_client, model_name, prompt)

                s3_total, clamped_scores = _sum_criterion_scores(parsed, criterion_maxes)

                # Apply partial topic cap per criterion
                if is_partially_on_topic and content_criteria:
                    clamped_scores = _apply_partial_topic_cap(
                        clamped_scores, criterion_maxes, content_criteria
                    )
                    s3_total = round(sum(clamped_scores.values()), 1)

                # Second-pass for long essays scoring unexpectedly low
                if word_count >= 600 and s3_total < (s3_max * 0.53):
                    print(f"[Stage 3] Long essay scored low ({s3_total}/{s3_max}) — verification pass...")
                    try:
                        parsed2 = _call_groq_once(groq_client, model_name, prompt)
                        s3_total2, clamped_scores2 = _sum_criterion_scores(parsed2, criterion_maxes)
                        if is_partially_on_topic and content_criteria:
                            clamped_scores2 = _apply_partial_topic_cap(
                                clamped_scores2, criterion_maxes, content_criteria
                            )
                            s3_total2 = round(sum(clamped_scores2.values()), 1)

                        if s3_total2 > s3_total:
                            s3_total = s3_total2
                            clamped_scores = clamped_scores2
                            parsed["comprehensive_critique"] = parsed2.get(
                                "comprehensive_critique",
                                parsed.get("comprehensive_critique", "")
                            )
                            print(f"[Stage 3] Pass 2 higher ({s3_total}/{s3_max}) — using Pass 2")
                        else:
                            print(f"[Stage 3] Pass 1 held ({s3_total}/{s3_max})")
                    except Exception as ve:
                        print(f"[Stage 3] Verification pass failed: {ve}")

                print(f"[Stage 3] Groq graded with {model_name} — {s3_total}/{s3_max}")
                return {
                    "criterion_scores":      clamped_scores,
                    "criterion_maxes":       criterion_maxes,
                    "s3_total":              s3_total,
                    "comprehensive_critique": parsed.get("comprehensive_critique", ""),
                }

            except Exception as e:
                print(f"[Stage 3] Groq error on {model_name}: {e} — next...")
                continue

        print("[Stage 3] All Groq models failed — HF fallback...")
    else:
        print("[Stage 3] Groq unavailable — HF fallback...")

    # FALLBACK: HF router — returns a flat score, split proportionally
    fallback_prompt = _build_stage3_fallback_prompt(
        essay_text, assignment, s3_max, content_criteria
    )
    raw_hf   = call_huggingface(fallback_prompt)
    s3_total = s3_max * 0.50

    try:
        parsed_hf = clean_and_parse_json(raw_hf)
        _, clamped_scores = _sum_criterion_scores(parsed_hf, criterion_maxes)
        if is_partially_on_topic and content_criteria:
            clamped_scores = _apply_partial_topic_cap(
                clamped_scores, criterion_maxes, content_criteria
            )
        s3_total = round(sum(clamped_scores.values()), 1)
        critique = _strip_markdown(parsed_hf.get("comprehensive_critique", raw_hf[:800]))
    except Exception:
        # Last resort: parse a "Score: X/Y" pattern and split evenly
        num_match = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s3_max)}", raw_hf)
        if num_match:
            s3_total = min(s3_max, max(0.0, float(num_match.group(1))))
        frac = s3_total / s3_max if s3_max > 0 else 0.5
        clamped_scores = {name: round(max_pts * frac, 1) for name, max_pts in criterion_maxes.items()}
        if is_partially_on_topic and content_criteria:
            clamped_scores = _apply_partial_topic_cap(
                clamped_scores, criterion_maxes, content_criteria
            )
        s3_total = round(sum(clamped_scores.values()), 1)
        critique = _strip_markdown(raw_hf[:800])

    return {
        "criterion_scores":      clamped_scores,
        "criterion_maxes":       criterion_maxes,
        "s3_total":              s3_total,
        "comprehensive_critique": critique,
    }


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY FALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

def call_huggingface(prompt: str) -> str:
    last_error = None
    for model in HF_MODELS:
        try:
            print(f"Trying HF model: {model['name']}...")
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
                print(f"{model['name']} responded successfully")
                return result
        except Exception as e:
            print(f"{model['name']} failed: {e}")
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
    criterion_scores: dict,
    criterion_maxes: dict,
    final_score: int,
    max_score: int,
    classification: str,
) -> str:
    def pct_bar(score, maximum):
        if maximum <= 0:
            return "[" + " " * 20 + "]"
        filled = int((score / maximum) * 20)
        return "[" + "#" * filled + " " * (20 - filled) + "]"

    total_pts = round(s1_points + s2_coherence + s3_total, 1)
    topic_str = classification.replace("_", " ").title()

    lines = [
        "Academic Evaluation Report",
        "=" * 40,
        f"Overall Score : {final_score} / {max_score}",
        f"Topic Status  : {topic_str}",
        "",
        f"{'Stage':<10} {'Criteria':<35} {'Score':>6}  {'Max':>5}  Bar",
        "-" * 75,
        f"{'Stage 1':<10} {s1_label:<35} {round(s1_points, 1):>6}  {s1_max:>5}  {pct_bar(s1_points, s1_max)}",
        f"{'Stage 2':<10} {s2_label:<35} {round(s2_coherence, 1):>6}  {s2_max:>5}  {pct_bar(s2_coherence, s2_max)}",
        f"{'Stage 3':<10} {s3_label:<35} {round(s3_total, 1):>6}  {s3_max:>5}  {pct_bar(s3_total, s3_max)}",
    ]

    # Per-criterion breakdown rows under Stage 3
    if criterion_scores and criterion_maxes:
        for name, score in criterion_scores.items():
            max_pts = criterion_maxes.get(name, 0)
            indent_name = f"  ↳ {name}"
            lines.append(
                f"{'':10} {indent_name:<35} {score:>6}  {max_pts:>5}  {pct_bar(score, max_pts)}"
            )

    lines += [
        "-" * 75,
        f"{'Total':<10} {'(weighted)':<35} {total_pts:>6}  {'100':>5}",
        "",
        "=" * 40,
        "Detailed Stage Feedback",
        "=" * 40,
        "",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────

def grade_with_ai(prompt: str, assignment=None, essay_text: str = "", word_count: int = 0, enable_stage0: bool = True) -> dict:
    """
    v5.3 Rubric-aware ensemble grading pipeline with per-criterion Stage 3 scoring.

    Stage weights are computed from the teacher's actual marking key / rubric.
    Each model grades only the criteria that belong to its specialty:
      Stage 1 (Qwen IELTS)     -> linguistics criteria (grammar, vocabulary, terminology)
      Stage 2 (Qwen coherence) -> coherence criteria   (structure, flow, organisation)
      Stage 3 (Groq)           -> content criteria, scored per criterion individually

    Stage 3 change (v5.3):
      Instead of collapsing content criteria into thesis_score + evidence_score,
      Groq now scores each content criterion by its exact rubric name. The Stage 3
      total is the sum of those per-criterion scores. The score breakdown header
      shows one row per criterion so teachers can see exactly where marks were lost.

    Stage 0 optionally extracts an explicit rubric checklist that Stage 3 verifies
    per-criterion.

    If no rubric/marking key is provided, defaults to 15 / 10 / 75 weights and
    two generic content buckets (Thesis & Argument / Evidence & Accuracy).
    """
    max_score = getattr(assignment, "max_score", None) or 100

    master_title        = getattr(assignment, "title", "") or ""
    master_instructions = getattr(assignment, "instructions", "") or ""
    rubric_content      = getattr(assignment, "rubric_content", "") or ""
    reference_material  = getattr(assignment, "reference_material", "") or ""

    if not master_title and prompt:
        master_title = prompt

    if not essay_text or word_count < 15:
        return {
            "score": 0,
            "feedback": "Submission content unreadable or below minimum grading length.",
            "off_topic": False, "ai_detected": False,
            "low_confidence": True, "graded_by": "pipeline-pre-rejection",
        }

    # STEP 0: Resolve dynamic stage weights from marking key / rubric
    s1_max, s2_max, s3_max, classified = resolve_stage_weights(assignment)
    linguistics_criteria = classified.get("linguistics", [])
    coherence_criteria   = classified.get("coherence", [])
    content_criteria     = classified.get("content", [])

    # cumulative_feedback initialised ONCE here
    cumulative_feedback = []

    stage0_checklist = []
    if enable_stage0:
        stage0_checklist = extract_stage0_checklist(assignment, content_criteria, rubric_content)

    if stage0_checklist:
        cumulative_feedback.append(
            "Stage 0: Rubric Checklist\n"
            "The following explicit checklist items were derived from the marking key "
            "and will be verified by Stage 3:\n"
            f"{_format_stage0_checklist(stage0_checklist)}\n\n"
            f"{'—' * 60}\n\n"
        )
    elif enable_stage0:
        cumulative_feedback.append(
            "Stage 0: Rubric Checklist\n"
            "No explicit checklist items could be extracted from the marking key or rubric.\n\n"
            f"{'—' * 60}\n\n"
        )

    # Human-readable labels for score breakdown
    s1_label = " & ".join(c["name"] for c in linguistics_criteria) if linguistics_criteria else "Language, Grammar & Vocabulary"
    s2_label = " & ".join(c["name"] for c in coherence_criteria)   if coherence_criteria   else "Topic Relevance & Coherence"
    s3_label = " & ".join(c["name"] for c in content_criteria)     if content_criteria     else "Thesis, Structure & Evidence"

    print(f"[Pipeline v5.3] Stage weights: S1={s1_max}pts | S2={s2_max}pts | S3={s3_max}pts")

    running_points         = 0.0
    hard_off_topic_tripped = False
    s1_points              = 0.0
    s2_coherence_final     = 0.0
    s3_total_final         = 0.0
    s3_criterion_scores    = {}
    s3_criterion_maxes     = {}
    classification         = "completely_on_topic"
    is_partially_on_topic  = False
    qwen_data              = {}

    # ══════════════════════════════════════════════════════════════
    # STAGE 1 — LINGUISTICS  (s1_max pts)
    # ══════════════════════════════════════════════════════════════
    try:
        raw_phi3, model_name = call_phi3_ielts(essay_text, assignment, linguistics_criteria)
        s1_points, s1_fb     = parse_phi3_stage1_response(raw_phi3, s1_max)
        running_points      += s1_points
        cumulative_feedback.append(s1_fb)
        print(f"Stage 1 complete — {s1_points}/{s1_max} pts via {model_name}")
    except Exception as s1_err:
        print(f"Stage 1 failed: {s1_err}. Trying fallback router...")
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
                f"Stage 1: {s1_label} (Router Fallback)\n"
                f"Score: {s1_points} / {s1_max} pts\n\n"
                f"Comments: {_strip_markdown(raw_hf[:400])}\n\n"
                f"{'—' * 60}\n\n"
            )
        except Exception:
            floor     = round(s1_max * 0.20, 1)
            s1_points = max(floor, min(s1_max * 0.67, (word_count / 400) * s1_max) if word_count >= 350 else s1_max * 0.33)
            running_points += s1_points
            cumulative_feedback.append(
                f"Stage 1: {s1_label} (Emergency Baseline)\n"
                f"Score: {s1_points} / {s1_max} pts\n\n"
                f"System timed out. Conservative score applied.\n\n"
                f"{'—' * 60}\n\n"
            )

    # ══════════════════════════════════════════════════════════════
    # STAGE 2 — COHERENCE  (s2_max pts)
    # ══════════════════════════════════════════════════════════════
    try:
        qwen_data    = run_stage2_qwen(
            essay_text, master_title, master_instructions,
            s2_max, coherence_criteria
        )
        s2_coherence  = float(qwen_data.get("coherence_score", s2_max * 0.5))
        s2_coherence  = max(0.0, min(s2_max, s2_coherence))
        classification = str(qwen_data.get("relevance_classification", "completely_on_topic")).lower().strip()

        if (classification == "completely_off_topic" or
                qwen_data.get("is_on_topic") is False or
                s2_coherence <= (s2_max * 0.20)):
            s2_coherence           = 0.0
            hard_off_topic_tripped = True
            classification         = "completely_off_topic"
        elif "partially_on_topic" in classification:
            s2_coherence          = min(s2_coherence, round(s2_max * 0.60, 1))
            is_partially_on_topic = True

        s2_coherence       = max(0.0, min(s2_max, s2_coherence))
        s2_coherence_final = s2_coherence
        running_points    += s2_coherence

        s2_fb = (
            f"Stage 2: {s2_label}\n"
            f"Model: Qwen 2.5 72B\n"
            f"Score: {s2_coherence} / {s2_max} pts\n"
            f"Topic Classification: {classification.replace('_', ' ').title()}\n\n"
            f"Evaluator Rationale:\n{_strip_markdown(str(qwen_data.get('justification', '')))}\n\n"
            f"{'—' * 60}\n\n"
        )
        cumulative_feedback.append(s2_fb)
        print(f"Stage 2 complete — {s2_coherence}/{s2_max} pts | {classification}")

    except Exception as s2_err:
        s2_coherence        = round(s2_max * 0.55, 1)
        s2_coherence_final  = s2_coherence
        running_points     += s2_coherence
        cumulative_feedback.append(
            f"Stage 2: {s2_label}\n"
            f"Score: {s2_coherence} / {s2_max} pts\n\n"
            f"System timeout. Conservative baseline applied.\n\n"
            f"{'—' * 60}\n\n"
        )

    # CIRCUIT BREAKER: Off-topic -> skip Stage 3
    if hard_off_topic_tripped:
        final_score = round((running_points / 100.0) * max_score)
        final_score = max(0, min(max_score, final_score))

        breakdown_header = _build_score_breakdown(
            s1_points, s1_max, s1_label,
            0.0, s2_max, s2_label,
            0.0, s3_max, s3_label,
            {}, {},
            final_score, max_score, "completely_off_topic"
        )

        fail_rationale = _strip_markdown(str(qwen_data.get(
            "justification",
            f"Submission dropped. Content lacks thematic relationship to: '{master_title}'."
        )))
        cumulative_feedback.append(
            f"Stage 3: {s3_label}\n"
            f"Score: 0 / {s3_max} pts — BYPASSED\n\n"
            f"Grading halted: essay topic does not match the assignment prompt.\n\n"
            f"Pipeline stopped: {fail_rationale}\n\n"
            f"{'—' * 60}\n\n"
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
    # STAGE 3 — CONTENT & LOGIC  (s3_max pts, per-criterion)
    # ══════════════════════════════════════════════════════════════
    try:
        gemini_data = call_gemini_stage3(
            essay_text, assignment, word_count,
            s3_max,
            content_criteria,
            rubric_content=rubric_content,
            reference_material=reference_material,
            stage0_checklist=stage0_checklist,
            is_partially_on_topic=is_partially_on_topic,
        )

        s3_criterion_scores = gemini_data.get("criterion_scores", {})
        s3_criterion_maxes  = gemini_data.get("criterion_maxes", {})
        s3_total            = gemini_data.get("s3_total", s3_max * 0.5)

        s3_total_final  = s3_total
        running_points += s3_total

        # Build per-criterion lines for the feedback block
        criterion_lines = "\n".join(
            f"  {name:<40}: {score} / {s3_criterion_maxes.get(name, '?')} pts"
            for name, score in s3_criterion_scores.items()
        )

        s3_fb = (
            f"Stage 3: {s3_label}\n"
            f"Model: Groq (llama-3.3-70b-versatile)\n"
            f"Per-Criterion Scores:\n{criterion_lines}\n"
            f"Stage 3 Total                : {round(s3_total, 1)} / {s3_max} pts\n\n"
            f"Detailed Critique:\n{_strip_markdown(str(gemini_data.get('comprehensive_critique', '')))}\n\n"
            f"{'—' * 60}\n\n"
        )
        cumulative_feedback.append(s3_fb)
        print(f"Stage 3 complete — {s3_total}/{s3_max} pts ({len(s3_criterion_scores)} criteria)")

    except Exception as s3_err:
        print(f"Stage 3 failed: {s3_err}. Falling back to Qwen-72B router...")
        try:
            fallback_prompt = prompt or _build_stage3_fallback_prompt(
                essay_text, assignment, s3_max, content_criteria
            )
            raw_hf    = call_huggingface(fallback_prompt)
            s3_total  = round(s3_max * 0.50, 1)
            feedback_text = _strip_markdown(raw_hf.strip())

            try:
                parsed = clean_and_parse_json(raw_hf)
                if parsed:
                    _, clamped = _sum_criterion_scores(parsed, s3_criterion_maxes or {})
                    if clamped:
                        s3_criterion_scores = clamped
                        s3_total = round(sum(clamped.values()), 1)
                    feedback_text = _strip_markdown(parsed.get("comprehensive_critique", feedback_text))
            except Exception:
                num_match = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s3_max)}", raw_hf)
                if num_match:
                    s3_total = min(s3_max, max(0.0, float(num_match.group(1))))

            if is_partially_on_topic and content_criteria and s3_criterion_scores:
                s3_criterion_scores = _apply_partial_topic_cap(
                    s3_criterion_scores, s3_criterion_maxes, content_criteria
                )
                s3_total = round(sum(s3_criterion_scores.values()), 1)

            s3_total_final  = s3_total
            running_points += s3_total
            cumulative_feedback.append(
                f"Stage 3: {s3_label} (Qwen Router Fallback)\n"
                f"Score: {s3_total} / {s3_max} pts\n\n"
                f"Feedback:\n{feedback_text[:700]}\n\n"
                f"{'—' * 60}\n\n"
            )
        except Exception:
            s3_total_final  = round(s3_max * 0.40, 1)
            running_points += s3_total_final
            cumulative_feedback.append(
                f"Stage 3: {s3_label} (Emergency Default)\n"
                f"Score: {s3_total_final} / {s3_max} pts\n\n"
                f"System unavailable. Conservative score applied.\n\n"
                f"{'—' * 60}\n\n"
            )

    # Final score assembly
    final_scaled_score = round((running_points / 100.0) * max_score)
    final_scaled_score = max(0, min(max_score, final_scaled_score))

    breakdown_header = _build_score_breakdown(
        s1_points, s1_max, s1_label,
        s2_coherence_final, s2_max, s2_label,
        s3_total_final, s3_max, s3_label,
        s3_criterion_scores, s3_criterion_maxes,
        final_scaled_score, max_score, classification
    )

    return {
        "score":          final_scaled_score,
        "feedback":       breakdown_header + "".join(cumulative_feedback).strip(),
        "off_topic":      False,
        "ai_detected":    False,
        "low_confidence": False,
        "graded_by":      "ensemble-pipeline-engine-v5.3",
    }