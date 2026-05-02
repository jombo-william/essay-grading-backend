from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from database import Base

class UserSession(Base):
    __tablename__ = "user_sessions"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_token = Column(String(255), unique=True, nullable=False)
    csrf_token    = Column(String(255), unique=True, nullable=False)
    ip_address    = Column(String(50), nullable=True)
    expires_at    = Column(DateTime, nullable=False)
