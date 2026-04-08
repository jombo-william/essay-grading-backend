# C:\PROJECTS\Essay-Grader\backend\models\message.py
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base

class Message(Base):
    __tablename__ = "messages"

    id         = Column(Integer, primary_key=True, index=True)
    sender_id  = Column(Integer, ForeignKey("users.id"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject    = Column(String(255), nullable=True)
    content    = Column(Text, nullable=False)
    message_type = Column(Enum("question", "answer", "response", "general"), default="general")
    is_read    = Column(Integer, default=0)  # 0 = unread, 1 = read
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    sender = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])