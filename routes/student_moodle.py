"""
routes/student_moodle.py
========================
Student-side Moodle integration.
Allows students to connect their Moodle token so submissions
are synced automatically when they submit in EssayGrade AI.
"""

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from auth_utils import require_student
import models

router = APIRouter()


class StudentMoodleConnectRequest(BaseModel):
    token:    str
    site_url: str


def moodle_call(site_url: str, token: str, function: str, params: dict = {}):
    """Make a Moodle Web Service call."""
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


# ── POST /api/student/moodle/connect ─────────────────────────────────────────
@router.post("/moodle/connect")
def connect_moodle(
    body: StudentMoodleConnectRequest,
    ctx: dict = Depends(require_student)
):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    # Verify token works
    try:
        moodle_call(
            body.site_url, body.token,
            "core_webservice_get_site_info", {}
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid token or site URL: {str(e)}"
        )

    # Save or update token
    existing = db.query(models.StudentMoodleToken).filter(
        models.StudentMoodleToken.student_id == user.id
    ).first()

    if existing:
        existing.token    = body.token
        existing.site_url = body.site_url
    else:
        db.add(models.StudentMoodleToken(
            student_id = user.id,
            token      = body.token,
            site_url   = body.site_url
        ))

    db.commit()
    return {"success": True, "message": "Connected to Moodle successfully!"}


