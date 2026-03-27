from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
import os

from routes.auth import auth_bp
from routes.submissions import submissions_bp
from routes.students import students_bp

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')

# Allow requests from React frontend
CORS(app, origins=["http://localhost:5173"])

# Register route blueprints
app.register_blueprint(auth_bp, url_prefix='/api/auth')
app.register_blueprint(submissions_bp, url_prefix='/api/submissions')
app.register_blueprint(students_bp, url_prefix='/api/students')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
```

---

**Then add this to your `.env` file:**
```
SECRET_KEY=your-secret-key-here
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=yourpassword
DB_NAME=essaygrade