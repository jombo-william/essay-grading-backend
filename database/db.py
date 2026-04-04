import mysql.connector
from dotenv import load_dotenv
import os

load_dotenv()

def get_db():
    connection = mysql.connector.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', 'essaygrade')
    )
    return connection