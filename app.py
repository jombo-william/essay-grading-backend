

#C:\Users\COMLAB1\Desktop\jombo\essayf-and-backend\backend\backend-jombo-essaygrade\app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

#from routes import auth, teacher, student, exams, student_exams
#from routes import auth, teacher, student, exams, student_exams, google_classroom, moodle_integration

from routes import auth, teacher, student, exams, student_exams
from routes import google_classroom, moodle_integration
from routes.student_classroom import router as student_classroom_router


app = FastAPI(title="JomboEssayGrade API")

app.add_middleware(
    CORSMiddleware,
  allow_origins=[
    "http://localhost:5173",
    "http://localhost:5174",  # 👈 ADD THIS
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",  # 👈 ADD THIS TOO
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

@app.get("/")
def root():
    return {"message": "JomboEssayGrade API is running ✅"}