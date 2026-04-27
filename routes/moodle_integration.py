import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from auth_utils import require_teacher
import models
import json

router = APIRouter()

MOODLE_URL = "http://localhost/moodle"


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
        params   = {"userid": "2"}  # 0 = current user
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


# @router.post("/moodle/autograde")
# async def autograde_moodle(
#     body: MoodleAutoGradeRequest,
#     ctx: dict = Depends(require_teacher)
# ):
#     from routes.grading import grade_essay_with_ai

#     user: models.User = ctx["user"]
#     db: Session       = ctx["db"]

#     # Get local assignment
#     assignment = db.query(models.Assignment).filter(
#         models.Assignment.id         == body.local_assignment_id,
#         models.Assignment.teacher_id == user.id,
#     ).first()

#     if not assignment:
#         raise HTTPException(status_code=404, detail="Local assignment not found")

#     # Fetch submissions from Moodle
#     subs_data = moodle_call(
#         token    = body.moodle_token,
#         function = "mod_assign_get_submissions",
#         params   = {"assignmentids[0]": body.moodle_assignment_id}
#     )

#     results = []

#     for assign in subs_data.get("assignments", []):
#         for sub in assign.get("submissions", []):

#             if sub.get("status") != "submitted":
#                 continue

#             # Extract essay text from online text plugin
#             essay_text = ""
#             for plugin in sub.get("plugins", []):
#                 if plugin.get("type") == "onlinetext":
#                     for field in plugin.get("editorfields", []):
#                         essay_text += field.get("text", "")

#             if not essay_text.strip():
#                 continue

#             # Grade with AI
#             try:
#                 grade = await grade_essay_with_ai(
#                     essay_text         = essay_text,
#                     instructions       = assignment.instructions,
#                     reference_material = assignment.reference_material or "",
#                     rubric             = json.loads(assignment.rubric) if assignment.rubric else None,
#                     max_score          = assignment.max_score,
#                 )

#                 # Push grade back to Moodle immediately
#                 moodle_call(
#                     token    = body.moodle_token,
#                     function = "mod_assign_save_grade",
#                     params   = {
#                         "assignmentid":    body.moodle_assignment_id,
#                         "userid":          sub["userid"],
#                         "grade":           grade["score"],
#                         "attemptnumber":   -1,
#                         "addattempt":      0,
#                         "workflowstate":   "released",
#                         "plugindata[assignfeedbackcomments_editor][text]":   grade["feedback"],
#                         "plugindata[assignfeedbackcomments_editor][format]": 1,
#                     }
#                 )

#                 results.append({
#                     "moodle_user_id": sub["userid"],
#                     "score":   grade["score"],
#                     "status":  "graded"
#                 })

#             except Exception as e:
#                 results.append({
#                     "moodle_user_id": sub["userid"],
#                     "error":  str(e),
#                     "status": "failed"
#                 })

#     return {
#         "success":      True,
#         "total_graded": len(results),
#         "results":      results
#     }


