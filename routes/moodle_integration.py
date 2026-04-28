


import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from auth_utils import require_teacher
import models
import json

router = APIRouter()

DEFAULT_MOODLE_URL = "https://essaygrade.moodlecloud.com"


def moodle_call(token: str, function: str, params: dict, site_url: str = DEFAULT_MOODLE_URL):
    """Make a Moodle Web Service call."""
    # Strip trailing slash
    site_url = site_url.rstrip("/")
    try:
        response = requests.post(
            f"{site_url}/webservice/rest/server.php",
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
            detail=f"Cannot connect to Moodle at {site_url}"
        )


# ── GET /api/teacher/moodle/courses ──────────────────────────────────────
@router.get("/moodle/courses")
def get_moodle_courses(
    moodle_token: str,
    site_url: str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token     = moodle_token,
        function  = "core_enrol_get_users_courses",
        params    = {"userid": "2"},
        site_url  = site_url
    )
    return {"success": True, "courses": data}


# ── GET /api/teacher/moodle/assignments ──────────────────────────────────
@router.get("/moodle/assignments")
def get_moodle_assignments(
    moodle_token: str,
    course_id:    int,
    site_url:     str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_assign_get_assignments",
        params   = {"courseids[0]": course_id},
        site_url = site_url
    )
    return {"success": True, "data": data}


# ── GET /api/teacher/moodle/submissions ──────────────────────────────────
@router.get("/moodle/submissions")
def get_moodle_submissions(
    moodle_token:  str,
    assignment_id: int,
    site_url:      str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_assign_get_submissions",
        params   = {"assignmentids[0]": assignment_id},
        site_url = site_url
    )
    return {"success": True, "data": data}


# ── POST /api/teacher/moodle/autograde ───────────────────────────────────
class MoodleAutoGradeRequest(BaseModel):
    moodle_token:         str
    moodle_assignment_id: int
    local_assignment_id:  int
    site_url:             str = DEFAULT_MOODLE_URL


