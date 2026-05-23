
from re import sub

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from auth_utils import require_teacher
import models
import json
import asyncio
import time
import re as _re

router = APIRouter()

DEFAULT_MOODLE_URL = "https://essaygrade.moodlecloud.com"
MOODLE_URL = DEFAULT_MOODLE_URL


def moodle_call(token: str, function: str, params: dict, site_url: str = DEFAULT_MOODLE_URL):
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
    site_url: str = DEFAULT_MOODLE_URL,
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
    site_url:     str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_assign_get_assignments",
        params   = {"courseids[0]": course_id},
        site_url = site_url
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
    site_url:      str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "mod_assign_get_submissions",
        params   = {"assignmentids[0]": assignment_id},
        site_url = site_url
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


# ── Pydantic models ───────────────────────────────────────────────────────

class MoodleAutoGradeRequest(BaseModel):
    moodle_token:         str
    moodle_assignment_id: int
    local_assignment_id:  int


class MoodleCreateAssignmentRequest(BaseModel):
    moodle_token:        str
    course_id:           int
    name:                str
    instructions:        str
    due_date:            Optional[int] = None
    max_grade:           Optional[int] = 100
    local_assignment_id: int
    site_url:            str = DEFAULT_MOODLE_URL


# ── POST /api/teacher/moodle/autograde ───────────────────────────────────
@router.post("/moodle/autograde")
def autograde_moodle(
    body: MoodleAutoGradeRequest,
    ctx: dict = Depends(require_teacher)
):
    from routes.ai_grader import grade_with_ai
    from routes.grading_prompt import build_grading_prompt

    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Local assignment not found")

    # subs_data = moodle_call(
    #     token    = body.moodle_token,
    #     function = "mod_assign_get_submissions",
    #     params   = {"assignmentids[0]": body.moodle_assignment_id},
    #     site_url = body.site_url
    # )

    subs_data = moodle_call(
        token    = body.moodle_token,
        function = "mod_assign_get_submissions",
        params   = {"assignmentids[0]": body.moodle_assignment_id},
        site_url = body.site_url
    )

    # fetch enrolled users to resolve userid → fullname
    enrolled_raw = moodle_call(
        token    = body.moodle_token,
        function = "mod_assign_get_participants",
        params   = {"assignid": body.moodle_assignment_id},
        site_url = body.site_url
    )
    user_names = {
        u["id"]: u.get("fullname", f"User {u['id']}")
        for u in (enrolled_raw if isinstance(enrolled_raw, list) else [])
    }

    results = []

    for assign in subs_data.get("assignments", []):
        all_subs = assign.get("submissions", [])
        print(f"DEBUG autograde: found {len(all_subs)} total submissions")

        for sub in all_subs:
            sub_status = sub.get("status", "unknown")
           #userid     = sub.get("userid")
            userid     = sub.get("userid")
            username   = user_names.get(userid) or f"User {userid}"
            #username   = sub.get("fullname") or sub.get("userfullname") or f"User {userid}"
            plugins    = [p.get("type") for p in sub.get("plugins", [])]
            print(f"DEBUG sub userid={userid} status={repr(sub_status)} plugins={plugins}")

            # Accept "submitted" and "new" — gate on content, not status
            ACCEPTED_STATUSES = {"submitted", "new"}
            if sub_status not in ACCEPTED_STATUSES:
                print(f"DEBUG skipping userid={userid} — status={repr(sub_status)}")
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
                    },
                    site_url = body.site_url
                )

                print(f"✅ Graded {student_name} → {grade['score']}/{assignment.max_score}")

                results.append({
                    "student_name":   student_name,
                    "moodle_user_id": user_id,
                    "score":          grade["score"],
                    "feedback":       grade["feedback"],
                    "status":         "graded",
                })
                print(f"DEBUG graded userid={userid} score={grade.get('total_score', grade.get('score', 0))}")
            except Exception as e:
                print(f"❌ Grading failed for {student_name}: {e}")
                results.append({
                    "student_name":   student_name,
                    "moodle_user_id": user_id,
                    "error":          str(e),
                    "status":         "failed",
                })

    graded_count  = len([r for r in results if r["status"] == "graded"])
    skipped_count = len([r for r in results if r["status"] == "skipped"])
    print(f"DEBUG autograde complete: {graded_count} graded, {skipped_count} skipped, {len(results)} total")

    return {
        "success":       True,
        "total_graded":  graded_count,
        "total_skipped": skipped_count,
        "results":       results
    }


