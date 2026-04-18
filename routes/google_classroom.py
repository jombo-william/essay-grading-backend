

"""
routes/google_classroom.py
Google Classroom Integration
"""
import json
import os
import secrets
import hashlib
import base64
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from auth_utils import require_teacher
from database import get_db
import models
import os
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
_code_verifiers: dict = {}

router = APIRouter()

# ── Check if google packages are installed ────────────────────────────────────
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    GOOGLE_AVAILABLE = True
    print("✅ Google packages available")
except ImportError:
    GOOGLE_AVAILABLE = False
    print("❌ Google packages NOT installed — run: pip install google-auth google-auth-oauthlib google-api-python-client")

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CLIENT_SECRETS_FILE = "google_credentials.json"
REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8000/api/teacher/auth/google/callback"
)


# ── Helper: load saved credentials for a teacher ─────────────────────────────
def get_credentials(teacher_id: int, db: Session):
    token_row = db.query(models.GoogleClassroomToken).filter_by(
        teacher_id=teacher_id
    ).first()

    if not token_row:
        raise HTTPException(
            status_code=401,
            detail="Google Classroom not connected. Please click 'Connect Google Classroom' first."
        )

    return Credentials(
        token         = token_row.access_token,
        refresh_token = token_row.refresh_token,
        token_uri     = token_row.token_uri,
        client_id     = token_row.client_id,
        client_secret = token_row.client_secret,
        scopes        = json.loads(token_row.scopes) if token_row.scopes else SCOPES,
    )

def get_gc_course_id_for_class(class_id: int, db: Session):
    """Get the linked Google Classroom course ID for a local class."""
    cls = db.query(models.Class).filter_by(id=class_id).first()
    return cls.gc_course_id if cls else None


def create_gc_assignment(teacher_id: int, class_id: int, assignment, db: Session):
    """Create an assignment in Google Classroom and return the coursework ID."""
    gc_course_id = get_gc_course_id_for_class(class_id, db)
    if not gc_course_id:
        return None  # class not linked, skip silently

    try:
        creds   = get_credentials(teacher_id, db)
        service = build("classroom", "v1", credentials=creds)

        due = assignment.due_date
        coursework_body = {
            "title":       assignment.title,
            "description": assignment.description or assignment.instructions or "",
            "workType":    "ASSIGNMENT",
            "state":       "PUBLISHED",
            "maxPoints":   assignment.max_score,
            "dueDate": {
                "year":  due.year,
                "month": due.month,
                "day":   due.day,
            },
            "dueTime": {
                "hours":   due.hour,
                "minutes": due.minute,
                "seconds": 0,
                "nanos":   0,
            },
        }

        result = service.courses().courseWork().create(
            courseId=gc_course_id,
            body=coursework_body
        ).execute()

        return result.get("id")  # gc_coursework_id

    except Exception as e:
        print(f"⚠️ Could not create Google Classroom assignment: {e}")
        return None

# ── GET /api/teacher/auth/google/classroom ────────────────────────────────────
@router.get("/auth/google/classroom")
def start_google_auth(ctx: dict = Depends(require_teacher)):
    if not GOOGLE_AVAILABLE:
        raise HTTPException(status_code=500, detail="Google packages not installed.")

    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise HTTPException(status_code=500, detail="google_credentials.json not found.")

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    teacher_id = str(ctx["user"].id)

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=teacher_id,
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )

    _code_verifiers[teacher_id] = code_verifier

    print(f"🔗 Google auth URL generated for teacher {teacher_id}")
    return {"auth_url": auth_url, "state": state}


