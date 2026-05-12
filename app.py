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