@router.post("/moodle/autograde")
async def autograde_moodle(
    body: MoodleAutoGradeRequest,
    ctx: dict = Depends(require_teacher)
):
    from services.grader import grade_essay

    user = ctx["user"]
    db   = ctx["db"]

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

            # Build rubric from local assignment
            rubric = None
            if assignment.rubric:
                try:
                    rubric = json.loads(assignment.rubric)
                except:
                    rubric = None

            # Grade with AI using your existing grader
            try:
                grade = grade_essay(essay_text, rubric)

                # Push grade back to Moodle
                moodle_call(
                    token    = body.moodle_token,
                    function = "mod_assign_save_grade",
                    params   = {
                        "assignmentid":  body.moodle_assignment_id,
                        "userid":        sub["userid"],
                        "grade":         grade.get("score", 0),
                        "attemptnumber": -1,
                        "addattempt":    0,
                        "workflowstate": "released",
                        "plugindata[assignfeedbackcomments_editor][text]":   grade.get("feedback", ""),
                        "plugindata[assignfeedbackcomments_editor][format]": 1,
                    }
                )

                results.append({
                    "moodle_user_id": sub["userid"],
                    "score":  grade.get("score", 0),
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

# POST /api/teacher/moodle/sync-students
@router.post("/moodle/sync-students")
def sync_moodle_students(
    moodle_token: str,
    course_id: int,
    local_class_id: int,
    ctx: dict = Depends(require_teacher)
):
    db = ctx["db"]
    
    # Get enrolled students from Moodle
    data = moodle_call(
        token=moodle_token,
        function="core_enrol_get_enrolled_users",
        params={"courseid": course_id}
    )
    
    synced = []
    for moodle_user in data:
        # Skip admin and guest
        if moodle_user.get("id") in [1, 2]:
            continue
            
        # Check if student already exists in your system
        existing = db.query(models.Student).filter(
            models.Student.email == moodle_user.get("email", "")
        ).first()
        
        if not existing:
            # Create new student in your system
            student = models.Student(
                name=moodle_user.get("fullname", ""),
                email=moodle_user.get("email", ""),
                class_id=local_class_id,
                moodle_user_id=moodle_user.get("id")
            )
            db.add(student)
            synced.append(moodle_user.get("fullname"))
    
    db.commit()
    return {"success": True, "synced": synced}

    # ── GET /api/teacher/moodle/quizzes ──────────────────────────────────────
@router.get("/moodle/quizzes")
def get_moodle_quizzes(
    moodle_token: str,
    course_id: int,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token=moodle_token,
        function="mod_quiz_get_quizzes_by_courses",
        params={"courseids[0]": course_id}
    )
    return {"success": True, "quizzes": data.get("quizzes", [])}


# ── GET /api/teacher/moodle/quiz-attempts ────────────────────────────────
@router.get("/moodle/quiz-attempts")
def get_quiz_attempts(
    moodle_token: str,
    quiz_id: int,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token=moodle_token,
        function="mod_quiz_get_user_attempts",
        params={
            "quizid": quiz_id,
            "status": "finished",
            "includepreviews": 0
        }
    )
    return {"success": True, "attempts": data.get("attempts", [])}


# ── POST /api/teacher/moodle/autograde-quiz ──────────────────────────────
class MoodleQuizGradeRequest(BaseModel):
    moodle_token: str
    quiz_id: int
    local_assignment_id: int


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

    # Get all finished attempts
    attempts_data = moodle_call(
        token=body.moodle_token,
        function="mod_quiz_get_user_attempts",
        params={
            "quizid": body.quiz_id,
            "status": "finished",
            "includepreviews": 0
        }
    )

    results = []

    for attempt in attempts_data.get("attempts", []):
        attempt_id = attempt.get("id")
        userid     = attempt.get("userid")

        # Get the actual answers from the attempt
        attempt_data = moodle_call(
            token=body.moodle_token,
            function="mod_quiz_get_attempt_data",
            params={
                "attemptid": attempt_id,
                "page": -1
            }
        )

        # Extract essay answers
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


@router.post("/moodle/create-assignment")
def create_moodle_assignment(
    body: MoodleCreateAssignmentRequest,
    ctx: dict = Depends(require_teacher)
):
    db   = ctx["db"]
    user = ctx["user"]

    # Get course sections
    sections = moodle_call(
        token    = body.moodle_token,
        function = "core_course_get_contents",
        params   = {"courseid": body.course_id}
    )
    section_id = sections[0]["id"] if sections else 0

    # Create assignment in Moodle
    result = moodle_call(
        token    = body.moodle_token,
        function = "mod_assign_add_instance",
        params   = {
            "courseid":                              body.course_id,
            "name":                                  body.name,
            "intro":                                 body.instructions,
            "introformat":                           1,
            "section":                               section_id,
            "duedate":                               body.due_date or 0,
            "grade":                                 body.max_grade,
            "submissiondrafts":                      0,
            "assignsubmission_onlinetext_enabled":   1,
            "assignsubmission_file_enabled":         0,
        }
    )

    moodle_assignment_id = result.get("assignmentid") or result

    # Save the Moodle assignment ID in your local assignment
    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if assignment:
        assignment.moodle_assignment_id = moodle_assignment_id
        assignment.moodle_course_id     = body.course_id
        db.commit()

    return {
        "success":             True,
        "moodle_assignment_id": moodle_assignment_id,
        "message":             f"Assignment created in Moodle successfully"
    }