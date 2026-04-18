
"""
student_exams.py
================
ENTRY POINT ONLY — wires exam sub-routers together.
Do NOT add endpoint logic here.

  List exams / results  →  routes/exam_routes.py
  Submit exam           →  routes/exam_submit.py
  AI grading logic      →  routes/exam_grader.py
"""

from fastapi import APIRouter
from routes.exam_routes import router as exam_routes_router
from routes.exam_submit import router as exam_submit_router

router = APIRouter()
router.include_router(exam_routes_router)
router.include_router(exam_submit_router)