import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from auth_utils import require_teacher
import models
import json

router = APIRouter()

MOODLE_URL = "https://essaygrade.moodlecloud.com"


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


def get_moodle_user_name(token: str, user_id: int) -> str:
    """Fetch a student's full name from Moodle by user ID."""
    try:
        data = moodle_call(
            token    = token,
            function = "core_user_get_users_by_field",
            params   = {
                "field":    "id",
                "values[0]": user_id,
            }
        )
        if isinstance(data, list) and len(data) > 0:
            user = data[0]
            return f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
    except Exception as e:
        print(f"⚠️ Could not fetch name for user {user_id}: {e}")
    return f"User {user_id}"


# ── GET /api/teacher/moodle/courses ──────────────────────────────────────
@router.get("/moodle/courses")
def get_moodle_courses(
    moodle_token: str,
    ctx: dict = Depends(require_teacher)
):
    # Step 1: Get real user ID from the token
    site_info = moodle_call(
        token    = moodle_token,
        function = "core_webservice_get_site_info",
        params   = {}
    )

    user_id = site_info.get("userid")
    if not user_id:
        raise HTTPException(status_code=400, detail="Could not get Moodle user ID from token")

    print(f"🎓 Moodle user ID: {user_id}")

    # Step 2: Fetch courses using real user ID
    data = moodle_call(
        token    = moodle_token,
        function = "core_enrol_get_users_courses",
        params   = {"userid": user_id}
    )

    print(f"📚 Raw Moodle courses: {data}")

    courses = [
        {
            "id":        c.get("id"),
            "name":      c.get("fullname"),
            "shortname": c.get("shortname"),
            "category":  c.get("categoryid"),
        }
        for c in (data if isinstance(data, list) else [])
    ]

    print(f"📚 Found {len(courses)} Moodle courses")
    return {"success": True, "courses": courses}


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

    assignments = []
    for course in data.get("courses", []):
        for assign in course.get("assignments", []):
            assignments.append({
                "id":          assign.get("id"),
                "name":        assign.get("name"),
                "description": assign.get("intro", ""),
                "due_date":    assign.get("duedate", 0),
                "max_grade":   assign.get("grade", 100),
                "course_id":   assign.get("course"),
            })

    print(f"📝 Found {len(assignments)} assignments for course {course_id}")
    return {"success": True, "assignments": assignments}


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

    submissions = []
    for assign in data.get("assignments", []):
        for sub in assign.get("submissions", []):
            user_id       = sub.get("userid")
            student_name  = get_moodle_user_name(moodle_token, user_id)
            submissions.append({
                "id":           sub.get("id"),
                "user_id":      user_id,
                "student_name": student_name,
                "status":       sub.get("status"),
                "time_modified": sub.get("timemodified"),
            })

    return {"success": True, "submissions": submissions}


# ── POST /api/teacher/moodle/autograde ───────────────────────────────────
class MoodleAutoGradeRequest(BaseModel):
    moodle_token:         str
    moodle_assignment_id: int
    local_assignment_id:  int


@router.post("/moodle/autograde")
def autograde_moodle(
    body: MoodleAutoGradeRequest,
    ctx: dict = Depends(require_teacher)
):
    from routes.ai_grader import grade_with_ai
    from routes.grading_prompt import build_grading_prompt

    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

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

            user_id      = sub.get("userid")
            student_name = get_moodle_user_name(body.moodle_token, user_id)

            # Extract essay text from online text plugin
            essay_text = ""
            for plugin in sub.get("plugins", []):
                if plugin.get("type") == "onlinetext":
                    for field in plugin.get("editorfields", []):
                        essay_text += field.get("text", "")

            # Strip HTML tags if present
            import re
            essay_text = re.sub(r"<[^>]+>", " ", essay_text).strip()

            if not essay_text:
                print(f"⚠️ No text found for {student_name} — skipping")
                results.append({
                    "student_name":   student_name,
                    "moodle_user_id": user_id,
                    "error":          "No text content in submission",
                    "status":         "skipped",
                })
                continue

            try:
                word_count = len(essay_text.split())
                prompt     = build_grading_prompt(assignment, essay_text, word_count)
                grade      = grade_with_ai(
                    prompt     = prompt,
                    assignment = assignment,
                    essay_text = essay_text,
                    word_count = word_count,
                )

                # Push grade back to Moodle
                moodle_call(
                    token    = body.moodle_token,
                    function = "mod_assign_save_grade",
                    params   = {
                        "assignmentid":  body.moodle_assignment_id,
                        "userid":        user_id,
                        "grade":         grade["score"],
                        "attemptnumber": -1,
                        "addattempt":    0,
                        "workflowstate": "released",
                        "plugindata[assignfeedbackcomments_editor][text]":   grade["feedback"],
                        "plugindata[assignfeedbackcomments_editor][format]": 1,
                    }
                )

                print(f"✅ Graded {student_name} → {grade['score']}/{assignment.max_score}")

                results.append({
                    "student_name":   student_name,
                    "moodle_user_id": user_id,
                    "score":          grade["score"],
                    "feedback":       grade["feedback"],
                    "status":         "graded",
                })

            except Exception as e:
                print(f"❌ Grading failed for {student_name}: {e}")
                results.append({
                    "student_name":   student_name,
                    "moodle_user_id": user_id,
                    "error":          str(e),
                    "status":         "failed",
                })

    return {
        "success":      True,
        "total_graded": len([r for r in results if r["status"] == "graded"]),
        "results":      results,
    }