from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room
from dotenv import load_dotenv
import os

from routes.auth import auth_bp
from routes.submissions import submissions_bp
from routes.students import students_bp
from database import engine, Base, SessionLocal
from models import SubmissionMessage

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')

# Allow requests from React frontend
CORS(app, origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"], supports_credentials=True)

socketio = SocketIO(app, cors_allowed_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"], async_mode='threading')

# Create database tables for any new models
Base.metadata.create_all(bind=engine)

# Register route blueprints
app.register_blueprint(auth_bp, url_prefix='/api/auth')
app.register_blueprint(submissions_bp, url_prefix='/api/submissions')
app.register_blueprint(students_bp, url_prefix='/api/students')

@app.route('/api/chat/history/<int:submission_id>', methods=['GET'])
def chat_history(submission_id):
    with SessionLocal() as db:
        messages = (
            db.query(SubmissionMessage)
            .filter(SubmissionMessage.submission_id == submission_id)
            .order_by(SubmissionMessage.created_at.asc())
            .all()
        )
        return jsonify({
            'success': True,
            'messages': [
                {
                    'id': message.id,
                    'submission_id': message.submission_id,
                    'sender_id': message.sender_id,
                    'sender_role': message.sender_role,
                    'sender_name': message.sender_name,
                    'message': message.message,
                    'created_at': message.created_at.isoformat() if message.created_at else None,
                }
                for message in messages
            ],
        })

@socketio.on('join_submission')
def handle_join_submission(data):
    submission_id = data.get('submission_id')
    if submission_id is None:
        return
    room = f'submission_{submission_id}'
    join_room(room)
    socketio.emit('joined', {'submission_id': submission_id}, room=request.sid)

@socketio.on('leave_submission')
def handle_leave_submission(data):
    submission_id = data.get('submission_id')
    if submission_id is None:
        return
    leave_room(f'submission_{submission_id}')

@socketio.on('send_message')
def handle_send_message(data):
    submission_id = data.get('submission_id')
    sender_id = data.get('sender_id')
    sender_role = data.get('sender_role')
    sender_name = data.get('sender_name')
    message_text = data.get('message', '').strip()

    if not submission_id or not message_text:
        return

    with SessionLocal() as db:
        message = SubmissionMessage(
            submission_id=submission_id,
            sender_id=sender_id,
            sender_role=sender_role or 'student',
            sender_name=sender_name or 'Unknown',
            message=message_text,
        )
        db.add(message)
        db.commit()
        db.refresh(message)

    payload = {
        'id': message.id,
        'submission_id': submission_id,
        'sender_id': sender_id,
        'sender_role': sender_role or 'student',
        'sender_name': sender_name or 'Unknown',
        'message': message_text,
        'created_at': message.created_at.isoformat() if message.created_at else None,
    }
    socketio.emit('new_message', payload, room=f'submission_{submission_id}')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 8000)))
