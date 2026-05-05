from database import engine
from sqlalchemy import text

def migrate():
    with engine.connect() as conn:

        # Add Moodle fields to assignments table
        conn.execute(text(
            "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS moodle_assignment_id INTEGER"
        ))
        print("✅ Added moodle_assignment_id to assignments")

        conn.execute(text(
            "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS moodle_course_id INTEGER"
        ))
        print("✅ Added moodle_course_id to assignments")

        conn.execute(text(
            "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS moodle_site_url VARCHAR(255)"
        ))
        print("✅ Added moodle_site_url to assignments")

        # Create student moodle tokens table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS student_moodle_tokens (
                id         SERIAL PRIMARY KEY,
                student_id INTEGER REFERENCES users(id) ON DELETE CASCADE UNIQUE,
                token      TEXT NOT NULL,
                site_url   VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        print("✅ Created student_moodle_tokens table")

        conn.commit()
        print("🎉 Migration complete!")

if __name__ == "__main__":
    migrate()