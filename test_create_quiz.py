import requests
import json

url = "https://essaygrade.moodlecloud.com/webservice/rest/server.php"
token = "b31af5ab7968d76559983a1df6dbfcf1"

# First, get available courses
print("1. Getting courses...")
courses_response = requests.post(
    url,
    data={
        "wstoken": token,
        "wsfunction": "core_enrol_get_users_courses",
        "moodlewsrestformat": "json",
        "userid": "2"
    }
)
courses = courses_response.json()
print(f"Found {len(courses)} courses")
if courses:
    course_id = courses[0]['id']
    print(f"Using course ID: {course_id} - {courses[0]['fullname']}")
    
    # Try to create a quiz using mod_quiz_add_instance
    print("\n2. Creating quiz...")
    quiz_response = requests.post(
        url,
        data={
            "wstoken": token,
            "wsfunction": "mod_quiz_add_instance",
            "moodlewsrestformat": "json",
            "courseid": course_id,
            "name": "Test Quiz from API",
            "intro": "This is a test quiz created via API",
            "introformat": 1,
            "timelimit": 3600
        }
    )
    print(f"Quiz creation response: {quiz_response.json()}")
