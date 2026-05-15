from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id                  = Column(Integer, primary_key=True, index=True)
    name                = Column(String(100), nullable=False)
    email               = Column(String(150), unique=True, nullable=False)
    password            = Column(String(255), nullable=False)
    role                = Column(String(20), nullable=False)
    registration_number = Column(String(50), nullable=True)
    phone               = Column(String(20), nullable=True)
    is_active           = Column(Boolean, default=True)
    created_at          = Column(TIMESTAMP, server_default=func.now())
    updated_at          = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    teacher_classes = relationship("TeacherClass", back_populates="teacher", cascade="all, delete-orphan")
    enrollments     = relationship("ClassEnrollment", back_populates="student", cascade="all, delete-orphan")
    assignments     = relationship("Assignment", back_populates="teacher")
    submissions     = relationship("Submission", back_populates="student")
    sessions        = relationship("UserSession", back_populates="user")
    google_tokens   = relationship("GoogleClassroomToken", back_populates="user")  # FIXED


class UserSession(Base):
    __tablename__ = "user_sessions"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_token = Column(String(255), unique=True, nullable=False)
    csrf_token    = Column(String(255), nullable=False)
    ip_address    = Column(String(45), nullable=False)
    expires_at    = Column(TIMESTAMP, nullable=False)
    created_at    = Column(TIMESTAMP, server_default=func.now())

    user = relationship("User", back_populates="sessions")


class Class(Base):
    __tablename__ = "classes"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(100), nullable=False)
    description  = Column(Text, nullable=True)
    subject      = Column(String(100), nullable=True)
    section      = Column(String(50), nullable=True)
    is_active    = Column(Boolean, default=True, nullable=False)
    gc_course_id = Column(String(100), nullable=True)
    created_at   = Column(TIMESTAMP, server_default=func.now())
    updated_at   = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    teacher_classes = relationship("TeacherClass", back_populates="cls", cascade="all, delete-orphan")
    assignments     = relationship("Assignment", back_populates="cls", cascade="all, delete-orphan")
    enrollments     = relationship("ClassEnrollment", back_populates="cls", cascade="all, delete-orphan")


class TeacherClass(Base):
    __tablename__ = "teacher_classes"

    __table_args__ = (
        UniqueConstraint("teacher_id", "class_id", name="uq_teacher_class"),
    )

    id          = Column(Integer, primary_key=True, index=True)
    teacher_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    class_id    = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False)
    assigned_at = Column(TIMESTAMP, server_default=func.now())

    teacher = relationship("User", back_populates="teacher_classes")
    cls     = relationship("Class", back_populates="teacher_classes")


class ClassEnrollment(Base):
    __tablename__ = "class_enrollments"

    __table_args__ = (
        UniqueConstraint("class_id", "student_id", name="uq_enrollment"),
    )

    id          = Column(Integer, primary_key=True, index=True)
    class_id    = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False)
    student_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    enrolled_at = Column(TIMESTAMP, server_default=func.now())

    cls     = relationship("Class", back_populates="enrollments")
    student = relationship("User", back_populates="enrollments")