# ── POST /api/teacher/moodle/autograde-quiz ──────────────────────────────
@router.post("/moodle/autograde-quiz")
async def autograde_moodle_quiz(
    body: MoodleQuizGradeRequest,
    ctx: dict = Depends(require_teacher)
):
    from services.grader import grade_essay
    import re

    user = ctx["user"]
    db   = ctx["db"]

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == body.local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Local assignment not found")

    enrolled = moodle_call(
        token    = body.moodle_token,
        function = "core_enrol_get_enrolled_users",
        params   = {"courseid": body.course_id},
        site_url = body.site_url
    )

    results = []

    for moodle_user in enrolled:
        userid   = moodle_user.get("id")
        fullname = moodle_user.get("fullname", f"User {userid}")

        if userid in [1, 2]:
            continue

        attempts_data = moodle_call(
            token    = body.moodle_token,
            function = "mod_quiz_get_user_attempts",
            params   = {
                "quizid":          body.quiz_id,
                "userid":          userid,
                "status":          "finished",
                "includepreviews": 0
            },
            site_url = body.site_url
        )

        attempts = attempts_data.get("attempts", [])
        if not attempts:
            continue

        attempt    = attempts[-1]
        attempt_id = attempt.get("id")

        try:
            review_data = moodle_call(
                token    = body.moodle_token,
                function = "mod_quiz_get_attempt_review",
                params   = {"attemptid": attempt_id, "page": -1},
                site_url = body.site_url
            )
        except Exception as e:
            results.append({
                "moodle_user_id": fullname,
                "error":  f"Could not fetch attempt review: {str(e)}",
                "status": "failed",
                "source": "moodle"
            })
            continue

        essay_parts = []
        for question in review_data.get("questions", []):
            qtype = question.get("type", "")
            if qtype != "essay":
                continue

            answer = question.get("responsesummary", "").strip()

            if not answer:
                html = question.get("html", "")
                if html:
                    clean = re.sub(r'<[^>]+>', ' ', html)
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    if len(clean) > 30:
                        answer = clean

            if not answer:
                answer = question.get("questionsummary", "").strip()

            print(f"DEBUG question type={qtype}, responsesummary={repr(question.get('responsesummary',''))[:100]}, keys={list(question.keys())}")

            if answer:
                essay_parts.append(answer)

        essay_text = "\n\n".join(essay_parts).strip()

        if not essay_text:
            results.append({
                "moodle_user_id": fullname,
                "error":  "No essay answer found in attempt",
                "status": "skipped",
                "source": "moodle"
            })
            continue

        rubric = None
        if assignment.rubric:
            try:
                rubric = json.loads(assignment.rubric)
            except Exception:
                pass

        grade      = None
        last_error = None
        for attempt_num in range(4):
            try:
                grade = grade_essay(essay_text, rubric)
                break
            except Exception as retry_err:
                last_error = retry_err
                error_str = str(retry_err)
                if "429" in error_str:
                    wait = 15 * (attempt_num + 1)
                    print(f"Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                elif "503" in error_str and attempt_num < 3:
                    time.sleep(2 ** attempt_num)
                else:
                    break

        if grade is None:
            results.append({
                "moodle_user_id": fullname,
                "error":  str(last_error) if last_error else "Grading failed",
                "status": "failed",
                "source": "moodle"
            })
            continue

        results.append({
            "moodle_user_id": fullname,
            "attempt_id":     attempt_id,
            "score":          grade.get("total_score", grade.get("score", 0)),
            "feedback":       grade.get("overall_feedback", grade.get("feedback", "")),
            "status":         "graded",
            "source":         "moodle"
        })

        await asyncio.sleep(1)

    return {
        "success":      True,
        "total_graded": len([r for r in results if r["status"] == "graded"]),
        "results":      results,
    }
