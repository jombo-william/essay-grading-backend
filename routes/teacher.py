# C:\PROJECTS\Essay-Grader\backend\routes\teacher.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

from database import get_db
from models.user import User
from models.assignment import Assignment
from models.submission import Submission
from models.message import Message

router = APIRouter()

# ── Schemas ────────────────────────────────────────────────────────────────
class AssignmentCreateSchema(BaseModel):
    title: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    reference_material: Optional[str] = None
    max_score: Optional[int] = 100
    due_date: datetime
    rubric: Optional[Dict[str, Any]] = None

class AssignmentUpdateSchema(AssignmentCreateSchema):
    id: int

class GradeRequestSchema(BaseModel):
    submission_id: int
    score: float
    feedback: Optional[str] = None

class SendMessageSchema(BaseModel):
    receiver_id: int
    subject: Optional[str] = None
    content: str
    message_type: str = "answer"

# ── GET ASSIGNMENTS FOR TEACHER ────────────────────────────────────────────
@router.get("/assignments")
def get_assignments(db: Session = Depends(get_db)):
    teacher_id = 1  # Demo teacher - in production this would come from the authenticated user

    assignments = db.query(Assignment).filter(Assignment.teacher_id == teacher_id).all()
    result = []
    for a in assignments:
        result.append({
            "id": a.id,
            "title": a.title,
            "description": a.description or "",
            "due_date": a.due_date.isoformat() if a.due_date else None,
            "max_score": a.max_score,
            "teacher_id": a.teacher_id,
        })
    return {"assignments": result}

# ── CREATE ASSIGNMENT ──────────────────────────────────────────────────────
@router.post("/assignments/create")
def create_assignment(body: AssignmentCreateSchema, db: Session = Depends(get_db)):
    teacher_id = 1  # Demo teacher

    assignment = Assignment(
        title=body.title,
        description=body.description,
        due_date=body.due_date,
        max_score=body.max_score,
        teacher_id=teacher_id,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    return {"message": "Assignment created successfully", "id": assignment.id}

# ── UPDATE ASSIGNMENT ──────────────────────────────────────────────────────
@router.post("/assignments/update")
def update_assignment(body: AssignmentUpdateSchema, db: Session = Depends(get_db)):
    teacher_id = 1  # Demo teacher

    assignment = db.query(Assignment).filter(Assignment.id == body.id, Assignment.teacher_id == teacher_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    assignment.title = body.title
    assignment.description = body.description
    assignment.due_date = body.due_date
    assignment.max_score = body.max_score
    db.commit()
    db.refresh(assignment)

    return {"message": "Assignment updated successfully", "id": assignment.id}

# ── GET SUBMISSIONS FOR TEACHER ─────────────────────────────────────────────
@router.get("/submissions")
def get_submissions(db: Session = Depends(get_db)):
    teacher_id = 1  # Demo teacher

    submissions = db.query(Submission).join(Assignment).filter(Assignment.teacher_id == teacher_id).all()
    result = []
    for s in submissions:
        result.append({
            "id": s.id,
            "assignment_id": s.assignment_id,
            "assignment_title": s.assignment.title if s.assignment else None,
            "student_id": s.student_id,
            "student_name": s.student.name if s.student else None,
            "student_email": s.student.email if s.student else None,
            "essay_text": s.essay_text,
            "file_name": s.file_name,
            "submit_mode": s.submit_mode,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "ai_score": s.ai_score,
            "ai_detection_score": s.ai_detection_score,
            "ai_feedback": s.essay_text if hasattr(s, 'ai_feedback') else None,
            "final_score": s.final_score,
            "teacher_feedback": s.teacher_feedback,
            "status": s.status,
            "max_score": s.assignment.max_score if s.assignment else None,
        })
    return {"submissions": result}

# ── GET PENDING SUBMISSIONS ────────────────────────────────────────────────
@router.get("/submissions/pending")
def get_pending_submissions(db: Session = Depends(get_db)):
    teacher_id = 1  # Demo teacher

    submissions = db.query(Submission).join(Assignment).filter(
        Assignment.teacher_id == teacher_id,
        Submission.status.in_(["submitted", "ai_graded"])
    ).all()

    result = []
    for s in submissions:
        result.append({
            "id": s.id,
            "assignment_id": s.assignment_id,
            "assignment_title": s.assignment.title if s.assignment else None,
            "student_id": s.student_id,
            "student_name": s.student.name if s.student else None,
            "student_email": s.student.email if s.student else None,
            "essay_text": s.essay_text,
            "file_name": s.file_name,
            "submit_mode": s.submit_mode,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "ai_score": s.ai_score,
            "ai_detection_score": s.ai_detection_score,
            "ai_feedback": s.essay_text if hasattr(s, 'ai_feedback') else None,
            "final_score": s.final_score,
            "teacher_feedback": s.teacher_feedback,
            "status": s.status,
            "max_score": s.assignment.max_score if s.assignment else None,
        })
    return {"submissions": result}

# ── GRADE OR OVERRIDE SUBMISSION ───────────────────────────────────────────
@router.post("/submissions/grade")
def grade_submission(body: GradeRequestSchema, db: Session = Depends(get_db)):
    teacher_id = 1  # Demo teacher

    submission = db.query(Submission).join(Assignment).filter(
        Submission.id == body.submission_id,
        Assignment.teacher_id == teacher_id
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    submission.final_score = body.score
    submission.teacher_feedback = body.feedback
    submission.status = "graded"
    db.commit()
    db.refresh(submission)

    return {"message": "Grade saved successfully"}

# ── GET MESSAGES FOR TEACHER ──────────────────────────────────────────────
@router.get("/messages")
def get_messages(db: Session = Depends(get_db)):
    teacher_id = 1  # Demo teacher - in production this would come from the authenticated user
    
    messages = db.query(Message).filter(
        (Message.sender_id == teacher_id) | (Message.receiver_id == teacher_id)
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

# ── SEND REPLY TO STUDENT ─────────────────────────────────────────────
@router.post("/send-message")
def send_message(body: SendMessageSchema, db: Session = Depends(get_db)):
    sender_id = 1  # Demo teacher - in production this would come from the authenticated user
    
    # Check if receiver exists
    receiver = db.query(User).filter(User.id == body.receiver_id).first()
    if not receiver:
        raise HTTPException(status_code=404, detail="Student not found")
    
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

# ── MARK MESSAGE AS READ ──────────────────────────────────────────────
@router.post("/messages/{message_id}/read")
def mark_as_read(message_id: int, db: Session = Depends(get_db)):
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    message.is_read = True
    db.commit()
    
    return {"message": "Message marked as read"}
