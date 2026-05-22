

"""
routes/student.py
=================
Entry point — just imports and registers sub-routers.
You should rarely need to edit this file.

File map:
  routes/ai_grader.py          → change AI model, scoring logic, HF models
  routes/grading_prompt.py     → change the prompt sent to AI, response parsing
  routes/submission_routes.py  → change submit/unsubmit behaviour
  routes/assignment_routes.py  → change what assignments/results students see
"""

from fastapi import APIRouter
from routes.submission_routes import router as submission_router
from routes.assignment_routes import router as assignment_router

router = APIRouter()

router.include_router(assignment_router)
router.include_router(submission_router)