import requests
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from auth_utils import require_teacher
from database import get_db
import models
import os

router = APIRouter(prefix="/moodle", tags=["Moodle Quizzes"])

# Get Moodle URL and token from environment
MOODLE_URL = os.getenv("MOODLE_URL", "https://essaygrade.moodlecloud.com")
MOODLE_TOKEN = os.getenv("MOODLE_TOKEN")

def moodle_call(token: str, function: str, params: dict):
    """Make a Moodle Web Service call."""
    url = f"{MOODLE_URL}/webservice/rest/server.php"
    try:
        response = requests.post(
            url,
            data={
                "wstoken": token,
                "wsfunction": function,
                "moodlewsrestformat": "json",
                **params
            },
            timeout=30
        )
        data = response.json()
        if isinstance(data, dict) and data.get("exception"):
            raise HTTPException(
                status_code=400,
                detail=f"Moodle error: {data.get('message', 'Unknown error')}"
            )
        return data
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to Moodle at {MOODLE_URL}"
        )

class QuestionOption(BaseModel):
    text: str
    is_correct: bool = False

class QuizQuestion(BaseModel):
    question_text: str
    question_type: str
    points: float = 1.0
    options: Optional[List[QuestionOption]] = None
    correct_answer: Optional[str] = None

class CreateQuizRequest(BaseModel):
    title: str
    description: Optional[str] = ""
    course_id: int
    time_limit_minutes: Optional[int] = None
    questions: List[QuizQuestion]
    shuffle_questions: bool = False
    shuffle_answers: bool = False

@router.get("/quizzes")
def get_quizzes(
    moodle_token: str = Query(...),
    site_url: str = Query(...),
    db: Session = Depends(get_db),
    ctx: dict = Depends(require_teacher)
):
    """Get all quizzes created by this teacher"""
    user = ctx["user"]
    quizzes = db.query(models.Quiz).filter(
        models.Quiz.teacher_id == user.id
    ).order_by(models.Quiz.created_at.desc()).all()
    
    return {
        "success": True,
        "quizzes": [
            {
                "id": q.id,
                "title": q.title,
                "description": q.description,
                "moodle_quiz_id": q.moodle_quiz_id,
                "moodle_course_id": q.moodle_course_id,
                "time_limit_minutes": q.time_limit_minutes,
                "question_count": len(q.questions) if q.questions else 0,
                "created_at": q.created_at.isoformat() if q.created_at else None,
                "synced_to_moodle": q.moodle_quiz_id is not None
            }
            for q in quizzes
        ]
    }

@router.post("/create-quiz")
def create_quiz_in_moodle(
    quiz_data: CreateQuizRequest,
    moodle_token: str = Query(...),
    site_url: str = Query(...),
    db: Session = Depends(get_db),
    ctx: dict = Depends(require_teacher)
):
    """Create a quiz locally AND push it to Moodle"""
    user = ctx["user"]
    
    # 1. Create the quiz in Moodle first
    try:
        # Step 1: Create the quiz activity in Moodle course
        quiz_params = {
            "courseid": quiz_data.course_id,
            "name": quiz_data.title,
            "intro": quiz_data.description or "",
            "introformat": 1,
            "timeopen": 0,
            "timeclose": 0,
            "timelimit": (quiz_data.time_limit_minutes or 0) * 60,
            "shuffleanswers": 1 if quiz_data.shuffle_answers else 0,
            "shufflequestions": 1 if quiz_data.shuffle_questions else 0,
        }
        
        moodle_result = moodle_call(
            token=moodle_token,
            function="mod_quiz_add_quiz",
            params=quiz_params
        )
        
        moodle_quiz_id = moodle_result.get("id")
        if not moodle_quiz_id:
            raise HTTPException(status_code=500, detail="Failed to create quiz in Moodle")
        
        # Step 2: Add questions to the Moodle quiz
        for idx, q in enumerate(quiz_data.questions):
            question_params = {
                "quizid": moodle_quiz_id,
                "name": f"Q{idx+1}: {q.question_text[:50]}",
                "questiontext": q.question_text,
                "questiontextformat": 1,
                "defaultmark": q.points,
                "type": _map_question_type_to_moodle(q.question_type),
            }
            
            # Add type-specific parameters
            if q.question_type == "multiple_choice" and q.options:
                question_params["single"] = 1
                for opt_idx, opt in enumerate(q.options):
                    question_params[f"answer_{opt_idx+1}"] = opt.text
                    question_params[f"fraction_{opt_idx+1}"] = 1.0 if opt.is_correct else 0.0
            
            elif q.question_type == "true_false":
                question_params["answer_1"] = "True"
                question_params["answer_2"] = "False"
                question_params["fraction_1"] = 1.0 if q.correct_answer == "True" else 0.0
                question_params["fraction_2"] = 1.0 if q.correct_answer == "False" else 0.0
            
            elif q.question_type in ["short_answer", "essay"]:
                if q.correct_answer:
                    question_params["answer_1"] = q.correct_answer
                    question_params["fraction_1"] = 1.0
            
            moodle_call(
                token=moodle_token,
                function="mod_quiz_add_question",
                params=question_params
            )
        
        # 2. Save quiz to local database
        quiz = models.Quiz(
            teacher_id=user.id,
            title=quiz_data.title,
            description=quiz_data.description,
            questions=[q.dict() for q in quiz_data.questions],
            moodle_quiz_id=moodle_quiz_id,
            moodle_course_id=quiz_data.course_id,
            time_limit_minutes=quiz_data.time_limit_minutes,
            shuffle_questions=quiz_data.shuffle_questions,
            shuffle_answers=quiz_data.shuffle_answers
        )
        db.add(quiz)
        db.commit()
        db.refresh(quiz)
        
        return {
            "success": True,
            "message": f"✅ Quiz created in Moodle and saved locally! Moodle Quiz ID: {moodle_quiz_id}",
            "local_quiz_id": quiz.id,
            "moodle_quiz_id": moodle_quiz_id
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create quiz: {str(e)}")

def _map_question_type_to_moodle(q_type: str) -> str:
    """Map our question types to Moodle's format"""
    mapping = {
        "multiple_choice": "multichoice",
        "true_false": "truefalse",
        "short_answer": "shortanswer",
        "essay": "essay"
    }
    return mapping.get(q_type, "essay")

@router.get("/quiz-results/{moodle_quiz_id}")
def get_quiz_results(
    moodle_quiz_id: int,
    moodle_token: str = Query(...),
    site_url: str = Query(...),
    db: Session = Depends(get_db),
    ctx: dict = Depends(require_teacher)
):
    """Get student results for a Moodle quiz"""
    try:
        # Get quiz attempts
        attempts_data = moodle_call(
            token=moodle_token,
            function="mod_quiz_get_user_attempts",
            params={"quizid": moodle_quiz_id, "status": "finished"}
        )
        
        results = []
        for attempt in attempts_data.get("attempts", []):
            results.append({
                "user_id": attempt.get("userid"),
                "attempt_id": attempt.get("id"),
                "score": attempt.get("sumgrades", 0),
                "time_start": attempt.get("timestart"),
                "time_finish": attempt.get("timefinish"),
                "state": attempt.get("state"),
            })
        
        return {
            "success": True,
            "quiz_id": moodle_quiz_id,
            "results": results
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get results: {str(e)}")