# ── GET /api/student/moodle/status ───────────────────────────────────────────
@router.get("/moodle/status")
def moodle_status(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    record = db.query(models.StudentMoodleToken).filter(
        models.StudentMoodleToken.student_id == user.id
    ).first()

    return {
        "connected": record is not None,
        "site_url":  record.site_url if record else None
    }


# ── DELETE /api/student/moodle/disconnect ────────────────────────────────────
@router.delete("/moodle/disconnect")
def disconnect_moodle(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    record = db.query(models.StudentMoodleToken).filter(
        models.StudentMoodleToken.student_id == user.id
    ).first()

    if record:
        db.delete(record)
        db.commit()

    return {"success": True, "message": "Disconnected from Moodle"}

# ── GET /api/student/moodle/courses ──────────────────────────────────────────
@router.get("/moodle/courses")
def get_student_moodle_courses(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    record = db.query(models.StudentMoodleToken).filter(
        models.StudentMoodleToken.student_id == user.id
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Moodle not connected")

    # Get the Moodle user ID first
    site_info = moodle_call(
        record.site_url, record.token,
        "core_webservice_get_site_info", {}
    )
    moodle_user_id = site_info.get("userid")

    courses = moodle_call(
        record.site_url, record.token,
        "core_enrol_get_users_courses",
        {"userid": moodle_user_id}
    )

    return {"success": True, "courses": courses if isinstance(courses, list) else []}


# ── GET /api/student/moodle/assignments ──────────────────────────────────────
@router.get("/moodle/assignments")
def get_student_moodle_assignments(ctx: dict = Depends(require_student)):
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    record = db.query(models.StudentMoodleToken).filter(
        models.StudentMoodleToken.student_id == user.id
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Moodle not connected")

    # Get enrolled courses
    site_info      = moodle_call(record.site_url, record.token, "core_webservice_get_site_info", {})
    moodle_user_id = site_info.get("userid")
    courses        = moodle_call(record.site_url, record.token, "core_enrol_get_users_courses", {"userid": moodle_user_id})

    if not isinstance(courses, list) or not courses:
        return {"success": True, "assignments": []}

    # Get assignments for all courses at once
    params = {f"courseids[{i}]": c["id"] for i, c in enumerate(courses)}
    data   = moodle_call(record.site_url, record.token, "mod_assign_get_assignments", params)

    assignments = []
    for course in data.get("courses", []):
        for a in course.get("assignments", []):
            # Check if student already submitted
            try:
                status = moodle_call(
                    record.site_url, record.token,
                    "mod_assign_get_submission_status",
                    {"assignid": a["id"], "userid": moodle_user_id}
                )
                submission  = status.get("lastattempt", {}).get("submission", {})
                sub_status  = submission.get("status", "new")
                grade_info  = status.get("feedback", {}).get("grade", {})
                grade       = grade_info.get("grade", None) if grade_info else None
            except:
                sub_status = "new"
                grade      = None

            assignments.append({
                "id":          a["id"],
                "name":        a["name"],
                "intro":       a.get("intro", ""),
                "duedate":     a.get("duedate", 0),
                "maxgrade":    a.get("grade", 100),
                "course_id":   course["id"],
                "course_name": course["fullname"],
                "status":      sub_status,   # new / draft / submitted / graded
                "grade":       grade,
            })

    return {"success": True, "assignments": assignments}


# ── GET /api/student/moodle/grades ───────────────────────────────────────────
@router.get("/moodle/grades")
def get_student_moodle_grades(ctx: dict = Depends(require_student)):
    """Return the student's grades from all Moodle courses."""
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    record = db.query(models.StudentMoodleToken).filter(
        models.StudentMoodleToken.student_id == user.id
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Moodle not connected")

    site_info      = moodle_call(record.site_url, record.token, "core_webservice_get_site_info", {})
    moodle_user_id = site_info.get("userid")
    courses        = moodle_call(record.site_url, record.token, "core_enrol_get_users_courses", {"userid": moodle_user_id})

    all_grades = []
    for course in (courses if isinstance(courses, list) else []):
        try:
            grade_data = moodle_call(
                record.site_url, record.token,
                "gradereport_user_get_grade_items",
                {"courseid": course["id"], "userid": moodle_user_id}
            )
            for item in grade_data.get("usergrades", [{}])[0].get("gradeitems", []):
                all_grades.append({
                    "course_name": course["fullname"],
                    "item_name":   item.get("itemname", ""),
                    "grade":       item.get("gradeformatted", "N/A"),
                    "max_grade":   item.get("grademax", 100),
                    "feedback":    item.get("feedback", ""),
                })
        except:
            continue

    return {"success": True, "grades": all_grades}


# ── POST /api/student/moodle/sync-grade ──────────────────────────────────────
@router.post("/moodle/sync-grade/{submission_id}")
def sync_grade_to_moodle(
    submission_id: int,
    ctx: dict = Depends(require_student)
):
    """
    Called after AI grading is done on our system.
    Pushes the grade back to Moodle if:
      1. Student has Moodle connected
      2. The assignment has a moodle_assignment_id linked
    """
    user: models.User = ctx["user"]
    db: Session       = ctx["db"]

    # Get the submission with its grade
    submission = db.query(models.Submission).filter(
        models.Submission.id         == submission_id,
        models.Submission.student_id == user.id
    ).first()

    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.ai_score is None:
        return {"synced": False, "reason": "Not graded yet"}

    # Check if student has Moodle connected
    record = db.query(models.StudentMoodleToken).filter(
        models.StudentMoodleToken.student_id == user.id
    ).first()

    if not record:
        return {"synced": False, "reason": "Moodle not connected"}

    # Check if the assignment is linked to a Moodle assignment
    assignment = db.query(models.Assignment).filter(
        models.Assignment.id == submission.assignment_id
    ).first()

    if not assignment or not assignment.moodle_assignment_id:
        return {"synced": False, "reason": "Assignment not linked to Moodle"}

    # Get student's Moodle user ID
    try:
        site_info      = moodle_call(record.site_url, record.token, "core_webservice_get_site_info", {})
        moodle_user_id = site_info.get("userid")

        moodle_call(
            record.site_url, record.token,
            "mod_assign_save_grade",
            {
                "assignmentid":                                     assignment.moodle_assignment_id,
                "userid":                                           moodle_user_id,
                "grade":                                            float(submission.ai_score),
                "attemptnumber":                                    -1,
                "addattempt":                                       0,
                "workflowstate":                                    "graded",
                "applytoall":                                       1,
                "plugindata[assignfeedbackcomments_editor][text]":  submission.ai_feedback or "",
                "plugindata[assignfeedbackcomments_editor][format]": 1,
            }
        )
        return {"synced": True, "grade_pushed": submission.ai_score}

    except Exception as e:
        return {"synced": False, "reason": str(e)}