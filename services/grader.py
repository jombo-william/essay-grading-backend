# services/grader.py

import os
import re
import json
import time
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


def _build_prompt(essay_text: str, rubric: dict = None) -> str:
    if rubric:
        rubric_text = "\n".join([f"- {k}: {v} marks" for k, v in rubric.items()])
    else:
        rubric_text = (
            "- Content & Arguments: 40 marks\n"
            "- Structure & Organization: 30 marks\n"
            "- Grammar & Language: 30 marks"
        )

    return f"""You are an expert essay grader. Grade the essay below based on this rubric.

RUBRIC:
{rubric_text}

ESSAY:
\"\"\"
{essay_text}
\"\"\"

INSTRUCTIONS:
- Return ONLY a single valid JSON object — no markdown, no commentary.
- All string values must use escaped double-quotes if they contain quotes.
- Do NOT include the essay text in your response.
- Keep all feedback concise (1-2 sentences max per field) to avoid truncation.
- Use this exact structure:

{{
  "total_score": <integer 0-100>,
  "breakdown": {{
    "Content & Arguments": {{"score": <int>, "max_score": 40, "feedback": "<text>"}},
    "Structure & Organization": {{"score": <int>, "max_score": 30, "feedback": "<text>"}},
    "Grammar & Language": {{"score": <int>, "max_score": 30, "feedback": "<text>"}}
  }},
  "overall_feedback": "<2-3 sentence summary>",
  "strengths": ["<strength 1>", "<strength 2>"],
  "improvements": ["<improvement 1>", "<improvement 2>"]
}}"""


def _extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from AI output that may contain:
    - markdown code fences
    - extra text before/after the JSON
    - truncated responses
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip()
    text = text.replace("```", "").strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first {...} block and try parsing that
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            # Try to repair truncated JSON by closing open brackets
            partial = match.group()
            repaired = _repair_truncated_json(partial)
            if repaired:
                return repaired

    raise ValueError(f"Could not parse JSON from AI response: {text[:300]}")


def _repair_truncated_json(text: str) -> dict | None:
    """Attempt to close unclosed brackets/braces in a truncated JSON string."""
    # Count open/close braces and brackets
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")

    # Strip trailing incomplete key-value (e.g. ends with `"key": "val` or `"key":`)
    # Cut off at the last complete value (after a comma or closing brace/bracket)
    trimmed = re.sub(r',\s*"[^"]*"\s*:\s*[^,}\]]*$', '', text.rstrip())
    trimmed = re.sub(r',\s*$', '', trimmed)

    # Close any open arrays and objects
    trimmed += "]" * open_brackets + "}" * open_braces

    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        return None


def grade_essay(essay_text: str, rubric: dict = None) -> dict:
    prompt = _build_prompt(essay_text, rubric)

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "maxOutputTokens": 4096,   # increased from 1500 to prevent truncation
                        "topP": 1.0,
                        "topK": 1,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=60,
            )

            # Retry on 503
            if resp.status_code == 503:
                raise requests.exceptions.ConnectionError("503 Service Unavailable")

            resp.raise_for_status()
            data = resp.json()
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            return _extract_json(raw)

        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 ** attempt)  # 1s, 2s, then fail
            continue

        except ValueError as e:
            # JSON parse failed — don't retry, raise immediately
            raise

    raise Exception(f"AI service unavailable after 3 attempts: {last_err}")