# ── GET /api/teacher/auth/google/callback ─────────────────────────────────────
@router.get("/auth/google/callback")
def google_callback(
    code: str,
    state: str,
    db: Session = Depends(get_db)
):
    teacher_id = int(state)

    code_verifier = _code_verifiers.pop(str(teacher_id), None)
    if not code_verifier:
        raise HTTPException(status_code=400, detail="OAuth session expired or invalid. Please try connecting again.")

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    flow.fetch_token(code=code, code_verifier=code_verifier)
    creds = flow.credentials

    existing = db.query(models.GoogleClassroomToken).filter_by(
        teacher_id=teacher_id
    ).first()

    token_data = dict(
        access_token  = creds.token,
        refresh_token = creds.refresh_token,
        token_uri     = creds.token_uri,
        client_id     = creds.client_id,
        client_secret = creds.client_secret,
        scopes        = json.dumps(list(creds.scopes or SCOPES)),
    )

    if existing:
        for k, v in token_data.items():
            setattr(existing, k, v)
    else:
        db.add(models.GoogleClassroomToken(teacher_id=teacher_id, **token_data))

    db.commit()
    print(f"✅ Google tokens saved for teacher {teacher_id}")

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return RedirectResponse(url=f"{frontend_url}?google_connected=true")


# ── GET /api/teacher/classroom/courses ───────────────────────────────────────
@router.get("/classroom/courses")
def get_courses(ctx: dict = Depends(require_teacher)):
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    creds   = get_credentials(user.id, db)
    service = build("classroom", "v1", credentials=creds)

    result = service.courses().list(
        teacherId    = "me",
        courseStates = ["ACTIVE"]
    ).execute()

    raw_courses = result.get("courses", [])
    print(f"📚 Found {len(raw_courses)} Google Classroom courses for teacher {user.id}")

    return {
        "success": True,
        "courses": [
            {
                "id":      c.get("id"),
                "name":    c.get("name"),
                "section": c.get("section", ""),
                "subject": c.get("descriptionHeading", ""),
            }
            for c in raw_courses
        ]
    }


# ── GET /api/teacher/classroom/courses/{course_id}/assignments ───────────────
@router.get("/classroom/courses/{course_id}/assignments")
def get_course_assignments(
    course_id: str,
    ctx: dict = Depends(require_teacher)
):
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    creds   = get_credentials(user.id, db)
    service = build("classroom", "v1", credentials=creds)

    result = service.courses().courseWork().list(
        courseId=course_id
    ).execute()

    work = result.get("courseWork", [])

    return {
        "success":     True,
        "assignments": [
            {
                "id":          a.get("id"),
                "title":       a.get("title"),
                "description": a.get("description", ""),
                "maxPoints":   a.get("maxPoints", 100),
            }
            for a in work
        ]
    }


# # ── POST /api/teacher/classroom/courses/{course_id}/assignments/{cw_id}/grade ─
# @router.post("/classroom/courses/{course_id}/assignments/{coursework_id}/grade")
# def import_and_grade(
#     course_id:           str,
#     coursework_id:       str,
#     local_assignment_id: int = Query(...),
#     ctx: dict = Depends(require_teacher)
# ):
#     """
#     Fetch all TURNED_IN student submissions from Google Classroom,
#     grade each one with the local AI model,
#     and save results to the database.
#     """
#     from routes.ai_grader import grade_with_local_model

#     user: models.User = ctx["user"]
#     db:   Session     = ctx["db"]

#     assignment = db.query(models.Assignment).filter(
#         models.Assignment.id         == local_assignment_id,
#         models.Assignment.teacher_id == user.id,
#     ).first()

#     if not assignment:
#         raise HTTPException(status_code=404, detail="Local assignment not found")

#     creds         = get_credentials(user.id, db)
#     classroom_svc = build("classroom", "v1", credentials=creds)
#     drive_svc     = build("drive",     "v3", credentials=creds)

#     subs_result = classroom_svc.courses().courseWork().studentSubmissions().list(
#         courseId     = course_id,
#         courseWorkId = coursework_id,
#         states       = ["TURNED_IN"]
#     ).execute()

#     student_subs = subs_result.get("studentSubmissions", [])
#     print(f"📥 Found {len(student_subs)} submissions in Google Classroom")

#     if not student_subs:
#         return {
#             "success":      True,
#             "message":      "No submitted essays found in Google Classroom",
#             "total_graded": 0,
#             "results":      []
#         }

#     results = []

#     for gs in student_subs:
#         essay_text  = ""
#         attachments = gs.get("assignmentSubmission", {}).get("attachments", [])

#         for att in attachments:
#             if "driveFile" in att:
#                 file_id = att["driveFile"]["id"]
#                 try:
#                     # Check MIME type first
#                     file_meta = drive_svc.files().get(
#                         fileId = file_id,
#                         fields = "mimeType, name"
#                     ).execute()
#                     mime = file_meta.get("mimeType", "")

