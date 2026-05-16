from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from auth_utils import require_teacher
import models

router = APIRouter()

@router.get("/classes")
def get_teacher_classes(
    db: Session = Depends(get_db),
    ctx: dict = Depends(require_teacher)
):
    """Get all classes for the logged-in teacher"""
    user = ctx["user"]
    
    # Get classes through the teacher_classes relationship
    teacher_classes = db.query(models.TeacherClass).filter(
        models.TeacherClass.teacher_id == user.id
    ).all()
    
    classes = [tc.cls for tc in teacher_classes if tc.cls]
    
    return {
        "success": True,
        "classes": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "subject": c.subject,
                "section": c.section,
                "is_active": c.is_active,
                "total_students": len(c.enrollments) if c.enrollments else 0
            }
            for c in classes
        ]
    }

@router.get("/test")
def test_endpoint():
    """Test endpoint"""
    return {"message": "Teacher router is working!"}
