

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
   #"https://www.googleapis.com/auth/classroom.grades",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

#CLIENT_SECRETS_FILE = "google_credentials.json"
CLIENT_SECRETS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),  # routes/
    "..",                                          # go up one level to backend root
    "google_credentials.json"
)
print(f"📄 Creds path: {os.path.abspath(CLIENT_SECRETS_FILE)}")
print(f"📄 Size: {os.path.getsize(CLIENT_SECRETS_FILE)} bytes")
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


# ── POST /api/teacher/classroom/courses/{course_id}/assignments/{cw_id}/grade ─
@router.post("/classroom/courses/{course_id}/assignments/{coursework_id}/grade")
def import_and_grade(
    course_id:           str,
    coursework_id:       str,
    local_assignment_id: int = Query(...),
    ctx: dict = Depends(require_teacher)
):
    from routes.ai_grader import grade_with_ai

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

    results = []

    # Track which local student_ids were graded via GC
    # so we don't re-grade them in the local pass
    gc_graded_student_ids = set()

    # ── Grade Google Classroom submissions ────────────────────────────────────
    for gs in student_subs:
        gc_uid      = gs.get("userId", "unknown")
        essay_text  = ""
        attachments = gs.get("assignmentSubmission", {}).get("attachments", [])

        # ── Resolve real student name ─────────────────────────────────────
        gc_token = db.query(models.StudentGoogleToken).filter_by(
            gc_user_id=gc_uid
        ).first()
        actual_student_id = gc_token.student_id if gc_token else None

        student_name = "Unknown"
        if actual_student_id:
            local_user = db.query(models.User).filter_by(id=actual_student_id).first()
            if local_user:
                student_name = local_user.name

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
                "student_name":      student_name,
                "google_student_id": gc_uid,
                "error":             "No text content found in submission",
                "status":            "skipped",
                "source":            "google_classroom",
            })
            continue

        try:
            word_count = len(essay_text.split())
            from routes.grading_prompt import build_grading_prompt
            prompt = build_grading_prompt(assignment, essay_text, word_count)
            grade  = grade_with_ai(
                prompt=prompt,
                assignment=assignment,
                essay_text=essay_text,
                word_count=word_count,
            )

            # ── Save to DB ────────────────────────────────────────────────
            existing_sub = db.query(models.Submission).filter(
                models.Submission.assignment_id == assignment.id,
                models.Submission.file_name     == f"gc_{gc_uid}",
            ).first()

            if existing_sub:
                existing_sub.essay_text         = essay_text[:5000]
                existing_sub.ai_score           = grade["score"]
                existing_sub.ai_feedback        = grade["feedback"]
                existing_sub.ai_detection_score = 0
                existing_sub.status             = "ai_graded"
                db.commit()
                # Mark this student as already graded
                if existing_sub.student_id:
                    gc_graded_student_ids.add(existing_sub.student_id)

            elif actual_student_id:
                gc_graded_student_ids.add(actual_student_id)  # ← mark before saving
                check = db.query(models.Submission).filter_by(
                    assignment_id = assignment.id,
                    student_id    = actual_student_id,
                ).first()
                if check:
                    check.essay_text         = essay_text[:5000]
                    check.ai_score           = grade["score"]
                    check.ai_feedback        = grade["feedback"]
                    check.ai_detection_score = 0
                    check.status             = "ai_graded"
                    check.file_name          = f"gc_{gc_uid}"
                else:
                    db.add(models.Submission(
                        assignment_id      = assignment.id,
                        student_id         = actual_student_id,
                        essay_text         = essay_text[:5000],
                        submit_mode        = "upload",
                        file_name          = f"gc_{gc_uid}",
                        ai_score           = grade["score"],
                        ai_feedback        = grade["feedback"],
                        ai_detection_score = 0,
                        status             = "ai_graded",
                    ))
                db.commit()
            else:
                print(f"⚠️ Could not find local student for GC user {gc_uid} — skipping DB save")

            # ── Push grade back to Google Classroom ───────────────────────
            try:
                gc_sub_list = classroom_svc.courses().courseWork().studentSubmissions().list(
                    courseId     = course_id,
                    courseWorkId = coursework_id,
                    userId       = gc_uid,
                ).execute()
                gc_subs = gc_sub_list.get("studentSubmissions", [])
                if gc_subs:
                    gc_sub_id = gc_subs[0]["id"]
                    classroom_svc.courses().courseWork().studentSubmissions().patch(
                        courseId     = course_id,
                        courseWorkId = coursework_id,
                        id           = gc_sub_id,
                        updateMask   = "assignedGrade,draftGrade",
                        body={
                            "assignedGrade": grade["score"],
                            "draftGrade":    grade["score"],
                        },
                    ).execute()
                    print(f"✅ Grade {grade['score']} posted to Google Classroom for {gc_uid}")
            except Exception as grade_err:
                print(f"⚠️ Could not post grade to Google Classroom: {grade_err}")

            results.append({
                "student_name":      student_name,
                "google_student_id": gc_uid,
                "score":             grade["score"],
                "feedback":          grade["feedback"],
                "status":            "graded",
                "source":            "google_classroom",
            })
            print(f"✅ Graded GC submission for {student_name} → {grade['score']}/{assignment.max_score}")

        except Exception as e:
            db.rollback()
            print(f"❌ Grading failed for {student_name}: {e}")
            results.append({
                "student_name":      student_name,
                "google_student_id": gc_uid,
                "error":             str(e),
                "status":            "failed",
                "source":            "google_classroom",
            })

    # ── Grade local submissions — SKIP anyone already graded via GC ──────────
    local_subs = db.query(models.Submission, models.User).join(
        models.User, models.User.id == models.Submission.student_id
    ).filter(
        models.Submission.assignment_id == assignment.id
    ).all()

    print(f"📥 Found {len(local_subs)} submissions in local DB")

    for sub, student_user in local_subs:

        # ── Skip if this student was already graded via Google Classroom ──
        if student_user.id in gc_graded_student_ids:
            print(f"⏭️ Skipping {student_user.name} — already graded via Google Classroom")
            continue

        essay_text = sub.essay_text
        if not essay_text or not essay_text.strip():
            continue

        try:
            word_count = len(essay_text.split())
            from routes.grading_prompt import build_grading_prompt
            prompt = build_grading_prompt(assignment, essay_text, word_count)
            grade  = grade_with_ai(
                prompt=prompt,
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
                "student_name":      student_user.name,
                "google_student_id": f"local_{student_user.id}",
                "score":             grade["score"],
                "feedback":          grade["feedback"],
                "status":            "graded",
                "source":            "local",
            })
            print(f"✅ Graded local submission for {student_user.name} → {grade['score']}/{assignment.max_score}")

        except Exception as e:
            db.rollback()
            print(f"❌ Local grading failed for {student_user.name}: {e}")
            results.append({
                "student_name":      student_user.name,
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

   # return {"success": True, "message": "Google Classroom course linked to class."}


# ── POST /api/teacher/classroom/courses/{course_id}/enroll-students ──────────
@router.post("/classroom/courses/{course_id}/enroll-students")
def enroll_gc_students(
    course_id:      str,
    local_class_id: int = Query(...),
    ctx: dict = Depends(require_teacher)
):
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    creds   = get_credentials(user.id, db)
    service = build("classroom", "v1", credentials=creds)

    result = service.courses().students().list(
        courseId=course_id
    ).execute()

    gc_students = result.get("students", [])
    print(f"👥 Found {len(gc_students)} students in GC course {course_id}")

    enrolled = 0
    created  = 0
    skipped  = 0

    for gc_student in gc_students:
        profile   = gc_student.get("profile", {})
        gc_uid    = gc_student.get("userId")
        email     = profile.get("emailAddress", "")
        full_name = profile.get("name", {}).get("fullName", f"Student {gc_uid}")

        if not email:
            print(f"⚠️ No email for GC user {gc_uid} — skipping")
            skipped += 1
            continue

        # Step 1 — find or create local user
        local_user = db.query(models.User).filter_by(email=email).first()

        if not local_user:
            import bcrypt
            random_pw = secrets.token_urlsafe(12)
            hashed    = bcrypt.hashpw(random_pw.encode(), bcrypt.gensalt()).decode()

            local_user = models.User(
                name     = full_name,
                email    = email,
                password = hashed,
                role     = "student",
            )
            db.add(local_user)
            db.flush()
            created += 1
            print(f"✅ Created local account for {full_name} ({email})")

        # Step 2 — link gc_user_id for grading matching
        gc_token = db.query(models.StudentGoogleToken).filter_by(
            student_id=local_user.id
        ).first()

        if not gc_token:
            db.add(models.StudentGoogleToken(
                student_id = local_user.id,
                gc_user_id = gc_uid,
            ))

        # Step 3 — enroll in local class if not already enrolled
        already_enrolled = db.query(models.ClassEnrollment).filter_by(
            class_id   = local_class_id,
            student_id = local_user.id,
        ).first()

        if not already_enrolled:
            db.add(models.ClassEnrollment(
                class_id   = local_class_id,
                student_id = local_user.id,
            ))
            enrolled += 1

    db.commit()

    print(f"✅ GC sync done: {created} created, {enrolled} enrolled, {skipped} skipped")

    return {
        "success":  True,
        "created":  created,
        "enrolled": enrolled,
        "skipped":  skipped,
        "message":  f"{created} accounts created, {enrolled} enrolled, {skipped} skipped",
    }


    # ── POST /api/teacher/classroom/courses/{course_id}/sync ─────────────────────
@router.post("/classroom/courses/{course_id}/sync")
def sync_gc_course(
    course_id:      str,
    local_class_id: int = Query(...),
    ctx: dict = Depends(require_teacher)
):
    """
    Sync a Google Classroom course with a local class:
    1. Enroll all GC students locally
    2. Import all GC assignments locally
    """
    user: models.User = ctx["user"]
    db:   Session     = ctx["db"]

    creds   = get_credentials(user.id, db)
    service = build("classroom", "v1", credentials=creds)

    # ── Sync students ─────────────────────────────────────────────────────────
    students_result = service.courses().students().list(
        courseId=course_id
    ).execute()

    gc_students = students_result.get("students", [])
    students_created  = 0
    students_enrolled = 0

    for gc_student in gc_students:
        profile   = gc_student.get("profile", {})
        gc_uid    = gc_student.get("userId")
        email     = profile.get("emailAddress", "")
        full_name = profile.get("name", {}).get("fullName", f"Student {gc_uid}")

        if not email:
            continue

        local_user = db.query(models.User).filter_by(email=email).first()
        if not local_user:
            import bcrypt
            hashed = bcrypt.hashpw(
                secrets.token_urlsafe(12).encode(), bcrypt.gensalt()
            ).decode()
            local_user = models.User(
                name=full_name, email=email,
                password=hashed, role="student",
            )
            db.add(local_user)
            db.flush()
            students_created += 1

        # Link gc_user_id
        gc_token = db.query(models.StudentGoogleToken).filter_by(
            student_id=local_user.id
        ).first()
        if not gc_token:
            db.add(models.StudentGoogleToken(
                student_id=local_user.id,
                gc_user_id=gc_uid,
            ))

        # Enroll in local class
        already = db.query(models.ClassEnrollment).filter_by(
            class_id=local_class_id, student_id=local_user.id
        ).first()
        if not already:
            db.add(models.ClassEnrollment(
                class_id=local_class_id, student_id=local_user.id
            ))
            students_enrolled += 1

    # ── Sync assignments ──────────────────────────────────────────────────────
    work_result = service.courses().courseWork().list(
        courseId=course_id
    ).execute()

    gc_assignments     = work_result.get("courseWork", [])
    assignments_synced = 0

    for gca in gc_assignments:
        gc_cw_id = gca.get("id")

        # Skip if already imported
        exists = db.query(models.Assignment).filter_by(
            gc_coursework_id=gc_cw_id
        ).first()
        if exists:
            continue

        due_date = None
        if gca.get("dueDate"):
            d = gca["dueDate"]
            t = gca.get("dueTime", {})
            from datetime import datetime
            due_date = datetime(
                d.get("year", 2025), d.get("month", 1), d.get("day", 1),
                t.get("hours", 23), t.get("minutes", 59), 0,
            )

        new_assignment = models.Assignment(
            teacher_id       = user.id,
            class_id         = local_class_id,
            title            = gca.get("title", "Untitled"),
            description      = gca.get("description", ""),
            instructions     = gca.get("description", "Imported from Google Classroom"),
            max_score        = int(gca.get("maxPoints", 100)),
            due_date         = due_date,
            gc_coursework_id = gc_cw_id,
            is_active        = True,
        )
        db.add(new_assignment)
        assignments_synced += 1

    db.commit()

    print(f"✅ Sync done: {students_created} students created, {students_enrolled} enrolled, {assignments_synced} assignments imported")

    return {
        "success":            True,
        "students_created":   students_created,
        "students_enrolled":  students_enrolled,
        "assignments_synced": assignments_synced,
        "message":            f"Sync complete — {students_enrolled} students enrolled, {assignments_synced} assignments imported",
    }