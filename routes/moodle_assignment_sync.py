import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from auth_utils import require_teacher
from database import get_db
import models
import json
import os

router = APIRouter(prefix="/moodle", tags=["Moodle Assignment Sync"])

MOODLE_URL = os.getenv("MOODLE_URL", "https://essaygrade.moodlecloud.com")

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
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot connect to Moodle: {str(e)}")

class SyncAssignmentRequest(BaseModel):
    assignment_id: int
    moodle_course_id: int
    moodle_token: str

@router.post("/sync-assignment")
def sync_assignment_to_moodle(
    request: SyncAssignmentRequest,
    db: Session = Depends(get_db),
    ctx: dict = Depends(require_teacher)
):
    """Sync a local assignment to Moodle"""
    user = ctx["user"]
    
    # Get the assignment
    assignment = db.query(models.Assignment).filter(
        models.Assignment.id == request.assignment_id,
        models.Assignment.teacher_id == user.id
    ).first()
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    try:
        # Create assignment in Moodle
        moodle_params = {
            "courseid": request.moodle_course_id,
            "name": assignment.title,
            "intro": assignment.instructions or "",
            "introformat": 1,
            "grade": assignment.max_score,
            "cutoffdate": 0,
            "duedate": 0,
        }
        
        result = moodle_call(
            token=request.moodle_token,
            function="mod_assign_add_instance",
            params=moodle_params
        )
        
        moodle_assignment_id = result.get("id")
        
        if moodle_assignment_id:
            # Update local assignment with Moodle IDs
            assignment.moodle_assignment_id = moodle_assignment_id
            assignment.moodle_course_id = request.moodle_course_id
            assignment.synced_to_moodle = True
            db.commit()
            
            # Record in sync table
            sync_record = models.MoodleSync(
                local_assignment_id=assignment.id,
                moodle_assignment_id=moodle_assignment_id,
                moodle_course_id=request.moodle_course_id,
                last_sync_status="success"
            )
            db.add(sync_record)
            db.commit()
            
            return {
                "success": True,
                "message": f"Assignment '{assignment.title}' synced to Moodle!",
                "moodle_assignment_id": moodle_assignment_id
            }
        else:
            return {
                "success": False,
                "message": "Failed to create assignment in Moodle"
            }
            
    except Exception as e:
        # Record error
        sync_record = models.MoodleSync(
            local_assignment_id=assignment.id,
            moodle_assignment_id=None,
            moodle_course_id=request.moodle_course_id,
            last_sync_status="failed",
            last_error=str(e)
        )
        db.add(sync_record)
        db.commit()
        
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")
