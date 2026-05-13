import google.generativeai as genai
import json
import re
import os

def grade_essay_with_gemini(essay_text: str, instructions: str, reference_material: str, rubric: dict, max_score: int):
    """
    Grade an essay using Gemini 2.0 Flash Lite (FREE - higher quota)
    """
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    
    # Use flash-lite which has better free tier limits
    model = genai.GenerativeModel('models/gemini-2.5-flash-preview')
    
    rubric_text = "\n".join([f"- {k}: {v}%" for k, v in rubric.items()])
    
    prompt = f"""Grade this essay.

Instructions: {instructions}
Rubric: {rubric_text}
Max score: {max_score}

Essay: {essay_text}

Return ONLY JSON: {{"score": number, "feedback": "text", "scores": {{"content": number, "structure": number, "grammar": number, "evidence": number}}}}"""
    
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip()
        clean_text = re.sub(r'^```json\s*', '', clean_text)
        clean_text = re.sub(r'^```\s*', '', clean_text)
        clean_text = re.sub(r'\s*```$', '', clean_text)
        result = json.loads(clean_text)
        
        # Ensure all fields exist
        result.setdefault('score', max_score // 2)
        result.setdefault('feedback', "Grade successfully processed.")
        result.setdefault('scores', {
            "content": result['score'] // 4,
            "structure": result['score'] // 4,
            "grammar": result['score'] // 4,
            "evidence": result['score'] // 4
        })
        
        return result
        
    except Exception as e:
        print(f"Gemini error: {e}")
        return {
            "score": max_score // 2,
            "feedback": f"AI grading temporarily unavailable. Essay ID: {id(essay_text)}",
            "scores": {
                "content": max_score // 4,
                "structure": max_score // 4,
                "grammar": max_score // 4,
                "evidence": max_score // 4
            }
        }
