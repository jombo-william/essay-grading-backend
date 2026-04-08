# C:\PROJECTS\Essay-Grader\backend\models\submission.py
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base

class Submission(Base):
    __tablename__ = "submissions"

    id                = Column(Integer, primary_key=True, index=True)
    assignment_id     = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    student_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    essay_text        = Column(Text, nullable=True)
    file_name         = Column(String(255), nullable=True)
    submit_mode       = Column(Enum("write", "upload"), default="write")
    status            = Column(Enum("submitted", "ai_graded", "graded"), default="submitted")
    ai_score          = Column(Float, nullable=True)
    ai_detection_score = Column(Float, nullable=True)
    final_score       = Column(Float, nullable=True)
    teacher_feedback  = Column(Text, nullable=True)
    submitted_at      = Column(DateTime, server_default=func.now())

    # Relationships
    assignment = relationship("Assignment", back_populates="submissions")
    student = relationship("User", back_populates="submissions")