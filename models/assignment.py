from database.db import get_db

def get_all_assignments():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM assignments")
    assignments = cursor.fetchall()
    cursor.close()
    db.close()
    return assignments