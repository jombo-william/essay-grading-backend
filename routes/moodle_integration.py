import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from auth_utils import require_teacher
import models
import json

router = APIRouter()

MOODLE_URL = "https://your-moodle-site.com"  # ← change this to your Moodle URL


def moodle_call(token: str, function: str, params: dict):
    """Make a Moodle Web Service call."""
    try:
        response = requests.post(
            f"{MOODLE_URL}/webservice/rest/server.php",
            data={
                "wstoken":            token,
                "wsfunction":         function,
                "moodlewsrestformat": "json",
                **params
            },
            timeout=30
        )
        data = response.json()
        if isinstance(data, dict) and data.get("exception"):
            raise HTTPException(
                status_code=400,
                detail=f"Moodle error: {data.get('message', 'Unknown error')}"
            )
        return data
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to Moodle at {MOODLE_URL}"
        )


# ── GET /api/teacher/moodle/courses ──────────────────────────────────────
@router.get("/moodle/courses")
def get_moodle_courses(
    moodle_token: str,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "core_enrol_get_users_courses",
        params   = {"userid": "0"}  # 0 = current user
    )
    return {"success": True, "courses": data}


# ── GET /api/teacher/moodle/assignments ──────────────────────────────────
@router.get("/moodle/assignments")
def get_moodle_assignments(
    moodle_token: str,
    course_id:    int,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_assign_get_assignments",
        params   = {"courseids[0]": course_id}
    )
    return {"success": True, "data": data}


# ── GET /api/teacher/moodle/submissions ──────────────────────────────────
@router.get("/moodle/submissions")
def get_moodle_submissions(
    moodle_token:  str,
    assignment_id: int,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_assign_get_submissions",
        params   = {"assignmentids[0]": assignment_id}
    )
    return {"success": True, "data": data}


# ── POST /api/teacher/moodle/autograde ───────────────────────────────────
class MoodleAutoGradeRequest(BaseModel):
    moodle_token:        str
    moodle_assignment_id: int
    local_assignment_id: int


@router.post("/moodle/autograde")
async def autograde_moodle(
    body: MoodleAutoGradeRequest,
    ctx: dict = Depends(require_teacher)
):
    from routes.grading import grade_essay_with_ai

    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    # Get local assignment
    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Local assignment not found")

    # Fetch submissions from Moodle
    subs_data = moodle_call(
        token    = body.moodle_token,
        function = "mod_assign_get_submissions",
        params   = {"assignmentids[0]": body.moodle_assignment_id}
    )

    results = []

    for assign in subs_data.get("assignments", []):
        for sub in assign.get("submissions", []):

            if sub.get("status") != "submitted":
                continue

            # Extract essay text from online text plugin
            essay_text = ""
            for plugin in sub.get("plugins", []):
                if plugin.get("type") == "onlinetext":
                    for field in plugin.get("editorfields", []):
                        essay_text += field.get("text", "")

            if not essay_text.strip():
                continue

            # Grade with AI
            try:
                grade = await grade_essay_with_ai(
                    essay_text         = essay_text,
                    instructions       = assignment.instructions,
                    reference_material = assignment.reference_material or "",
                    rubric             = json.loads(assignment.rubric) if assignment.rubric else None,
                    max_score          = assignment.max_score,
                )

                # Push grade back to Moodle immediately
                moodle_call(
                    token    = body.moodle_token,
                    function = "mod_assign_save_grade",
                    params   = {
                        "assignmentid":    body.moodle_assignment_id,
                        "userid":          sub["userid"],
                        "grade":           grade["score"],
                        "attemptnumber":   -1,
                        "addattempt":      0,
                        "workflowstate":   "released",
                        "plugindata[assignfeedbackcomments_editor][text]":   grade["feedback"],
                        "plugindata[assignfeedbackcomments_editor][format]": 1,
                    }
                )

                results.append({
                    "moodle_user_id": sub["userid"],
                    "score":   grade["score"],
                    "status":  "graded"
                })

            except Exception as e:
                results.append({
                    "moodle_user_id": sub["userid"],
                    "error":  str(e),
                    "status": "failed"
                })

    return {
        "success":      True,
        "total_graded": len(results),
        "results":      results
    }