#                     if mime == "application/vnd.google-apps.document":
#                         # Google Doc → export as plain text
#                         content = drive_svc.files().export(
#                             fileId   = file_id,
#                             mimeType = "text/plain"
#                         ).execute()
#                         essay_text += content.decode("utf-8", errors="ignore")

#                     elif mime == "application/pdf":
#                         # PDF → download then extract text
#                         content = drive_svc.files().get_media(fileId=file_id).execute()
#                         try:
#                             import io
#                             import pypdf
#                             reader = pypdf.PdfReader(io.BytesIO(content))
#                             for page in reader.pages:
#                                 essay_text += page.extract_text() or ""
#                         except Exception:
#                             essay_text += content.decode("utf-8", errors="ignore")

#                     elif "text" in mime:
#                         # Plain text file
#                         content = drive_svc.files().get_media(fileId=file_id).execute()
#                         essay_text += content.decode("utf-8", errors="ignore")

#                     else:
#                         # Any other file type — try downloading raw
#                         content = drive_svc.files().get_media(fileId=file_id).execute()
#                         essay_text += content.decode("utf-8", errors="ignore")

#                     print(f"✅ Read file {file_id} (type: {mime})")

#                 except Exception as e:
#                     print(f"⚠️ Could not read Drive file {file_id}: {e}")

#         if not essay_text.strip():
#             results.append({
#                 "google_student_id": gs.get("userId"),
#                 "error":  "No text content found in submission",
#                 "status": "skipped"
#             })
#             continue

#         try:
#             word_count = len(essay_text.split())
#             grade = grade_with_local_model(
#                 assignment = assignment,
#                 essay_text = essay_text,
#                 word_count = word_count,
#             )

#             # new_sub = models.Submission(
#             #     assignment_id      = assignment.id,
#             #     student_id         = user.id,
#             #     essay_text         = essay_text[:5000],
#             #     submit_mode        = "upload",
#             #     file_name          = f"gc_{gs.get('userId', 'unknown')}",
#             #     ai_score           = grade["score"],
#             #     ai_feedback        = grade["feedback"],
#             #     ai_detection_score = 0,
#             #     status             = "ai_graded",
#             # )
#             # db.add(new_sub)
#             # db.commit()

#              # Check if submission already exists for this student+assignment
#             existing_sub = db.query(models.Submission).filter(
#                 models.Submission.assignment_id == assignment.id,
#                 models.Submission.student_id    == user.id,
#             ).first()

#             if existing_sub:
#                 # Update existing submission
#                 existing_sub.essay_text         = essay_text[:5000]
#                 existing_sub.ai_score           = grade["score"]
#                 existing_sub.ai_feedback        = grade["feedback"]
#                 existing_sub.ai_detection_score = 0
#                 existing_sub.status             = "ai_graded"
#                 existing_sub.file_name          = f"gc_{gs.get('userId', 'unknown')}"
#             else:
#                 # Create new submission
#                 existing_sub = models.Submission(
#                     assignment_id      = assignment.id,
#                     student_id         = user.id,
#                     essay_text         = essay_text[:5000],
#                     submit_mode        = "upload",
#                     file_name          = f"gc_{gs.get('userId', 'unknown')}",
#                     ai_score           = grade["score"],
#                     ai_feedback        = grade["feedback"],
#                     ai_detection_score = 0,
#                     status             = "ai_graded",
#                 )
#                 db.add(existing_sub)

#             db.commit()   



#             results.append({
#                 "google_student_id": gs.get("userId"),
#                 "score":             grade["score"],
#                 "feedback":          grade["feedback"],
#                 "status":            "graded"
#             })
#             print(f"✅ Graded submission for Google user {gs.get('userId')} → {grade['score']}/{assignment.max_score}")

#         except Exception as e:
#             print(f"❌ Grading failed for {gs.get('userId')}: {e}")
#             results.append({
#                 "google_student_id": gs.get("userId"),
#                 "error":             str(e),
#                 "status":            "failed"
#             })

#     return {
#         "success":      True,
#         "total_graded": len([r for r in results if r["status"] == "graded"]),
#         "results":      results
#     }


