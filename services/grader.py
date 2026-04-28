# # services/grader.py
# # C:\Users\comadmin\Desktop\jombo\essayf-and-backend\backend\services\grader.py

# import google.generativeai as genai
# import os
# import json

# genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
# model = genai.GenerativeModel("gemini-1.5-flash")
# #model = genai.GenerativeModel("gemini-2.0-flash")



# def grade_essay(essay_text: str, rubric: dict = None) -> dict:
#     if rubric:
#         rubric_text = "\n".join([f"- {k}: {v} marks" for k, v in rubric.items()])
#     else:
#         rubric_text = """
#         - Content & Arguments: 40 marks
#         - Structure & Organization: 30 marks
#         - Grammar & Language: 30 marks
#         """

#     prompt = f"""
#     You are an expert essay grader. Grade the following essay based on this rubric:

#     RUBRIC:
#     {rubric_text}

#     ESSAY:
#     {essay_text}

#     Respond ONLY with a valid JSON object, no extra text, no markdown:
#     {{
#         "total_score": <number out of 100>,
#         "breakdown": {{
#             "criterion_name": {{
#                 "score": <number>,
#                 "max_score": <number>,
#                 "feedback": "<specific feedback>"
#             }}
#         }},
#         "overall_feedback": "<2-3 sentence summary>",
#         "strengths": ["<strength 1>", "<strength 2>"],
#         "improvements": ["<improvement 1>", "<improvement 2>"]
#     }}
#     """

#     response = model.generate_content(prompt)
#     text = response.text.strip().replace("```json", "").replace("```", "").strip()
#     return json.loads(text)




# services/grader.py
# C:\Users\comadmin\Desktop\jombo\essayf-and-backend\backend\services\grader.py

import os
import re
import json
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


def _extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from AI output that may contain:
    - markdown code fences (```json ... ```)
    - extra text before/after the JSON
    - HTML tags inside string values
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip()

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
            pass

    # Last resort: ask Gemini to fix the JSON (rare)
    raise ValueError(f"Could not parse JSON from AI response:\n{text[:300]}")


def grade_essay(essay_text: str, rubric: dict = None) -> dict:
    if rubric:
        rubric_text = "\n".join([f"- {k}: {v} marks" for k, v in rubric.items()])
    else:
        rubric_text = (
            "- Content & Arguments: 40 marks\n"
            "- Structure & Organization: 30 marks\n"
            "- Grammar & Language: 30 marks"
        )

    # Sanitize essay text so it can't break the JSON the model returns
    # We pass it in the prompt but tell the model NOT to echo it back
    safe_essay = essay_text.replace("\\", "\\\\").replace('"', '\\"')

    prompt = f"""You are an expert essay grader. Grade the essay below based on this rubric.

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

    resp = requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1500,
                "topP": 1.0,
                "topK": 1,
                "responseMimeType": "application/json",  # Force JSON mode
            },
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    raw = data["candidates"][0]["content"]["parts"][0]["text"]

    return _extract_json(raw)