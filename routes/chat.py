from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import ChatMessage, Submission
from pydantic import BaseModel
from typing import List

router = APIRouter()

class MessageCreate(BaseModel):
    submission_id: int
    sender_id: int
    sender_role: str
    sender_name: str
    message: str

class MessageResponse(BaseModel):
    id: int
    submission_id: int
    sender_id: int
    sender_role: str
    sender_name: str
    message: str
    created_at: str

    class Config:
        from_attributes = True

@router.get("/history/{submission_id}", response_model=dict)
def get_chat_history(submission_id: int, db: Session = Depends(get_db)):
    messages = db.query(ChatMessage).filter(
        ChatMessage.submission_id == submission_id
    ).order_by(ChatMessage.created_at).all()
    
    return {
        "success": True,
        "messages": [
            {
                "id": m.id,
                "submission_id": m.submission_id,
                "sender_id": m.sender_id,
                "sender_role": m.sender_role,
                "sender_name": m.sender_name,
                "message": m.message,
                "created_at": m.created_at.isoformat() if m.created_at else None
            }
            for m in messages
        ]
    }

@router.post("/send", response_model=MessageResponse)
def send_message(msg: MessageCreate, db: Session = Depends(get_db)):
    submission = db.query(Submission).filter(Submission.id == msg.submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    chat_msg = ChatMessage(
        submission_id=msg.submission_id,
        sender_id=msg.sender_id,
        sender_role=msg.sender_role,
        sender_name=msg.sender_name,
        message=msg.message
    )
    db.add(chat_msg)
    db.commit()
    db.refresh(chat_msg)
    
    return chat_msg