# # ── GET /api/teacher/classroom/status ────────────────────────────────────────
# @router.get("/classroom/status")
# def check_connection_status(ctx: dict = Depends(require_teacher)):
#     user: models.User = ctx["user"]
#     db:   Session     = ctx["db"]

#     token_row = db.query(models.GoogleClassroomToken).filter_by(
#         teacher_id=user.id
#     ).first()

#     return {
#         "connected": token_row is not None,
#         "message":   "Google Classroom connected" if token_row else "Not connected"
#     }

#     # ── POST /api/teacher/classes/{class_id}/link-google ─────────────────────────

# class LinkGoogleRequest(BaseModel):
#     gc_course_id: str

# @router.post("/classes/{class_id}/link-google")
# def link_google_course(
#     class_id: int,
#     body: LinkGoogleRequest,
#     ctx: dict = Depends(require_teacher),
# ):
#     user: models.User = ctx["user"]
#     db:   Session     = ctx["db"]

#     cls = db.query(models.Class).filter_by(id=class_id).first()
#     if not cls:
#         raise HTTPException(status_code=404, detail="Class not found.")

#     cls.gc_course_id = body.gc_course_id
#     db.commit()

#     return {"success": True, "message": "Google Classroom course linked to class."}

# ── POST /api/teacher/classroom/courses/{course_id}/assignments/{cw_id}/grade ─
@router.post("/classroom/courses/{course_id}/assignments/{coursework_id}/grade")
def import_and_grade(
    course_id:           str,
    coursework_id:       str,
    local_assignment_id: int = Query(...),
    ctx: dict = Depends(require_teacher)
):
    from routes.ai_grader import grade_with_local_model

    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    assignment = db.query(models.Assignment).filter(
        models.Assignment.id         == local_assignment_id,
        models.Assignment.teacher_id == user.id,
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Local assignment not found")

    creds         = get_credentials(user.id, db)
    classroom_svc = build("classroom", "v1", credentials=creds)
    drive_svc     = build("drive",     "v3", credentials=creds)

    subs_result = classroom_svc.courses().courseWork().studentSubmissions().list(
        courseId     = course_id,
        courseWorkId = coursework_id,
        states       = ["TURNED_IN"]
    ).execute()

    student_subs = subs_result.get("studentSubmissions", [])
    print(f"📥 Found {len(student_subs)} submissions in Google Classroom")

    results      = []
    gc_user_ids  = set()

    # ── Grade Google Classroom submissions ────────────────────────────────────
    for gs in student_subs:
        gc_user_ids.add(gs.get("userId"))
        essay_text  = ""
        attachments = gs.get("assignmentSubmission", {}).get("attachments", [])

        for att in attachments:
            if "driveFile" in att:
                file_id = att["driveFile"]["id"]
                try:
                    file_meta = drive_svc.files().get(
                        fileId = file_id,
                        fields = "mimeType, name"
                    ).execute()
                    mime = file_meta.get("mimeType", "")

                    if mime == "application/vnd.google-apps.document":
                        content = drive_svc.files().export(
                            fileId=file_id, mimeType="text/plain"
                        ).execute()
                        essay_text += content.decode("utf-8", errors="ignore")
                    elif mime == "application/pdf":
                        content = drive_svc.files().get_media(fileId=file_id).execute()
                        try:
                            import io, pypdf
                            reader = pypdf.PdfReader(io.BytesIO(content))
                            for page in reader.pages:
                                essay_text += page.extract_text() or ""
                        except Exception:
                            essay_text += content.decode("utf-8", errors="ignore")
                    elif "text" in mime:
                        content = drive_svc.files().get_media(fileId=file_id).execute()
                        essay_text += content.decode("utf-8", errors="ignore")
                    else:
                        content = drive_svc.files().get_media(fileId=file_id).execute()
                        essay_text += content.decode("utf-8", errors="ignore")

                    print(f"✅ Read file {file_id} (type: {mime})")
                except Exception as e:
                    print(f"⚠️ Could not read Drive file {file_id}: {e}")

        if not essay_text.strip():
            results.append({
                "google_student_id": gs.get("userId"),
                "error":             "No text content found in submission",
                "status":            "skipped",
                "source":            "google_classroom",
            })
            continue

        try:
            word_count = len(essay_text.split())
            grade = grade_with_local_model(
                assignment=assignment,
                essay_text=essay_text,
                word_count=word_count,
            )

            existing_sub = db.query(models.Submission).filter(
                models.Submission.assignment_id == assignment.id,
                models.Submission.file_name     == f"gc_{gs.get('userId', 'unknown')}",
            ).first()

            if existing_sub:
                existing_sub.essay_text         = essay_text[:5000]
                existing_sub.ai_score           = grade["score"]
                existing_sub.ai_feedback        = grade["feedback"]
                existing_sub.ai_detection_score = 0
                existing_sub.status             = "ai_graded"
            else:
                db.add(models.Submission(
                    assignment_id      = assignment.id,
                    student_id         = user.id,
                    essay_text         = essay_text[:5000],
                    submit_mode        = "upload",
                    file_name          = f"gc_{gs.get('userId', 'unknown')}",
                    ai_score           = grade["score"],
                    ai_feedback        = grade["feedback"],
                    ai_detection_score = 0,
                    status             = "ai_graded",
                ))
            db.commit()

            results.append({
                "google_student_id": gs.get("userId"),
                "score":             grade["score"],
                "feedback":          grade["feedback"],
                "status":            "graded",
                "source":            "google_classroom",
            })
            print(f"✅ Graded GC submission for {gs.get('userId')} → {grade['score']}/{assignment.max_score}")

        except Exception as e:
            print(f"❌ Grading failed for {gs.get('userId')}: {e}")
            results.append({
                "google_student_id": gs.get("userId"),
                "error":             str(e),
                "status":            "failed",
                "source":            "google_classroom",
            })

    # ── Grade local submissions ───────────────────────────────────────────────
    local_subs = db.query(models.Submission, models.User).join(
        models.User, models.User.id == models.Submission.student_id
    ).filter(
        models.Submission.assignment_id == assignment.id
    ).all()

    print(f"📥 Found {len(local_subs)} submissions in local DB")

    for sub, student_user in local_subs:
        essay_text = sub.essay_text
        if not essay_text or not essay_text.strip():
            continue

        try:
            word_count = len(essay_text.split())
            grade = grade_with_local_model(
                assignment=assignment,
                essay_text=essay_text,
                word_count=word_count,
            )

            sub.ai_score           = grade["score"]
            sub.ai_feedback        = grade["feedback"]
            sub.ai_detection_score = 0
            sub.status             = "ai_graded"
            db.commit()

            results.append({
                "google_student_id": f"local_{student_user.id}",
                "student_name":      student_user.name,
                "score":             grade["score"],
                "feedback":          grade["feedback"],
                "status":            "graded",
                "source":            "local",
            })
            print(f"✅ Graded local submission for {student_user.name} → {grade['score']}/{assignment.max_score}")

        except Exception as e:
            print(f"❌ Local grading failed for {student_user.name}: {e}")
            results.append({
                "google_student_id": f"local_{student_user.id}",
                "error":             str(e),
                "status":            "failed",
                "source":            "local",
            })

    return {
        "success":      True,
        "total_graded": len([r for r in results if r["status"] == "graded"]),
        "results":      results,
    }


# ── GET /api/teacher/classroom/status ────────────────────────────────────────
@router.get("/classroom/status")
def check_connection_status(ctx: dict = Depends(require_teacher)):
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    token_row = db.query(models.GoogleClassroomToken).filter_by(
        teacher_id=user.id
    ).first()

    return {
        "connected": token_row is not None,
        "message":   "Google Classroom connected" if token_row else "Not connected"
    }


# ── POST /api/teacher/classes/{class_id}/link-google ─────────────────────────

class LinkGoogleRequest(BaseModel):
    gc_course_id: str


@router.post("/classes/{class_id}/link-google")
def link_google_course(
    class_id: int,
    body: LinkGoogleRequest,
    ctx: dict = Depends(require_teacher),
):
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    cls = db.query(models.Class).filter_by(id=class_id).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    cls.gc_course_id = body.gc_course_id
    db.commit()

    return {"success": True, "message": "Google Classroom course linked to class."}