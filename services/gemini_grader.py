import google.generativeai as genai
import json
import re
import os
from dotenv import load_dotenv

# Load .env file from parent directory
load_dotenv()

def grade_essay_with_gemini(essay_text: str, instructions: str, reference_material: str, rubric: dict, max_score: int):
    """
    Grade an essay using Gemini 2.5 Flash - Production Ready
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        # Try to read from .env file directly
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    if line.startswith('GEMINI_API_KEY='):
                        api_key = line.strip().split('=', 1)[1]
                        break
        
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment")
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    rubric_text = "\n".join([f"- {k}: {v}%" for k, v in rubric.items()])
    
    prompt = f"""You are an expert university essay grader. Grade the following essay based on the instructions and rubric.

INSTRUCTIONS:
{instructions}

GRADING RUBRIC (percentages):
{rubric_text}

Maximum score: {max_score} points

STUDENT'S ESSAY:
{essay_text}

Return ONLY valid JSON in this exact format (no other text):
{{"score": number, "feedback": "detailed constructive feedback", "scores": {{"content": number, "structure": number, "grammar": number, "evidence": number}}}}"""
    
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip()
        clean_text = re.sub(r'^```json\s*', '', clean_text)
        clean_text = re.sub(r'^```\s*', '', clean_text)
        clean_text = re.sub(r'\s*```$', '', clean_text)
        result = json.loads(clean_text)
        
        return {
            "score": min(max_score, max(0, result.get('score', max_score // 2))),
            "feedback": result.get('feedback', "Essay graded successfully."),
            "scores": result.get('scores', {
                "content": max_score // 4,
                "structure": max_score // 4,
                "grammar": max_score // 4,
                "evidence": max_score // 4
            })
        }
        
    except Exception as e:
        print(f"Gemini error: {e}")
        return {
            "score": max_score // 2,
            "feedback": f"AI grading temporary unavailable. Please grade manually.",
            "scores": {
                "content": max_score // 4,
                "structure": max_score // 4,
                "grammar": max_score // 4,
                "evidence": max_score // 4
            }
        }
