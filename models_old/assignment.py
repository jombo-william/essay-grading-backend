# C:\PROJECTS\Essay-Grader\backend\models\assignment.py
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base

class Assignment(Base):
    __tablename__ = "assignments"

    id          = Column(Integer, primary_key=True, index=True)
    title       = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    due_date    = Column(DateTime, nullable=False)
    max_score   = Column(Integer, default=100)
    teacher_id  = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at  = Column(DateTime, server_default=func.now())

    # Relationships
    teacher = relationship("User", back_populates="assignments")
    submissions = relationship("Submission", back_populates="assignment")