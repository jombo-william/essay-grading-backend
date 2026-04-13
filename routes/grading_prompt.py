"""
grading_prompt.py
=================
PROMPT + RESPONSE PARSING LIVES HERE.

To change what the AI is told to do  → edit build_grading_prompt()
To change how scores are extracted   → edit parse_ai_response()
To change rubric weights             → edit the default rubric dict inside build_grading_prompt()
"""

import json
import re


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_grading_prompt(assignment, essay_text: str, word_count: int) -> str:
    """
    Build the grading prompt sent to Gemini or HuggingFace.
    The local model does NOT use this — it uses sentence similarity instead.
    """
    rubric = json.loads(assignment.rubric) if assignment.rubric else {
        "content": 30, "structure": 25, "grammar": 20,
        "vocabulary": 15, "argumentation": 10,
    }
    rubric_lines = "\n".join(
        f"  - {k.capitalize()}: {v} points" for k, v in rubric.items()
    )

    reference_block = ""
    if assignment.reference_material and assignment.reference_material.strip():
        reference_block = (
            f"\nREFERENCE MATERIAL (provided by teacher — use to verify accuracy):\n"
            f"---\n{assignment.reference_material[:2500]}\n---\n"
        )

    max_score = assignment.max_score

    return f"""You are a strict academic essay grader. Grade the student essay ONLY based on how well it answers the assignment question below.

════════════════════════════════════════
ASSIGNMENT
════════════════════════════════════════
Title: {assignment.title}
Instructions: {assignment.instructions}
Maximum score: {max_score} points
{reference_block}
════════════════════════════════════════
GRADING RUBRIC
════════════════════════════════════════
{rubric_lines}

════════════════════════════════════════
MANDATORY RULES — FOLLOW EXACTLY
════════════════════════════════════════

RULE 1 — CHECK TOPIC FIRST (most important rule):
Before scoring anything, ask: Does this essay actually answer the assignment question?
- If the essay is about a COMPLETELY DIFFERENT SUBJECT, set off_topic=true and score 0 to {round(max_score * 0.05)}.
- A beautifully written essay on the WRONG topic still scores near 0.

RULE 2 — LENGTH CHECK:
- Under 100 words on any assignment → max score is {round(max_score * 0.20)}.

RULE 3 — SCORING SCALE (only if essay is ON-TOPIC):
- 90-100%: Exceptional
- 75-89%:  Good
- 60-74%:  Satisfactory
- 40-59%:  Weak
- 20-39%:  Very poor
- 0-15%:   Off-topic / wrong subject

RULE 4 — AI DETECTION (be very conservative):
- Default: ai_detected=false
- Only set ai_detected=true if ALL three are true:
  (a) zero personal voice
  (b) robotic, perfectly structured paragraphs with no errors
  (c) 5+ of these exact phrases: "it is important to note", "plays a crucial role",
      "in today's society", "it is worth noting", "delve into", "in conclusion it is", "furthermore it is"

════════════════════════════════════════
STUDENT ESSAY ({word_count} words)
════════════════════════════════════════
{essay_text[:4000]}
════════════════════════════════════════

THINK STEP BY STEP:
Step 1: What topic does the assignment ask about?
Step 2: What topic is the essay actually about?
Step 3: Do they match? If not → off_topic=true, score very low
Step 4: If they match → score using the rubric

Reply ONLY with this exact JSON, nothing else:
{{"score": <integer 0-{max_score}>, "feedback": "specific feedback", "off_topic": <true or false>, "ai_detected": <true or false>}}"""


# ── Response parser ───────────────────────────────────────────────────────────

def parse_ai_response(raw_text: str, max_score: int) -> dict:
    """
    Parse the JSON response from Gemini or HuggingFace.
    Handles messy responses with markdown fences or extra text.
    """
    clean = raw_text.strip().replace("```json", "").replace("```", "").strip()

    json_match = re.search(r'\{.*\}', clean, re.DOTALL)
    if json_match:
        clean = json_match.group()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Fallback: extract fields with regex
    score_match    = re.search(r'"score"\s*:\s*(\d+)', clean)
    feedback_match = re.search(r'"feedback"\s*:\s*"(.*?)"(?:\s*,|\s*})', clean, re.DOTALL)
    ai_match       = re.search(r'"ai_detected"\s*:\s*(true|false)', clean)
    topic_match    = re.search(r'"off_topic"\s*:\s*(true|false)', clean)

    if score_match:
        feedback = ""
        if feedback_match:
            feedback = feedback_match.group(1).replace('\\"', '"').strip()
        else:
            fb = re.search(r'"feedback"\s*:\s*"(.+)', clean, re.DOTALL)
            if fb:
                feedback = fb.group(1)[:500].strip().rstrip('"}')
        return {
            "score":       int(score_match.group(1)),
            "feedback":    feedback or "Graded successfully.",
            "ai_detected": ai_match.group(1) == "true" if ai_match else False,
            "off_topic":   topic_match.group(1) == "true" if topic_match else False,
        }

    raise ValueError(f"Could not parse AI response: {clean[:200]}")