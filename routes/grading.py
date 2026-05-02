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