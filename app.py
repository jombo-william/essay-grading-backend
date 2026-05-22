<<<<<<< HEAD
# C:\PROJECTS\Essay-Grader\backend\app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
from routes import auth, students, teacher

# Create all tables automatically
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Essay Grader API")

# Allow React frontend to talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(students.router, prefix="/api/students", tags=["Students"])
app.include_router(teacher.router, prefix="/api/teachers", tags=["Teachers"])

@app.get("/")
def root():
    return {"message": "Essay Grader API is running"}
=======


# #C:\Users\COMLAB1\Desktop\jombo\essayf-and-backend\backend\backend-jombo-essaygrade\app.py
# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
# from dotenv import load_dotenv

# #load_dotenv()

# load_dotenv(dotenv_path=r"C:\Users\comadmin\Desktop\jombo\essayf-and-backend\backend\backend-jombo-essaygrade\.env")


# #from routes import auth, teacher, student, exams, student_exams
# #from routes import auth, teacher, student, exams, student_exams, google_classroom, moodle_integration

# from routes import auth, teacher, student, exams, student_exams
# from routes import google_classroom, moodle_integration
# from routes.student_classroom import router as student_classroom_router
# from routes import grading


# app = FastAPI(title="JomboEssayGrade API")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=[
#     "http://localhost:5174",  # ✅ ADD THIS
#     "http://localhost:5173",
#     "http://localhost:3000",
#     "http://127.0.0.1:5174",  # ✅ optional but good
#     "http://127.0.0.1:5173",
#     "http://127.0.0.1:3000",
#     "https://essaygrade.vercel.app",
#     "https://jombo-essaygrade.vercel.app",
# ],
#     allow_credentials=True,
#      allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
   
#     allow_headers=["*"],
#     expose_headers=["*"], 
# )

# app.include_router(auth.router,          prefix="/api/auth",    tags=["Auth"])
# app.include_router(teacher.router,       prefix="/api/teacher", tags=["Teacher"])
# app.include_router(student.router,       prefix="/api/student", tags=["Student"])
# app.include_router(exams.router,         prefix="/api/teacher", tags=["Exams"])
# #app.include_router(student_exams.router, prefix="/api/student", tags=["Student Exams"])
# app.include_router(google_classroom.router,   prefix="/api/teacher", tags=["Google Classroom"])
# app.include_router(moodle_integration.router, prefix="/api/teacher", tags=["Moodle"])
# app.include_router(student_classroom_router, prefix="/api/student", tags=["Student Classroom"])
# app.include_router(grading.router, prefix="/api", tags=["Grading"])

# @app.get("/")
# def root():
#     return {"message": "JomboEssayGrade API is running ✅"}




from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from routes import auth, teacher, student, exams, student_exams
from routes import google_classroom, moodle_integration
from routes.student_classroom import router as student_classroom_router
from routes import grading


app = FastAPI(title="JomboEssayGrade API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "https://essaygrade.vercel.app",
        "https://jombo-essaygrade.vercel.app",
        "https://essaygrade-ai-xi.vercel.app",  # ← ADD THIS
        "https://essaygrade-aelxsmi8m-williams-projects-f21505ba.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(auth.router,                  prefix="/api/auth",    tags=["Auth"])
app.include_router(teacher.router,               prefix="/api/teacher", tags=["Teacher"])
app.include_router(student.router,               prefix="/api/student", tags=["Student"])
app.include_router(exams.router,                 prefix="/api/teacher", tags=["Exams"])
app.include_router(google_classroom.router,      prefix="/api/teacher", tags=["Google Classroom"])
app.include_router(moodle_integration.router,    prefix="/api/teacher", tags=["Moodle"])
app.include_router(student_classroom_router,     prefix="/api/student", tags=["Student Classroom"])
app.include_router(grading.router,               prefix="/api",         tags=["Grading"])

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": "JomboEssayGrade API is running ✅"}
>>>>>>> master
