
from re import sub

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from auth_utils import require_teacher
import models
import json
import asyncio
import time
import re as _re

router = APIRouter()

DEFAULT_MOODLE_URL = "https://essaygrade.moodlecloud.com"


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


def download_moodle_file(url: str, token: str) -> bytes:
    """Download a file from Moodle, appending the token for auth."""
    sep = "&" if "?" in url else "?"
    download_url = f"{url}{sep}token={token}"
    response = requests.get(download_url, timeout=60)
    response.raise_for_status()
    return response.content


def extract_essay_text_from_submission(sub: dict, moodle_token: str) -> str:
    """
    Try every possible submission plugin to find essay text.
    Priority:
      1. onlinetext  (typed directly in Moodle)
      2. file        (uploaded PDF / DOCX / image / txt)
      3. comments    (fallback)
    Returns the best non-empty text found, or "".
    """
    from file_extractor import extract_text_from_bytes

    essay_text = ""

    for plugin in sub.get("plugins", []):
        ptype = plugin.get("type", "")

        # ── 1. Online text ────────────────────────────────────────────────
        if ptype == "onlinetext":
            for field in plugin.get("editorfields", []):
                raw = field.get("text", "")
                if raw.strip():
                    clean = _re.sub(r'<[^>]+>', ' ', raw)
                    clean = _re.sub(r'\s+', ' ', clean).strip()
                    if clean:
                        essay_text += clean + "\n\n"

        # ── 2. File upload ────────────────────────────────────────────────
        elif ptype == "file":
            for filearea in plugin.get("fileareas", []):
                for f in filearea.get("files", []):
                    filename    = f.get("filename", "")
                    file_url    = f.get("fileurl", "")
                    mimetype    = f.get("mimetype", "")

                    # skip empty placeholder files Moodle adds by default
                    filesize = f.get("filesize", 0)
                    if not file_url or filesize == 0:
                        print(f"DEBUG skipping placeholder file: {filename}")
                        continue

                    print(f"DEBUG downloading file: {filename} ({mimetype}, {filesize} bytes) from {file_url[:60]}...")

                    try:
                        raw_bytes = download_moodle_file(file_url, moodle_token)
                        extracted = extract_text_from_bytes(raw_bytes, filename)
                        if extracted.strip():
                            essay_text += extracted.strip() + "\n\n"
                            print(f"DEBUG extracted {len(extracted)} chars from {filename}")
                        else:
                            print(f"DEBUG extraction returned empty for {filename}")
                    except Exception as e:
                        print(f"DEBUG file download/extract failed for {filename}: {e}")

        # ── 3. Comments (last resort) ─────────────────────────────────────
        elif ptype == "comments":
            for field in plugin.get("editorfields", []):
                raw = field.get("text", "").strip()
                if raw and len(raw) > 20:   # ignore trivial one-liners
                    clean = _re.sub(r'<[^>]+>', ' ', raw)
                    clean = _re.sub(r'\s+', ' ', clean).strip()
                    if clean:
                        essay_text += clean + "\n\n"

    return essay_text.strip()


# ── GET /api/teacher/moodle/courses ──────────────────────────────────────
@router.get("/moodle/courses")
def get_moodle_courses(
    moodle_token: str,
    site_url: str = DEFAULT_MOODLE_URL,
    ctx: dict = Depends(require_teacher)
):
    data = moodle_call(
        token    = moodle_token,
        function = "core_enrol_get_users_courses",
        params   = {"userid": "2"},
        site_url = site_url
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
    site_url:             str = DEFAULT_MOODLE_URL


class MoodleQuizGradeRequest(BaseModel):
    moodle_token:        str
    quiz_id:             int
    course_id:           int
    local_assignment_id: int
    site_url:            str = DEFAULT_MOODLE_URL


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

            # ── Extract text from ALL plugin types (online text + files) ──
            essay_text = extract_essay_text_from_submission(sub, body.moodle_token)

            if not essay_text.strip():
                print(f"DEBUG skipping userid={userid} — no essay text found after extraction")
                results.append({
                    #"moodle_user_id": userid,
                    "moodle_user_id": username,
                    #"moodle_user_id": sub.get("fullname") or f"User {username}",
                    "status":  "skipped",
                    "error":   "No readable text found in submission (tried online text and uploaded files)",
                    "source":  "moodle"
                })
                continue

            print(f"DEBUG grading userid={userid} — {len(essay_text)} chars extracted")

            rubric = None
            if assignment.rubric:
                try:
                    rubric = json.loads(assignment.rubric)
                except Exception:
                    rubric = None

            await asyncio.sleep(1)

            grade      = None
            last_error = None
            for attempt in range(4):
                try:
                    grade = grade_essay(essay_text, rubric)
                    break
                except Exception as retry_err:
                    last_error = retry_err
                    error_str = str(retry_err)
                    if "429" in error_str:
                        wait = 15 * (attempt + 1)
                        print(f"Rate limited, waiting {wait}s before retry {attempt+1}...")
                        time.sleep(wait)
                    elif "503" in error_str and attempt < 3:
                        time.sleep(2 ** attempt)
                    else:
                        break

            if grade is None:
                error_msg = str(last_error) if last_error else "Unknown grading error"
                if "googleapis.com" in error_msg or "generativelanguage" in error_msg:
                    error_msg = "AI grading service temporarily unavailable (503). Please retry."
                results.append({
                    #"moodle_user_id": userid,
                    "moodle_user_id": username,
                    #"moodle_user_id": sub.get("fullname") or f"User {username}",
                    "error":  error_msg,
                    "status": "failed",
                    "source": "moodle"
                })
                continue

            try:
                feedback_text = grade.get("overall_feedback", grade.get("feedback", ""))
                moodle_call(
                    token    = body.moodle_token,
                    function = "mod_assign_save_grade",
                    params   = {
                        "assignmentid":                                      body.moodle_assignment_id,
                        "userid":                                            userid,
                        "grade":                                             float(grade.get("total_score", grade.get("score", 0))),
                        "attemptnumber":                                     -1,
                        "addattempt":                                        0,
                        "workflowstate":                                     "graded",
                        "applytoall":                                        1,
                        "plugindata[assignfeedbackcomments_editor][text]":   feedback_text,
                        "plugindata[assignfeedbackcomments_editor][format]": 1,
                    },
                    site_url = body.site_url
                )
                results.append({
                    #"moodle_user_id": userid,
                    "moodle_user_id": username,
                    #"moodle_user_id": sub.get("fullname") or f"User {username}",
                    "score":    grade.get("total_score", grade.get("score", 0)),
                    "feedback": grade.get("overall_feedback", grade.get("feedback", "")),
                    "status":   "graded",
                    "source":   "moodle"
                })
                print(f"DEBUG graded userid={userid} score={grade.get('total_score', grade.get('score', 0))}")
            except Exception as e:
                error_msg = str(e)
                if "googleapis.com" in error_msg or "generativelanguage" in error_msg:
                    error_msg = "AI grading service temporarily unavailable (503). Please retry."
                results.append({
                    #"moodle_user_id": userid,
                    "moodle_user_id": username,
                    #"moodle_user_id": sub.get("fullname") or f"User {username}",
                    "error":  error_msg,
                    "status": "failed",
                    "source": "moodle"
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
        existing = db.query(models.User).filter(
            models.User.email == moodle_user.get("email", "")
        ).first()
        if not existing:
            synced.append(moodle_user.get("fullname"))

    db.commit()
    return {"success": True, "synced": synced}


# ── POST /api/teacher/moodle/create-assignment ───────────────────────────
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
            "assignsubmission_file_enabled":       1,
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