# create_test_user_safe.py
from sqlalchemy.orm import Session
from database import SessionLocal, Base, engine
from models.user import User
import bcrypt

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

# Open a database session
db: Session = SessionLocal()

# Test user details
email = "test@example.com"
password_plain = "password123"

email = "teacher@example.com"
password_plain = "password123"

user = User(
    name="Test Teacher",
    email=email,
    password=hashed,
    role="teacher",   # 👈 IMPORTANT
    registration_number="TCH12345"
)

# Check if user already exists
existing_user = db.query(User).filter(User.email == email).first()

if existing_user:
    print(f"User {email} already exists.")
else:
    # Hash the password
    hashed = bcrypt.hashpw(password_plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # Create new user
    user = User(
        name="Test User",
        email=email,
        password=hashed,
        role="student",
        registration_number="STU12345"
    )

    db.add(user)
    db.commit()
    print(f"Test user {email} created successfully!")

# Close session
db.close()