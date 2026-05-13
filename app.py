

#C:\Users\COMLAB1\Desktop\jombo\essayf-and-backend\backend\backend-jombo-essaygrade\app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from database import engine
import models
import sqlalchemy as sa

status = sa.Column(
    sa.Enum("pending", "approved", "rejected", name="status_enum"),
    nullable=False
)
# app.py — add after the existing imports at the top
from routes.student_moodle import router as student_moodle_router


#load_dotenv()
models.Base.metadata.create_all(bind=engine)

load_dotenv(dotenv_path=r"C:\Users\comadmin\Desktop\jombo\essayf-and-backend\backend\backend-jombo-essaygrade\.env")


#from routes import auth, teacher, student, exams, student_exams
#from routes import auth, teacher, student, exams, student_exams, google_classroom, moodle_integration

from routes import auth, teacher, student, exams, student_exams
from routes import google_classroom, moodle_integration
from routes.student_classroom import router as student_classroom_router
from routes import grading

# from routes.quiz_routes   import router as quiz_router
# from routes.student_quiz  import router as student_quiz_router



app = FastAPI(title="JomboEssayGrade API")



app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "http://localhost:5174",  # ✅ ADD THIS
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5174",  # ✅ optional but good
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "https://essaygrade.vercel.app",
    "https://jombo-essaygrade.vercel.app",
],
    allow_credentials=True,
     allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
   
    allow_headers=["*"],
    expose_headers=["*"], 
)

app.include_router(auth.router,          prefix="/api/auth",    tags=["Auth"])
app.include_router(teacher.router,       prefix="/api/teacher", tags=["Teacher"])
app.include_router(student.router,       prefix="/api/student", tags=["Student"])
app.include_router(exams.router,         prefix="/api/teacher", tags=["Exams"])
#app.include_router(student_exams.router, prefix="/api/student", tags=["Student Exams"])
app.include_router(google_classroom.router,   prefix="/api/teacher", tags=["Google Classroom"])
app.include_router(moodle_integration.router, prefix="/api/teacher", tags=["Moodle"])
app.include_router(student_classroom_router, prefix="/api/student", tags=["Student Classroom"])
app.include_router(grading.router, prefix="/api", tags=["Grading"])
app.include_router(student_moodle_router, prefix="/api/student", tags=["Student Moodle"])
# Under teacher routes
# app.include_router(quiz_router,        prefix="/api/teacher", tags=["quizzes"])

# # Under student routes
# app.include_router(student_quiz_router, prefix="/api/student", tags=["student-quizzes"])

@app.get("/")
def root():
    return {"message": "JomboEssayGrade API is running ✅"}