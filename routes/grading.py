# routes/grading.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from services.grader import grade_essay

router = APIRouter()

class GradeRequest(BaseModel):
    essay: str
    rubric: Optional[dict] = None

@router.post("/grade")
async def grade(request: GradeRequest):
    if not request.essay.strip():
        raise HTTPException(status_code=400, detail="Essay text is required")
    try:
        result = grade_essay(request.essay, request.rubric)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

import time
import random

def grade_essay(essay_text, rubric=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            # your existing Gemini API call here
            response = model.generate_content(prompt)
            return parse_response(response)
        except Exception as e:
            if "503" in str(e) and attempt < max_retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)  # 1s, 2s, 4s...
                time.sleep(wait)
                continue
            raise  # re-raise on final attempt