# # import os
# # from sqlalchemy import create_engine
# # from sqlalchemy.orm import sessionmaker, declarative_base
# # from dotenv import load_dotenv
# # # C:\Users\BR\Desktop\final_p\backend-jombo-essaygrade\database.py
# # load_dotenv()

# # DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
# # DB_PORT     = os.getenv("DB_PORT", "3306")
# # DB_USER     = os.getenv("DB_USER", "root")
# # DB_PASSWORD = os.getenv("DB_PASSWORD", "")
# # DB_NAME     = os.getenv("DB_NAME", "essay_grading")

# # DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# # engine       = create_engine(DATABASE_URL)
# # SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# # Base         = declarative_base()

# # def get_db():
# #     db = SessionLocal()
# #     try:
# #         yield db
# #     finally:
# #         db.close() 


# # #local machine ends here 


# import os
# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker, declarative_base
# from dotenv import load_dotenv

# # C:\Users\comadmin\Desktop\jombo\essayf-and-backend\backend\backend-jombo-essaygrade\database.py
# load_dotenv()

# # Reads DATABASE_URL from your .env file
# DATABASE_URL = os.getenv("DATABASE_URL")

# engine = create_engine(
#     DATABASE_URL,
#     pool_pre_ping=True,
#     pool_size=5,
#     max_overflow=10
# )

# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Base = declarative_base()

# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()




# import os
# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker, declarative_base
# from dotenv import load_dotenv

# load_dotenv()

# DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://postgres.yyrqliklmlwvkkjhjfge:WJomBo.W%2F%40Tw2111@aws-1-eu-west-1.pooler.supabase.com:6543/postgres"

# engine = create_engine(
#     DATABASE_URL,
#     pool_pre_ping=True,
#     pool_size=5,
#     max_overflow=10
# )

# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Base = declarative_base()

# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()



#clean file 


#clean file 

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = os.getenv("DB_PORT", "3306")
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME", "essay_grading")

DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

print(f"🗄️  Connecting to: {DATABASE_URL[:40]}...")

is_postgres = DATABASE_URL.startswith("postgresql")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=5,
    pool_recycle=60,
    pool_timeout=30,
    connect_args={
        "sslmode": "require",
        "connect_timeout": 15,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 5,
        "keepalives_count": 3,
    } if is_postgres else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()