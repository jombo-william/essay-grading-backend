# """
# student_router.py
# =================
# ENTRY POINT — just imports and registers sub-routers.
# You should rarely need to edit this file.

# File map:
#   ai_grader.py          → change AI model, scoring logic, HF models
#   grading_prompt.py     → change the prompt sent to AI, response parsing
#   submission_routes.py  → change submit/unsubmit behaviour
#   assignment_routes.py  → change what assignments/results students see
# """

# from fastapi import APIRouter
# from submission_routes import router as submission_router
# from assignment_routes import router as assignment_router

# router = APIRouter()

# router.include_router(assignment_router)
# router.include_router(submission_router)