from database.db import get_db

def get_all_submissions():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM submissions ORDER BY submitted_at DESC")
    submissions = cursor.fetchall()
    cursor.close()
    db.close()
    return submissions

def get_pending_submissions():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM submissions 
        WHERE status = 'ai_graded' AND final_score IS NULL 
        ORDER BY submitted_at DESC
    """)
    submissions = cursor.fetchall()
    cursor.close()
    db.close()
    return submissions

def save_grade(submission_id, final_score, teacher_feedback):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        UPDATE submissions 
        SET final_score = %s, teacher_feedback = %s, status = 'graded'
        WHERE id = %s
    """, (final_score, teacher_feedback, submission_id))
    db.commit()
    cursor.close()
    db.close()

def save_feedback(submission_id, teacher_feedback):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        UPDATE submissions 
        SET teacher_feedback = %s 
        WHERE id = %s
    """, (teacher_feedback, submission_id))
    db.commit()
    cursor.close()
    db.close()