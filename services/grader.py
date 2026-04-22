# services/grader.py
import google.generativeai as genai
import os
import json

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

def grade_essay(essay_text: str, rubric: dict = None) -> dict:
    if rubric:
        rubric_text = "\n".join([f"- {k}: {v} marks" for k, v in rubric.items()])
    else:
        rubric_text = """
        - Content & Arguments: 40 marks
        - Structure & Organization: 30 marks
        - Grammar & Language: 30 marks
        """

    prompt = f"""
    You are an expert essay grader. Grade the following essay based on this rubric:

    RUBRIC:
    {rubric_text}

    ESSAY:
    {essay_text}

    Respond ONLY with a valid JSON object, no extra text, no markdown:
    {{
        "total_score": <number out of 100>,
        "breakdown": {{
            "criterion_name": {{
                "score": <number>,
                "max_score": <number>,
                "feedback": "<specific feedback>"
            }}
        }},
        "overall_feedback": "<2-3 sentence summary>",
        "strengths": ["<strength 1>", "<strength 2>"],
        "improvements": ["<improvement 1>", "<improvement 2>"]
    }}
    """

    response = model.generate_content(prompt)
    text = response.text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(text)