@router.post("/moodle/autograde")
async def autograde_moodle(
    body: MoodleAutoGradeRequest,
    ctx: dict = Depends(require_teacher)
):
    from services.grader import grade_essay

    user = ctx["user"]
    db   = ctx["db"]

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Local assignment not found")

    subs_data = moodle_call(
        token    = body.moodle_token,
        function = "mod_assign_get_submissions",
        params   = {"assignmentids[0]": body.moodle_assignment_id},
        site_url = body.site_url
    )

    results = []

    for assign in subs_data.get("assignments", []):
        for sub in assign.get("submissions", []):

            if sub.get("status") != "submitted":
                continue

            essay_text = ""
            for plugin in sub.get("plugins", []):
                if plugin.get("type") == "onlinetext":
                    for field in plugin.get("editorfields", []):
                        essay_text += field.get("text", "")

            if not essay_text.strip():
                continue

            rubric = None
            if assignment.rubric:
                try:
                    rubric = json.loads(assignment.rubric)
                except:
                    rubric = None

            try:
                grade = grade_essay(essay_text, rubric)


                feedback_text = grade.get("overall_feedback", grade.get("feedback", ""))
                moodle_call(
                    token    = body.moodle_token,
                    function = "mod_assign_save_grade",
                    params   = {
                        "assignmentid":                                       body.moodle_assignment_id,
                        "userid":                                             sub["userid"],
                        "grade":                                              float(grade.get("total_score", grade.get("score", 0))),
                        "attemptnumber":                                      -1,
                        "addattempt":                                         0,
                        "workflowstate":                                      "graded",   # "released" can fail on some Moodle versions
                        "applytoall":                                         1,
                        "plugindata[assignfeedbackcomments_editor][text]":    feedback_text,
                        "plugindata[assignfeedbackcomments_editor][format]":  1,
                    },
                    site_url = body.site_url
                )


                results.append({
                    "moodle_user_id": sub["userid"],
                    "score":  grade.get("total_score", grade.get("score", 0)),  # handles both grader.py versions
                    "status": "graded"
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


# ── POST /api/teacher/moodle/sync-students ───────────────────────────────
@router.post("/moodle/sync-students")
def sync_moodle_students(
    moodle_token:   str,
    course_id:      int,
    local_class_id: int,
    site_url:       str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    db = ctx["db"]

    data = moodle_call(
        token    = moodle_token,
        function = "core_enrol_get_enrolled_users",
        params   = {"courseid": course_id},
        site_url = site_url
    )

    synced = []
    for moodle_user in data:
        if moodle_user.get("id") in [1, 2]:
            continue

        existing = db.query(models.Student).filter(
            models.Student.email == moodle_user.get("email", "")
        ).first()

        if not existing:
            student = models.Student(
                name          = moodle_user.get("fullname", ""),
                email         = moodle_user.get("email", ""),
                class_id      = local_class_id,
                moodle_user_id= moodle_user.get("id")
            )
            db.add(student)
            synced.append(moodle_user.get("fullname"))

    db.commit()
    return {"success": True, "synced": synced}


# ── GET /api/teacher/moodle/quizzes ──────────────────────────────────────
@router.get("/moodle/quizzes")
def get_moodle_quizzes(
    moodle_token: str,
    course_id:    int,
    site_url:     str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_quiz_get_quizzes_by_courses",
        params   = {"courseids[0]": course_id},
        site_url = site_url
    )
    return {"success": True, "quizzes": data.get("quizzes", [])}


# ── GET /api/teacher/moodle/quiz-attempts ────────────────────────────────
@router.get("/moodle/quiz-attempts")
def get_quiz_attempts(
    moodle_token: str,
    quiz_id:      int,
    site_url:     str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_quiz_get_user_attempts",
        params   = {
            "quizid":          quiz_id,
            "status":          "finished",
            "includepreviews": 0
        },
        site_url = site_url
    )
    return {"success": True, "attempts": data.get("attempts", [])}


# ── POST /api/teacher/moodle/autograde-quiz ──────────────────────────────
class MoodleQuizGradeRequest(BaseModel):
    moodle_token:        str
    quiz_id:             int
    local_assignment_id: int
    site_url:            str = DEFAULT_MOODLE_URL


@router.post("/moodle/autograde-quiz")
async def autograde_moodle_quiz(
    body: MoodleQuizGradeRequest,
    ctx: dict = Depends(require_teacher)
):
    from services.grader import grade_essay

    user = ctx["user"]
    db   = ctx["db"]

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Local assignment not found")

    attempts_data = moodle_call(
        token    = body.moodle_token,
        function = "mod_quiz_get_user_attempts",
        params   = {
            "quizid":          body.quiz_id,
            "status":          "finished",
            "includepreviews": 0
        },
        site_url = body.site_url
    )

    results = []

    for attempt in attempts_data.get("attempts", []):
        attempt_id = attempt.get("id")
        userid     = attempt.get("userid")

        attempt_data = moodle_call(
            token    = body.moodle_token,
            function = "mod_quiz_get_attempt_data",
            params   = {"attemptid": attempt_id, "page": -1},
            site_url = body.site_url
        )

        essay_text = ""
        for question in attempt_data.get("questions", []):
            if question.get("type") == "essay":
                essay_text += question.get("responsefileareas", "")
                for key, val in question.get("questionsummary", {}).items():
                    if "answer" in key.lower():
                        essay_text += str(val)

        if not essay_text.strip():
            continue

        try:
            rubric = json.loads(assignment.rubric) if assignment.rubric else None
            grade  = grade_essay(essay_text, rubric)

            results.append({
                "moodle_user_id": userid,
                "attempt_id":     attempt_id,
                "score":          grade.get("score", 0),
                "feedback":       grade.get("feedback", ""),
                "status":         "graded"
            })

        except Exception as e:
            results.append({
                "moodle_user_id": userid,
                "error":          str(e),
                "status":         "failed"
            })

    return {
        "success":      True,
        "total_graded": len(results),
        "results":      results
    }


# ── POST /api/teacher/moodle/create-assignment ───────────────────────────
class MoodleCreateAssignmentRequest(BaseModel):
    moodle_token:        str
    course_id:           int
    name:                str
    instructions:        str
    due_date:            Optional[int] = None
    max_grade:           Optional[int] = 100
    local_assignment_id: int
    site_url:            str = DEFAULT_MOODLE_URL


@router.post("/moodle/create-assignment")
def create_moodle_assignment(
    body: MoodleCreateAssignmentRequest,
    ctx: dict = Depends(require_teacher)
):
    db   = ctx["db"]
    user = ctx["user"]

    sections = moodle_call(
        token    = body.moodle_token,
        function = "core_course_get_contents",
        params   = {"courseid": body.course_id},
        site_url = body.site_url
    )
    section_id = sections[0]["id"] if sections else 0

    result = moodle_call(
        token    = body.moodle_token,
        function = "mod_assign_add_instance",
        params   = {
            "courseid":                            body.course_id,
            "name":                                body.name,
            "intro":                               body.instructions,
            "introformat":                         1,
            "section":                             section_id,
            "duedate":                             body.due_date or 0,
            "grade":                               body.max_grade,
            "submissiondrafts":                    0,
            "assignsubmission_onlinetext_enabled": 1,
            "assignsubmission_file_enabled":       0,
        },
        site_url = body.site_url
    )

    moodle_assignment_id = result.get("assignmentid") or result

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if assignment:
        assignment.moodle_assignment_id = moodle_assignment_id
        assignment.moodle_course_id     = body.course_id
        db.commit()

    return {
        "success":              True,
        "moodle_assignment_id": moodle_assignment_id,
        "message":              "Assignment created in Moodle successfully"
    }