class Assignment(Base):
    __tablename__ = "assignments"

    id           = Column(Integer, primary_key=True, index=True)
    teacher_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    class_id     = Column(Integer, ForeignKey("classes.id", ondelete="SET NULL"), nullable=True)

    title                = Column(String(255), nullable=False)
    description          = Column(Text, nullable=True)
    instructions         = Column(Text, nullable=False)
    reference_material   = Column(Text, nullable=True)
    max_score            = Column(Integer, default=100)
    due_date             = Column(DateTime, nullable=False)
    rubric               = Column(Text, nullable=True)
    is_active            = Column(Boolean, default=True)
    gc_coursework_id     = Column(String(100), nullable=True)
    moodle_assignment_id = Column(Integer, nullable=True)
    moodle_course_id     = Column(Integer, nullable=True)
    moodle_site_url      = Column(String(255), nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    teacher     = relationship("User", back_populates="assignments")
    cls         = relationship("Class", back_populates="assignments")
    submissions = relationship("Submission", back_populates="assignment")
    attachments = relationship("AssignmentAttachment", back_populates="assignment")


class AssignmentAttachment(Base):
    __tablename__ = "assignment_attachments"

    id            = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    file_name     = Column(String(255), nullable=False)
    file_path     = Column(String(500), nullable=False)
    file_type     = Column(String(100), nullable=True)
    file_size     = Column(Integer, nullable=True)
    uploaded_at   = Column(TIMESTAMP, server_default=func.now())

    assignment = relationship("Assignment", back_populates="attachments")


class Submission(Base):
    __tablename__ = "submissions"

    id                   = Column(Integer, primary_key=True, index=True)
    assignment_id        = Column(Integer, ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    student_id           = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    essay_text           = Column(Text, nullable=False)
    submit_mode          = Column(String(20), default="write")
    file_name            = Column(String(255), nullable=True)
    file_path            = Column(String(500), nullable=True)
    ai_score             = Column(Integer, nullable=True)
    ai_feedback          = Column(Text, nullable=True)
    ai_detection_score   = Column(Integer, nullable=True)
    ai_graded_at         = Column(TIMESTAMP, nullable=True)
    final_score          = Column(Integer, nullable=True)
    teacher_feedback     = Column(Text, nullable=True)
    graded_at            = Column(TIMESTAMP, nullable=True)
    status               = Column(String(20), default="pending")
    submitted_at         = Column(TIMESTAMP, server_default=func.now())
    updated_at           = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    moodle_assignment_id = Column(Integer, nullable=True)
    moodle_course_id     = Column(Integer, nullable=True)

    assignment   = relationship("Assignment", back_populates="submissions")
    student      = relationship("User", back_populates="submissions")
    ai_detection = relationship("AIDetectionLog", back_populates="submission", uselist=False)


class AIDetectionLog(Base):
    __tablename__ = "ai_detection_logs"

    id              = Column(Integer, primary_key=True, index=True)
    submission_id   = Column(Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False)
    detection_score = Column(Integer, nullable=False)
    flagged         = Column(Boolean, default=False)
    indicators      = Column(Text, nullable=True)
    model_version   = Column(String(50), nullable=True)
    detected_at     = Column(TIMESTAMP, server_default=func.now())

    submission = relationship("Submission", back_populates="ai_detection")


class Exam(Base):
    __tablename__ = "exams"

    id           = Column(Integer, primary_key=True, index=True)
    teacher_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    class_id     = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False)
    title        = Column(String(255), nullable=False)
    description  = Column(Text, nullable=True)
    instructions = Column(Text, nullable=False)
    due_date     = Column(DateTime, nullable=False)
    time_limit   = Column(Integer, default=60)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(TIMESTAMP, server_default=func.now())
    updated_at   = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    teacher     = relationship("User", foreign_keys=[teacher_id])
    cls         = relationship("Class", foreign_keys=[class_id])
    questions   = relationship("ExamQuestion", back_populates="exam", cascade="all, delete-orphan")
    submissions = relationship("ExamSubmission", back_populates="exam", cascade="all, delete-orphan")


class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    id             = Column(Integer, primary_key=True, index=True)
    exam_id        = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    type           = Column(String(20), nullable=False)
    prompt         = Column(Text, nullable=False)
    marks          = Column(Integer, default=1)
    options        = Column(Text, nullable=True)
    correct_option = Column(String(1), nullable=True)
    marking_guide  = Column(Text, nullable=True)
    order_index    = Column(Integer, default=0)
    created_at     = Column(TIMESTAMP, server_default=func.now())

    exam = relationship("Exam", back_populates="questions")


class ExamSubmission(Base):
    __tablename__ = "exam_submissions"

    id           = Column(Integer, primary_key=True, index=True)
    exam_id      = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    student_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status       = Column(String(20), default="submitted")
    total_score  = Column(Integer, nullable=True)
    submitted_at = Column(TIMESTAMP, server_default=func.now())
    graded_at    = Column(TIMESTAMP, nullable=True)

    exam    = relationship("Exam", back_populates="submissions")
    student = relationship("User", foreign_keys=[student_id])
    answers = relationship("ExamAnswer", back_populates="submission", cascade="all, delete-orphan")


class ExamAnswer(Base):
    __tablename__ = "exam_answers"

    id            = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=False)
    question_id   = Column(Integer, ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False)
    answer_text     = Column(Text, nullable=True)
    selected_option = Column(String(1), nullable=True)
    is_correct      = Column(Boolean, nullable=True)
    score_awarded   = Column(Integer, nullable=True)
    ai_feedback     = Column(Text, nullable=True)
    created_at      = Column(TIMESTAMP, server_default=func.now())

    submission = relationship("ExamSubmission", back_populates="answers")
    question   = relationship("ExamQuestion", foreign_keys=[question_id])


# FIXED: was incorrectly nested inside ExamAnswer
class StudentMoodleToken(Base):
    __tablename__ = "student_moodle_tokens"

    id           = Column(Integer, primary_key=True, index=True)
    student_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    moodle_token = Column(Text, nullable=False)
    moodle_url   = Column(String(255), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class GoogleClassroomToken(Base):
    __tablename__ = "google_classroom_tokens"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    access_token  = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_uri     = Column(String(255), nullable=True)
    client_id     = Column(String(255), nullable=True)
    client_secret = Column(String(255), nullable=True)
    scopes        = Column(Text, nullable=True)
    expiry        = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="google_tokens")