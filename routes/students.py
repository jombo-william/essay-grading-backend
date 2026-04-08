# C:\PROJECTS\Essay-Grader\backend\routes\students.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from database import get_db
from models.user import User
from models.assignment import Assignment
from models.submission import Submission
from models.message import Message

router = APIRouter()

# ── Schemas ────────────────────────────────────────────────────────────────
class SubmitEssaySchema(BaseModel):
    assignment_id: int
    essay_text: str
    submit_mode: str = "write"
    file_name: Optional[str] = None

class SendMessageSchema(BaseModel):
    receiver_id: int
    subject: Optional[str] = None
    content: str
    message_type: str = "question"

# ── Helper function to get current user ────────────────────────────────────
def get_current_user(token: str, db: Session):
    # For now, we'll decode the JWT token to get user info
    # In a real app, you'd validate the token properly
    from jose import jwt
    try:
        payload = jwt.decode(token, "secret", algorithms=["HS256"])
        user = db.query(User).filter(User.id == payload["user_id"]).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

# ── GET ASSIGNMENTS ────────────────────────────────────────────────────────
@router.get("/assignments")
def get_assignments(db: Session = Depends(get_db)):
    assignments = db.query(Assignment).all()
    result = []
    for a in assignments:
        # For demo, use student ID 1
        submission = db.query(Submission).filter(
            Submission.assignment_id == a.id,
            Submission.student_id == 1
        ).first()
        
        result.append({
            "id": a.id,
            "title": a.title,
            "description": a.description,
            "due_date": a.due_date.isoformat() if a.due_date else None,
            "max_score": a.max_score,
            "submitted": submission is not None,
            "submission": {
                "id": submission.id if submission else None,
                "status": submission.status if submission else None,
                "final_score": submission.final_score if submission else None,
            } if submission else None
        })
    return {"assignments": result}

# ── SUBMIT ESSAY ───────────────────────────────────────────────────────────
@router.post("/submit-essay")
def submit_essay(body: SubmitEssaySchema, db: Session = Depends(get_db)):
    student_id = 1  # Demo student
    
    # Check if assignment exists
    assignment = db.query(Assignment).filter(Assignment.id == body.assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    # Check if already submitted
    existing = db.query(Submission).filter(
        Submission.assignment_id == body.assignment_id,
        Submission.student_id == student_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already submitted")
    
    # Create submission
    submission = Submission(
        assignment_id=body.assignment_id,
        student_id=student_id,
        essay_text=body.essay_text,
        submit_mode=body.submit_mode,
        file_name=body.file_name,
        status="submitted"
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    
    # Mock AI grading
    submission.status = "ai_graded"
    submission.ai_score = 85.0
    submission.ai_detection_score = 15.0
    db.commit()
    
    return {"message": "Essay submitted successfully", "submission_id": submission.id}

# ── GET RESULTS ────────────────────────────────────────────────────────────
@router.get("/results")
def get_results(db: Session = Depends(get_db)):
    student_id = 1  # Demo student
    
    submissions = db.query(Submission).filter(Submission.student_id == student_id).all()
    result = []
    for s in submissions:
        assignment = db.query(Assignment).filter(Assignment.id == s.assignment_id).first()
        result.append({
            "id": s.id,
            "assignment_id": s.assignment_id,
            "assignment_title": assignment.title if assignment else "Unknown",
            "status": s.status,
            "ai_score": s.ai_score,
            "final_score": s.final_score,
            "teacher_feedback": s.teacher_feedback,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        })
    return {"results": result}

# ── SEND MESSAGE TO TEACHER ────────────────────────────────────────────────
@router.post("/send-message")
def send_message(body: SendMessageSchema, db: Session = Depends(get_db)):
    sender_id = 1  # Demo student
    
    # Check if receiver exists and is a teacher
    receiver = db.query(User).filter(User.id == body.receiver_id, User.role == "teacher").first()
    if not receiver:
        raise HTTPException(status_code=404, detail="Teacher not found")
    
    message = Message(
        sender_id=sender_id,
        receiver_id=body.receiver_id,
        subject=body.subject,
        content=body.content,
        message_type=body.message_type
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    
    return {"message": "Message sent successfully", "message_id": message.id}

# ── GET MESSAGES ───────────────────────────────────────────────────────────
@router.get("/messages")
def get_messages(db: Session = Depends(get_db)):
    user_id = 1  # Demo student
    
    messages = db.query(Message).filter(
        (Message.sender_id == user_id) | (Message.receiver_id == user_id)
    ).order_by(Message.created_at.desc()).all()
    
    result = []
    for m in messages:
        result.append({
            "id": m.id,
            "sender_id": m.sender_id,
            "receiver_id": m.receiver_id,
            "sender_name": m.sender.name,
            "receiver_name": m.receiver.name,
            "subject": m.subject,
            "content": m.content,
            "message_type": m.message_type,
            "is_read": m.is_read,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return {"messages": result}