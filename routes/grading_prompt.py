"""
grading_prompt.py
=================
PROMPT + RESPONSE PARSING LIVES HERE.

- Edit build_grading_prompt() → change AI behavior
- Edit parse_ai_response()   → change how output is processed
"""

import json
import re


def build_grading_prompt(assignment, essay_text: str, word_count: int) -> str:
    rubric = assignment.rubric if isinstance(assignment.rubric, dict) else json.loads(assignment.rubric) if assignment.rubric else {
    "relevance":     30,
    "content":       25,
    "structure":     20,
    "grammar":       15,
    "vocabulary":    10,
}

    max_score           = assignment.max_score
    total_rubric_points = sum(rubric.values())

    rubric_lines   = []
    criterion_list = []

    for k, v in rubric.items():
        actual_max = round((v / total_rubric_points) * max_score)
        rubric_lines.append(
            f"  - {k.capitalize()} (max {actual_max} pts): strict evaluation"
        )
        criterion_list.append({"name": k.capitalize(), "max": actual_max})

    rubric_block = "\n".join(rubric_lines)

    breakdown_template = ", ".join(
        f'"{c["name"]}": {{"score": <0-{c["max"]}>, "max": {c["max"]}, "reason": "<one sentence>"}}'
        for c in criterion_list
    )

    reference_block = ""
    if assignment.reference_material and assignment.reference_material.strip():
        reference_block = (
            f"\nREFERENCE / MARKING KEY:\n---\n{assignment.reference_material[:3000]}\n---\n"
        )

    return f"""You are a STRICT academic essay grader. You must follow ALL rules exactly. Do NOT be lenient.

═══════════════════════════════════════
ASSIGNMENT
═══════════════════════════════════════
Title:         {assignment.title}
Instructions:  {assignment.instructions}
Maximum score: {max_score}
{reference_block}

═══════════════════════════════════════
RUBRIC (STRICT)
═══════════════════════════════════════
{rubric_block}

═══════════════════════════════════════
MANDATORY GRADING PROCESS
═══════════════════════════════════════

STEP 1 — TOPIC RELEVANCE (STRICT GATE):

Compare the essay against BOTH:
- assignment title
- assignment instructions

Classify as EXACTLY ONE:
- "directly relevant"
- "partially relevant"
- "off-topic"

RULES:
- "off-topic" → off_topic=true, total_score=0, ALL scores=0, STOP
- "partially relevant" → total_score MUST NOT exceed {int(max_score * 0.30)}
- ONLY "directly relevant" can exceed 30%

IMPORTANT:
- General discussion is NOT enough
- Essay must clearly answer the question
- Be strict — do NOT guess intent

═══════════════════════════════════════
STEP 2 — LENGTH RULES
═══════════════════════════════════════
- <50 words  → cap total_score at {round(max_score * 0.10)}
- <100 words → cap total_score at {round(max_score * 0.25)}
- <200 words → cap total_score at {round(max_score * 0.50)}
- 200+ words → no cap

═══════════════════════════════════════
STEP 3 — CRITERIA GRADING
═══════════════════════════════════════

SCORING SCALE (STRICT):
- 100%: precise, directly answers question with strong support
- 75%: mostly correct, minor gaps
- 50%: partially correct, noticeable gaps
- 25%: weak, mostly off-track
- 0%: irrelevant or incorrect

IMPORTANT:
- If RELEVANCE score = 0 → TOTAL MUST = 0
- Be consistent and strict

═══════════════════════════════════════
STEP 4 — TOTAL SCORE
═══════════════════════════════════════
Sum all scores, then apply caps from STEP 1 and STEP 2.

═══════════════════════════════════════
STEP 5 — AI DETECTION (DEFAULT FALSE)
═══════════════════════════════════════
Set ai_detected=true ONLY if ALL:
- no personal voice
- perfectly structured
- contains ≥5 common AI phrases

═══════════════════════════════════════
STUDENT ESSAY ({word_count} words)
═══════════════════════════════════════
{essay_text[:4000]}
═══════════════════════════════════════

RETURN ONLY VALID JSON:

{{
  "relevance_label": "directly relevant" | "partially relevant" | "off-topic",
  "total_score": <0-{max_score}>,
  "breakdown": {{{breakdown_template}}},
  "overall_feedback": "<clear explanation of score and relevance>",
  "strengths": ["<specific>", "<specific>"],
  "improvements": ["<specific>", "<specific>"],
  "off_topic": <true or false>,
  "ai_detected": <true or false>
}}"""


def parse_ai_response(raw_text: str, max_score: int) -> dict:
    clean = raw_text.strip().replace("```json", "").replace("```", "").strip()

    json_match = re.search(r'\{.*\}', clean, re.DOTALL)
    if json_match:
        clean = json_match.group()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        score_match    = re.search(r'"total_score"\s*:\s*(\d+)', clean) or re.search(r'"score"\s*:\s*(\d+)', clean)
        feedback_match = re.search(r'"overall_feedback"\s*:\s*"(.*?)"(?:\s*,|\s*})', clean, re.DOTALL)
        ai_match       = re.search(r'"ai_detected"\s*:\s*(true|false)', clean)
        topic_match    = re.search(r'"off_topic"\s*:\s*(true|false)', clean)

        if score_match:
            return {
                "score":       int(score_match.group(1)),
                "feedback":    feedback_match.group(1).strip() if feedback_match else "Graded.",
                "ai_detected": ai_match.group(1) == "true" if ai_match else False,
                "off_topic":   topic_match.group(1) == "true" if topic_match else False,
            }
        raise ValueError(f"Could not parse AI response: {clean[:200]}")

    score = max(0, min(max_score, int(data.get("total_score") or data.get("score") or 0)))

    # ✅ HARD ENFORCEMENT (VERY IMPORTANT)
    if data.get("off_topic") is True:
        score = 0

    if data.get("relevance_label") == "partially relevant":
        score = min(score, int(max_score * 0.3))

    overall      = data.get("overall_feedback", "")
    strengths    = data.get("strengths", [])
    improvements = data.get("improvements", [])
    breakdown    = data.get("breakdown", {})

    feedback_parts = []

    if overall:
        feedback_parts.append(overall)

    if breakdown:
        feedback_parts.append("\n📊 BREAKDOWN:")
        for criterion, detail in breakdown.items():
            if isinstance(detail, dict):
                s      = detail.get("score", "?")
                mx     = detail.get("max",   "?")
                reason = detail.get("reason", "")
                feedback_parts.append(f"  • {criterion}: {s}/{mx} — {reason}")

    if strengths:
        feedback_parts.append("\n✅ STRENGTHS:")
        for s in strengths:
            feedback_parts.append(f"  • {s}")

    if improvements:
        feedback_parts.append("\n📈 IMPROVEMENTS:")
        for imp in improvements:
            feedback_parts.append(f"  • {imp}")

    return {
        "score":       score,
        "feedback":    "\n".join(feedback_parts).strip() or "Graded successfully.",
        "off_topic":   data.get("off_topic", False),
        "ai_detected": data.get("ai_detected", False),
        "breakdown":   breakdown,
        "graded_by":   "ai_strict_v2",
    }