# C:\PROJECTS\Essay-Grader\backend\models\user.py
from sqlalchemy import Column, Integer, String, Enum, DateTime
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    id                  = Column(Integer, primary_key=True, index=True)
    name                = Column(String(100), nullable=False)
    email               = Column(String(100), unique=True, nullable=False)
    password            = Column(String(255), nullable=False)
    role                = Column(Enum("student", "teacher"), nullable=False)
    registration_number = Column(String(50), nullable=True)
    created_at          = Column(DateTime, server_default=func.now())