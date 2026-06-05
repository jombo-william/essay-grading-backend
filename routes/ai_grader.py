# routes/ai_grader.py
"""
ENSEMBLE GRADING CHAIN v5.3 — PER-CRITERION SCORING
═══════════════════════════════════════════════════════════

FIXED IN v5.1:
  - FIX 1: cumulative_feedback double-init bug removed (Stage 0 feedback no longer lost)
  - FIX 2: Stage 3 JSON keys now use generic names (thesis_score / evidence_score)
            so the model is not confused when s3_max != 75
  - FIX 3: coherence_score field renamed to coherence_score (no "out_of_10" suffix)
            so the model respects the actual s2_max from the prompt, not 10
  - FIX 4: s3_thesis_max / s3_evidence_max computed once in grade_with_ai() and
            passed into call_gemini_stage3() — no more dual computation drift
  - FIX 5: Partial-topic cap applied in Stage 3 as well as Stage 2
  - FIX 6: _strip_markdown() is now applied to all AI-generated free-text fields
            before they are embedded in feedback (no raw markdown in output)
  - FIX 7: classify_rubric_criteria() adds a comment explaining silent linguistics-first
            priority when a criterion matches both buckets

NEW IN v5.3:
  - Stage 3 scores EACH content criterion individually, not as a
    collapsed thesis/evidence split. Groq receives every criterion with
    its exact max points and returns a score per criterion.
  - Weighted sum of criterion scores = Stage 3 total (deterministic math,
    not a holistic judgment).
  - Second-pass verification compares per-criterion scores, taking the
    higher score per individual criterion (not just overall total).
  - Feedback shows a per-criterion score table so students see exactly
    where marks were lost on each rubric item.
  - Fallback (no rubric / HF router) collapses to proportional split.
  - _build_score_breakdown shows per-criterion sub-rows under Stage 3.

NEW IN v5.2:
  - rubric_content (marking key) and reference_material (study docs) are now
    read as SEPARATE fields matching the new AssignmentForm layout:
      assignment.rubric_content    → marking key / grading rubric
      assignment.reference_material → reference books, notes, study material
  - Stage 0 uses ONLY rubric_content for checklist extraction (cleaner signal)
  - Stage 3 receives rubric_content as primary grading anchor and
    reference_material as secondary context (labelled separately in prompt)
  - _build_stage3_fallback_prompt updated with same split
  - resolve_stage_weights already correct (reads rubric_content) — no change

PRESERVED FROM v5.0:
  - Dynamic stage weight extraction from rubric / reference material
  - Stage 0 checklist extraction
  - FIX 1-5 from v4.2 (band scale, partial cap, second-pass, breakdown, floors)
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
    # Note: "introduction" and "conclusion" removed — too broad,
    # would incorrectly catch "introduction of examples" etc.
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

def _normalise_to_weights(criteria: list[dict]) -> list[dict]:
    """
    Converts raw mark values to percentage weights.
    If values already sum to ~100 they are treated as percentages.
    Otherwise they are normalised: weight = (mark / total) * 100.
    Modifies in place and returns the list.
    """
    total = sum(c["weight"] for c in criteria)
    if total <= 0:
        return criteria
    # Already percentages — no conversion needed
    if abs(total - 100.0) <= 2.0:
        return criteria
    # Raw marks — normalise to percentages
    print(f"[RubricParser] Raw marks detected (sum={total}) — normalising to %")
    for c in criteria:
        c["weight"] = round(c["weight"] / total * 100.0, 2)
        c["raw_mark"] = c.get("raw_mark", c["weight"])   # preserve original
    return criteria


def parse_marking_key_from_reference(reference_material: str) -> list[dict]:
    """
    Extracts grading criteria from free-text marking key / rubric.

    Handles ALL common teacher formats — no specific template required:

      Format A — Markdown table with %:
        | Content & Accuracy | 25% | Correct identification... |

      Format B — Markdown table with raw marks:
        | Task Achievement | 10 | Accurate scientific content... |

      Format C — Plain table (PDF-extracted, spaces/tabs):
        Task Achievement   10   Accurate and relevant scientific content
        Coherence          10   Clear introduction, body, conclusion

      Format D — Percentage lines:
        Content & Accuracy   25%   Correct identification...

      Format E — Simple colon/dash format:
        Content & Accuracy: 25%
        Grammar - 15%

      Format F — "X marks" or "X / Y" inline:
        Task Achievement (10 marks): Accurate scientific content
        Grammar /Style (10/40): Grammatical accuracy

    Returns:
        [{"name": str, "weight": float, "descriptor": str, "raw_mark": float|None}]
        weights are always percentages (normalised if raw marks were given)

    Returns [] if no criteria found — caller falls back to rubric dict or defaults.
    """
    if not reference_material or not reference_material.strip():
        return []

    criteria = []
    seen_names = set()

    def add(name, weight, descriptor="", raw_mark=None):
        name = name.strip().rstrip("-|:/").strip()
        if not name or len(name) < 3:
            return
        if re.match(r"^[-=# ]+$", name):
            return
        # Skip obvious header rows
        if name.lower() in {"criterion", "criteria", "category", "component", "item",
                             "marks", "score", "weight", "total", "band", "grade"}:
            return
        key = name.lower()[:30]
        if key in seen_names:
            return
        seen_names.add(key)
        if weight <= 0:
            return
        criteria.append({
            "name":      name,
            "weight":    float(weight),
            "descriptor": descriptor.strip(),
            "raw_mark":  raw_mark,
        })

    # ── Strategy 1A: Markdown table — percentage weights ─────────────────────
    # | Criterion Name | 25% | descriptor |
    pat_md_pct = re.compile(
        r"\|\s*([^|]{3,60}?)\s*\|\s*(\d+(?:\.\d+)?)\s*%\s*\|([^|]*)",
        re.MULTILINE
    )
    for m in pat_md_pct.finditer(reference_material):
        name, weight, desc = m.group(1), float(m.group(2)), m.group(3)
        if re.match(r"^[-: ]+$", name.strip()):
            continue
        add(name, weight, desc)

    if criteria:
        print(f"[RubricParser] Strategy 1A (markdown table %): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    # ── Strategy 1B: Markdown table — raw marks ───────────────────────────────
    # | Criterion Name | 10 | descriptor |
    pat_md_raw = re.compile(
        r"\|\s*([^|]{3,60}?)\s*\|\s*(\d+(?:\.\d+)?)\s*\|([^|]*)",
        re.MULTILINE
    )
    for m in pat_md_raw.finditer(reference_material):
        name, weight, desc = m.group(1).strip(), float(m.group(2)), m.group(3)
        if re.match(r"^[-: ]+$", name):
            continue
        # Skip separator rows and rows where "name" is a number
        if re.match(r"^[\d.]+$", name):
            continue
        if weight > 200:   # implausible mark value
            continue
        add(name, weight, desc, raw_mark=weight)

    if criteria:
        print(f"[RubricParser] Strategy 1B (markdown table raw marks): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    # ── Strategy 2A: Plain table — raw marks (PDF-style, spaces/tabs) ─────────
    # Task Achievement   10   Accurate and relevant scientific content
    # Coherence          10   Clear introduction, body, conclusion
    pat_plain_raw = re.compile(
        r"^([A-Za-z][^\n\d]{2,55}?)\s{2,}(\d+(?:\.\d+)?)\s{2,}(.*)$",
        re.MULTILINE
    )
    for m in pat_plain_raw.finditer(reference_material):
        name, weight, desc = m.group(1), float(m.group(2)), m.group(3)
        if weight > 200:
            continue
        add(name, weight, desc, raw_mark=weight)

    if criteria:
        print(f"[RubricParser] Strategy 2A (plain table raw marks): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    # ── Strategy 2B: Plain lines — percentage weights ─────────────────────────
    # Content & Accuracy   25%   Correct identification...
    pat_plain_pct = re.compile(
        r"^([A-Za-z][^\n%]{4,60}?)\s{2,}(\d+(?:\.\d+)?)\s*%\s*(.*)?$",
        re.MULTILINE
    )
    for m in pat_plain_pct.finditer(reference_material):
        name, weight, desc = m.group(1), float(m.group(2)), (m.group(3) or "")
        if weight > 100:
            continue
        add(name, weight, desc)

    if criteria:
        print(f"[RubricParser] Strategy 2B (plain lines %): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    # ── Strategy 3A: Inline marks — "Criterion (X marks): desc" ──────────────
    # Task Achievement (10 marks): Accurate scientific content
    pat_inline_marks = re.compile(
        r"([A-Za-z][^\n(]{3,55}?)\s*\((\d+)\s*marks?\)\s*[:\-]?\s*(.*)",
        re.IGNORECASE | re.MULTILINE
    )
    for m in pat_inline_marks.finditer(reference_material):
        name, weight, desc = m.group(1), float(m.group(2)), m.group(3)
        add(name, weight, desc, raw_mark=weight)

    if criteria:
        print(f"[RubricParser] Strategy 3A (inline marks): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    # ── Strategy 3B: Inline fraction — "Criterion (X/Y): desc" ───────────────
    # Grammar /Style (10/40): Grammatical accuracy
    pat_inline_frac = re.compile(
        r"([A-Za-z][^\n(/]{3,55}?)\s*\((\d+)\s*/\s*(\d+)\)\s*[:\-]?\s*(.*)",
        re.MULTILINE
    )
    for m in pat_inline_frac.finditer(reference_material):
        name, num, desc = m.group(1), float(m.group(2)), m.group(4)
        add(name, num, desc, raw_mark=num)

    if criteria:
        print(f"[RubricParser] Strategy 3B (inline fraction): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    # ── Strategy 4: Simple colon/dash — "Name: 25%" or "Name - 15%" ──────────
    pat_simple = re.compile(
        r"([A-Za-z &/,\-]{4,60}?)[:\-–]\s*(\d+(?:\.\d+)?)\s*%",
        re.MULTILINE
    )
    for m in pat_simple.finditer(reference_material):
        name, weight = m.group(1), float(m.group(2))
        if 0 < weight <= 100:
            add(name, weight)

    if criteria:
        print(f"[RubricParser] Strategy 4 (simple colon/dash): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    # ── Strategy 5: Line-wrapped PDF table (two-pass) ────────────────────────
    # Handles PDF-extracted rubric tables where criterion names are split across
    # multiple lines and the mark appears at the start of the line containing
    # the descriptor:
    #   Task Achievement        ← name line 1
    #   / Content               ← name line 2
    #   10 Accurate and...      ← mark + descriptor start
    #   covers energy...        ← descriptor continuation
    HEADER_SKIP = {
        'criterion', 'marks', 'what to look for', 'scoring rubric', 'total',
        'band', 'performance', 'descriptor', 'score range', 'grade',
    }
    all_lines = reference_material.split('\n')
    # Find all lines that start with a number followed by an uppercase word
    mark_positions = [
        i for i, ln in enumerate(all_lines)
        if re.match(r'^\d+(?:\.\d+)?\s+[A-Z]', ln.strip())
    ]

    if len(mark_positions) >= 2:
        for pos in mark_positions:
            # Walk backwards to collect name lines
            name_parts = []
            j = pos - 1
            while j >= 0:
                prev = all_lines[j].strip()
                if not prev:
                    break
                if re.match(r'^\d+(?:\.\d+)?\s+[A-Z]', prev):
                    break
                lc = prev.lower()
                if any(lc.startswith(hw) for hw in HEADER_SKIP):
                    break
                # Skip lines that are clearly descriptor continuations
                # (start with lowercase, come from far back)
                if re.match(r'^[a-z]', prev) and (pos - j) > 2:
                    break
                name_parts.insert(0, prev)
                j -= 1

            raw_name = ' '.join(name_parts)
            raw_name = re.sub(r'^[/&\s]+', '', raw_name).strip()
            raw_name = re.sub(r'\s+', ' ', raw_name)

            if not raw_name or len(raw_name) < 3:
                continue

            # Extract mark and descriptor from the mark line
            mark_line = all_lines[pos].strip()
            mm = re.match(r'^(\d+(?:\.\d+)?)\s+(.*)', mark_line)
            if not mm:
                continue
            mark    = float(mm.group(1))
            desc_1  = mm.group(2)

            # Collect descriptor continuation lines
            desc_lines = [desc_1]
            k = pos + 1
            while k < len(all_lines):
                nxt = all_lines[k].strip()
                if not nxt:
                    break
                if re.match(r'^\d+(?:\.\d+)?\s+[A-Z]', nxt):
                    break
                # Stop if this looks like the start of the next criterion name
                if re.match(r'^[A-Z]', nxt) and k in [p - 1 for p in mark_positions]:
                    break
                desc_lines.append(nxt)
                k += 1

            descriptor = ' '.join(desc_lines).strip()
            if mark > 0 and mark <= 200:
                add(raw_name, mark, descriptor, raw_mark=mark)

    if criteria:
        print(f"[RubricParser] Strategy 5 (line-wrapped PDF table): {len(criteria)} criteria")
        return _normalise_to_weights(criteria)

    print("[RubricParser] No structured criteria found — will use rubric dict or defaults")
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
    # Get marking key from dedicated rubric_content field, not reference_material
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
    Reference material (books/notes) is intentionally excluded here — it is
    too noisy for checklist extraction. The marking key is the clean source.
    """
    title          = getattr(assignment, "title", "Untitled")
    instructions   = getattr(assignment, "instructions", "")
    rubric_text    = _format_assignment_rubric(assignment)
    criteria_block = _format_criteria_for_prompt(content_criteria or [])

    # Only inject rubric_content if it has real content
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
    Does not use reference_material (study notes) — that field is for
    contextual grading, not for extracting grading requirements.
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

    # Strip markdown from the raw AI response before embedding
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

    FIX v5.1: The returned JSON field is now "coherence_score" (no "out_of_10"
    suffix) so the model respects the actual s2_max stated in the prompt and
    does not silently cap its output at 10.
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

    return (
        f"You are a strict academic grader. Grade this essay out of {s3_max} points.\n\n"
        f"Assignment Title: {title}\n"
        f"Assignment Instructions: {instructions}\n\n"
        f"Rubric criteria:\n{rubric_text}\n\n"
        f"{rubric_key_block}"
        f"{ref_block}"
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


def _compute_criterion_points(criterion: dict, s3_max: float) -> float:
    """Convert a criterion weight% into its proportional points within s3_max."""
    return round(criterion["weight"] / 100.0 * s3_max, 1)


def _build_per_criterion_prompt(
    title, instructions, criteria_with_pts,
    rubric_key_block, ref_block, partial_note,
    essay_text, word_count, stage0_checklist,
):
    """
    Builds a prompt asking Groq to score EACH criterion individually.
    JSON response: one key per criterion name + overall_critique.
    """
    criteria_lines = []
    json_example_lines = []
    for c in criteria_with_pts:
        desc = f" — {c['descriptor']}" if c.get("descriptor") else ""
        criteria_lines.append(
            f'  * {c["name"]} | max {c["max_pts"]} pts ({c["weight"]}%){desc}'
        )
        json_example_lines.append(
            f'  "{c["name"]}": {{"score": {round(c["max_pts"] * 0.70, 1)}, "comment": "brief justification"}}'
        )

    criteria_block_str = "\n".join(criteria_lines)
    json_example_str   = ",\n".join(json_example_lines)

    checklist_section = ""
    if stage0_checklist:
        items = "\n".join(f"  - {item}" for item in stage0_checklist)
        checklist_section = (
            f"\nCHECKLIST FROM MARKING KEY (verify each item against the essay):\n"
            f"{items}\n\n"
            f"For each checklist item: note Present, Partial, or Missing in the relevant criterion comment.\n"
            f"Missing HIGH-weight checklist items must reduce that criterion score significantly.\n"
        )

    return (
        f"You are a strict senior academic professor. Grade this essay criterion by criterion.\n"
        f"Grade ONLY content, argument quality, and subject-matter accuracy.\n"
        f"Do NOT re-assess grammar, vocabulary, or basic structure — handled by other pipeline stages.\n\n"
        f"ASSIGNMENT: {title}\n"
        f"INSTRUCTIONS: {instructions}\n"
        f"{rubric_key_block}{ref_block}{partial_note}"
        f"CRITERIA TO GRADE — score each one independently:\n"
        f"{criteria_block_str}\n"
        f"{checklist_section}\n"
        f"STUDENT ESSAY:\n{essay_text}\n"
        f"WORD COUNT: {word_count}\n\n"
        f"SCORING RULES:\n"
        f"- Score each criterion out of its stated maximum — do not blend criteria\n"
        f"- Completely absent criterion = 0\n"
        f"- Distinction level (all requirements met) = 90-100% of max\n"
        f"- Good (most requirements met, minor gaps) = 70-89% of max\n"
        f"- Moderate (surface treatment, thin evidence) = 40-69% of max\n"
        f"- Weak (missing key elements) = 0-39% of max\n"
        f"- overall_critique must start with checklist summary if checklist provided,\n"
        f"  then explain each criterion: what was present, what was missing\n\n"
        f"Respond with ONLY valid JSON — one key per criterion name plus overall_critique:\n"
        f"{{\n"
        f"{json_example_str},\n"
        f'  "overall_critique": "Summary critique referencing each criterion."\n'
        f"}}\n"
        f"IMPORTANT: every criterion key must appear exactly as written above."
    )


def _extract_criterion_scores(parsed, criteria_with_pts, is_partially_on_topic):
    """
    Extract per-criterion scores from Groq response.
    Returns None if any criterion key is missing (triggers retry).
    """
    criterion_scores = {}
    s3_total = 0.0

    for c in criteria_with_pts:
        name    = c["name"]
        max_pts = c["max_pts"]
        raw     = parsed.get(name)

        if raw is None:
            print(f"[Stage 3] Missing criterion key: '{name}'")
            return None

        if isinstance(raw, dict):
            score   = float(raw.get("score", max_pts * 0.5))
            comment = str(raw.get("comment", ""))
        elif isinstance(raw, (int, float)):
            score, comment = float(raw), ""
        else:
            print(f"[Stage 3] Unexpected type for '{name}': {type(raw)}")
            return None

        score = max(0.0, min(max_pts, score))
        if is_partially_on_topic:
            score = min(score, round(max_pts * 0.60, 1))

        criterion_scores[name] = {
            "score":   round(score, 1),
            "max":     max_pts,
            "comment": _strip_markdown(comment),
        }
        s3_total += score

    return {"criterion_scores": criterion_scores, "s3_total": round(s3_total, 1)}


def _stage3_thesis_evidence_fallback(
    essay_text, assignment, word_count,
    s3_max, s3_thesis_max, s3_evidence_max,
    rubric_key_block, ref_block, partial_note,
    stage0_checklist, is_partially_on_topic,
):
    """v5.2-style fallback when no content_criteria are available."""
    title        = getattr(assignment, "title", "Untitled")
    instructions = getattr(assignment, "instructions", "")
    checklist_block = "No checklist available."
    if stage0_checklist:
        checklist_block = "\n".join(f"  - {i}" for i in stage0_checklist)

    prompt = (
        f"You are a strict senior academic professor.\n"
        f"Grade this essay on thesis quality and evidence quality.\n\n"
        f"ASSIGNMENT: {title}\nINSTRUCTIONS: {instructions}\n"
        f"{rubric_key_block}{ref_block}{partial_note}"
        f"CHECKLIST:\n{checklist_block}\n\n"
        f"STUDENT ESSAY:\n{essay_text}\nWORD COUNT: {word_count}\n\n"
        f"SCORING (total {s3_max}):\n"
        f"  thesis_score:   max {s3_thesis_max}\n"
        f"  evidence_score: max {s3_evidence_max}\n\n"
        f"Respond with ONLY valid JSON:\n"
        f'{{"thesis_score": {round(s3_thesis_max*0.5,1)}, '
        f'"evidence_score": {round(s3_evidence_max*0.5,1)}, '
        f'"comprehensive_critique": "Critique here."}}'
    )

    if GROQ_SDK_AVAILABLE and GROQ_API_KEY:
        groq_client = Groq(api_key=GROQ_API_KEY)
        for model in ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "mixtral-8x7b-32768"]:
            try:
                parsed      = _call_groq_once(groq_client, model, prompt)
                s3_thesis   = max(0.0, min(s3_thesis_max,   float(parsed.get("thesis_score",   s3_thesis_max   * 0.5))))
                s3_evidence = max(0.0, min(s3_evidence_max, float(parsed.get("evidence_score", s3_evidence_max * 0.5))))
                if is_partially_on_topic:
                    s3_evidence = min(s3_evidence, round(s3_evidence_max * 0.60, 1))
                return {
                    "criterion_scores": {},
                    "s3_total":         s3_thesis + s3_evidence,
                    "comprehensive_critique": _strip_markdown(str(parsed.get("comprehensive_critique", ""))),
                    "thesis_score":   s3_thesis,
                    "evidence_score": s3_evidence,
                }
            except Exception as e:
                print(f"[Stage 3 fallback] {model}: {e}")
                continue

    raw_hf   = call_huggingface(f"Grade this essay out of {s3_max}. Respond: 'Score: X/{s3_max}'\n\n{essay_text}")
    s3_total = s3_max * 0.50
    m = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s3_max)}", raw_hf)
    if m:
        s3_total = min(s3_max, max(0.0, float(m.group(1))))
    return {
        "criterion_scores": {},
        "s3_total":         round(s3_total, 1),
        "comprehensive_critique": _strip_markdown(raw_hf[:800]),
        "thesis_score":   round(s3_total * (s3_thesis_max / s3_max), 1),
        "evidence_score": round(s3_total * (s3_evidence_max / s3_max), 1),
    }


def call_gemini_stage3(
    essay_text: str,
    assignment,
    word_count: int,
    s3_max: float,
    s3_thesis_max: float,
    s3_evidence_max: float,
    content_criteria: list = None,
    rubric_content: str = "",
    reference_material: str = "",
    stage0_checklist: list = None,
    is_partially_on_topic: bool = False,
) -> dict:
    """
    Stage 3 v5.3 — per-criterion scoring.

    Each content criterion is scored individually up to its proportional
    share of s3_max. Stage 3 total = deterministic weighted sum of scores.

    Falls back to thesis/evidence split when no content_criteria provided.
    """
    title        = getattr(assignment, "title", "Untitled")
    instructions = getattr(assignment, "instructions", "")

    rubric_key_block = (
        f"\nMARKING KEY / RUBRIC DOCUMENT (PRIMARY grading anchor):\n{rubric_content[:3000]}\n"
        if rubric_content and rubric_content.strip() else ""
    )
    ref_block = (
        f"\nREFERENCE MATERIAL (verify factual accuracy):\n{reference_material[:2000]}\n"
        if reference_material and reference_material.strip() else ""
    )
    partial_note = (
        f"\nNOTE: Essay is partially on-topic. Cap each criterion score at 60% of its max.\n"
        if is_partially_on_topic else ""
    )

    # No criteria -> use thesis/evidence fallback
    if not content_criteria:
        print("[Stage 3] No content criteria — using thesis/evidence fallback")
        return _stage3_thesis_evidence_fallback(
            essay_text, assignment, word_count,
            s3_max, s3_thesis_max, s3_evidence_max,
            rubric_key_block, ref_block, partial_note,
            stage0_checklist, is_partially_on_topic,
        )

    # Assign proportional points to each criterion
    criteria_with_pts = [{**c, "max_pts": _compute_criterion_points(c, s3_max)} for c in content_criteria]
    allocated = sum(c["max_pts"] for c in criteria_with_pts)
    if criteria_with_pts and abs(allocated - s3_max) > 0.1:
        diff = round(s3_max - allocated, 1)
        criteria_with_pts[-1]["max_pts"] = round(criteria_with_pts[-1]["max_pts"] + diff, 1)

    print(f"[Stage 3 v5.3] Per-criterion mode: {len(criteria_with_pts)} criteria")
    for c in criteria_with_pts:
        print(f"  * {c['name']}: {c['max_pts']}pts ({c['weight']}%)")

    prompt = _build_per_criterion_prompt(
        title, instructions, criteria_with_pts,
        rubric_key_block, ref_block, partial_note,
        essay_text, word_count, stage0_checklist or [],
    )

    if GROQ_SDK_AVAILABLE and GROQ_API_KEY:
        groq_client = Groq(api_key=GROQ_API_KEY)
        for model_name in ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "mixtral-8x7b-32768"]:
            try:
                print(f"[Stage 3] Trying Groq -> {model_name}")
                parsed = _call_groq_once(groq_client, model_name, prompt)
                result = _extract_criterion_scores(parsed, criteria_with_pts, is_partially_on_topic)

                if result is None:
                    print(f"[Stage 3] Score extraction failed — trying next model")
                    continue

                s3_total = result["s3_total"]

                # Second-pass for long essays scoring unexpectedly low
                if word_count >= 600 and s3_total < (s3_max * 0.53):
                    print(f"[Stage 3] Long essay low ({s3_total}/{s3_max}) — verification pass...")
                    try:
                        parsed2 = _call_groq_once(groq_client, model_name, prompt)
                        result2 = _extract_criterion_scores(parsed2, criteria_with_pts, is_partially_on_topic)
                        if result2:
                            # Take higher score per individual criterion
                            merged_scores = {}
                            total2 = 0.0
                            for cname in result["criterion_scores"]:
                                s1 = result["criterion_scores"][cname]["score"]
                                s2 = result2["criterion_scores"].get(cname, {}).get("score", s1)
                                best = max(s1, s2)
                                merged_scores[cname] = {
                                    **result["criterion_scores"][cname],
                                    "score": best,
                                }
                                total2 += best
                            if total2 > s3_total:
                                result   = {"criterion_scores": merged_scores, "s3_total": round(total2, 1)}
                                s3_total = result["s3_total"]
                                print(f"[Stage 3] Pass 2 higher ({s3_total}/{s3_max})")
                    except Exception as ve:
                        print(f"[Stage 3] Verification pass failed: {ve}")

                critique = _strip_markdown(str(parsed.get("overall_critique", "")))
                print(f"[Stage 3] Done via {model_name} — {result['s3_total']}/{s3_max}")

                return {
                    "criterion_scores":       result["criterion_scores"],
                    "s3_total":               result["s3_total"],
                    "comprehensive_critique": critique,
                    "thesis_score":           result["s3_total"],   # compat
                    "evidence_score":         0.0,                   # compat
                }

            except Exception as e:
                print(f"[Stage 3] {model_name}: {e} — next...")
                continue

        print("[Stage 3] All Groq models failed — HF fallback...")
    else:
        print("[Stage 3] Groq unavailable — HF fallback...")

    # HF fallback: get a single score, distribute proportionally
    fallback_prompt = (
        f"Grade this essay out of {s3_max} points. Respond: 'Score: X/{s3_max}'\n\n{essay_text}"
    )
    raw_hf   = call_huggingface(fallback_prompt)
    s3_total = s3_max * 0.50
    m = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s3_max)}", raw_hf)
    if m:
        s3_total = min(s3_max, max(0.0, float(m.group(1))))
    if is_partially_on_topic:
        s3_total = min(s3_total, s3_max * 0.60)

    criterion_scores = {
        c["name"]: {
            "score":   round(s3_total * (c["max_pts"] / s3_max), 1),
            "max":     c["max_pts"],
            "comment": "Graded via fallback (proportional).",
        }
        for c in criteria_with_pts
    }
    return {
        "criterion_scores":       criterion_scores,
        "s3_total":               round(s3_total, 1),
        "comprehensive_critique": _strip_markdown(raw_hf[:800]),
        "thesis_score":           round(s3_total, 1),
        "evidence_score":         0.0,
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
    final_score: int,
    max_score: int,
    classification: str,
    criterion_scores: dict = None,
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
    v5.1 Rubric-aware ensemble grading pipeline.

    Stage weights are computed from the teacher's actual marking key / rubric.
    Each model grades only the criteria that belong to its specialty:
      Stage 1 (Qwen IELTS)     -> linguistics criteria (grammar, vocabulary, terminology)
      Stage 2 (Qwen coherence) -> coherence criteria   (structure, flow, organisation)
      Stage 3 (Groq)           -> content criteria     (accuracy, arguments, frameworks, evidence)

    Stage 0 optionally extracts an explicit rubric checklist that Stage 3 verifies.
    If no rubric/marking key is provided, defaults to 15 / 10 / 75 weights.

    v5.1 fixes applied:
      - cumulative_feedback initialised exactly once (Stage 0 feedback preserved)
      - Stage 3 JSON keys are generic (thesis_score / evidence_score)
      - coherence_score field has no misleading "out_of_10" suffix
      - s3_thesis_max / s3_evidence_max computed once and passed into Stage 3
      - Partial topic cap enforced in Stage 3 as well as Stage 2
      - _strip_markdown() applied to all AI free-text in feedback output
    """
    max_score = getattr(assignment, "max_score", None) or 100

    master_title        = getattr(assignment, "title", "") or ""
    master_instructions = getattr(assignment, "instructions", "") or ""
    # v5.2: two separate fields — marking key and reference docs
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

    # Compute Stage 3 split once here and pass into call_gemini_stage3
    # to avoid dual-computation drift (FIX v5.1)
    s3_thesis_max   = round(s3_max * 0.507, 0)
    s3_evidence_max = round(s3_max - s3_thesis_max, 0)

    # cumulative_feedback initialised ONCE here (FIX v5.1 — was reset twice before)
    cumulative_feedback = []

    stage0_checklist = []
    if enable_stage0:
        # Stage 0 uses ONLY the marking key for clean checklist extraction
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
        # FIX v5.1: field is now "coherence_score" (no "out_of_10" suffix)
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
            final_score, max_score, "completely_off_topic",
            criterion_scores={},
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
    # STAGE 3 — CONTENT & LOGIC  (s3_max pts)
    # ══════════════════════════════════════════════════════════════
    try:
        gemini_data = call_gemini_stage3(
            essay_text, assignment, word_count,
            s3_max, s3_thesis_max, s3_evidence_max,
            content_criteria,
            rubric_content=rubric_content,
            reference_material=reference_material,
            stage0_checklist=stage0_checklist,
            is_partially_on_topic=is_partially_on_topic,
        )

        # v5.3: per-criterion weighted sum
        criterion_scores = gemini_data.get("criterion_scores", {})
        s3_total         = float(gemini_data.get("s3_total", 0.0))
        s3_total         = max(0.0, min(s3_max, s3_total))
        s3_total_final   = s3_total
        running_points  += s3_total

        if criterion_scores:
            crit_rows = []
            for cname, cdata in criterion_scores.items():
                cscore   = cdata.get("score", 0)
                cmax     = cdata.get("max", 0)
                filled   = int((cscore / cmax * 10) if cmax > 0 else 0)
                cbar     = "#" * filled + " " * (10 - filled)
                ccomment = cdata.get("comment", "")[:120]
                crit_rows.append(
                    f"  {cname:<38} {cscore:>5}/{cmax:<5}  [{cbar}]"
                    + (f"\n    -> {ccomment}" if ccomment else "")
                )
            score_detail = (
                f"Per-Criterion Scores:\n"
                f"  {'Criterion':<38} {'Score':>5}/{'Max':<5}  Progress\n"
                f"  {'-'*70}\n"
                + "\n".join(crit_rows) + "\n"
                + f"  {'-'*70}\n"
                + f"  {'STAGE 3 TOTAL':<38} {round(s3_total,1):>5}/{s3_max:<5}\n"
            )
        else:
            s3_thesis   = float(gemini_data.get("thesis_score",   s3_total * 0.5))
            s3_evidence = float(gemini_data.get("evidence_score", s3_total * 0.5))
            score_detail = (
                f"Thesis Development & Flow    : {s3_thesis} / {s3_thesis_max} pts\n"
                f"Argument Quality & Evidence  : {s3_evidence} / {s3_evidence_max} pts\n"
            )
            criterion_scores = {}

        s3_fb = (
            f"Stage 3: {s3_label}\n"
            f"Model: Groq (llama-3.3-70b-versatile)\n"
            f"{score_detail}\n"
            f"Detailed Critique:\n{_strip_markdown(str(gemini_data.get('comprehensive_critique', '')))}\n\n"
            f"{'--' * 30}\n\n"
        )
        cumulative_feedback.append(s3_fb)
        print(f"Stage 3 complete -- {s3_total}/{s3_max} pts")
        print(f"Stage 3 complete — {s3_total}/{s3_max} pts")

    except Exception as s3_err:
        print(f"Stage 3 failed: {s3_err}. Falling back to Qwen-72B router...")
        try:
            fallback_prompt = prompt or _build_stage3_fallback_prompt(essay_text, assignment, s3_max)
            raw_hf    = call_huggingface(fallback_prompt)
            s3_total  = round(s3_max * 0.50, 1)
            feedback_text = _strip_markdown(raw_hf.strip())

            try:
                parsed = clean_and_parse_json(raw_hf)
                if parsed:
                    s3_total = min(s3_max, max(0.0, float(parsed.get("score") or parsed.get("total_score") or s3_total)))
                    feedback_text = _strip_markdown(parsed.get("feedback", feedback_text))
            except Exception:
                num_match = re.search(rf"(\d+\.?\d*)\s*/\s*{int(s3_max)}", raw_hf)
                if num_match:
                    s3_total = min(s3_max, max(0.0, float(num_match.group(1))))

            if is_partially_on_topic:
                s3_evidence_cap = round(s3_evidence_max * 0.60, 1)
                s3_e = min(round(s3_total * (s3_evidence_max / s3_max), 1), s3_evidence_cap)
                s3_t = round(s3_total * (s3_thesis_max / s3_max), 1)
                s3_total = s3_t + s3_e

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