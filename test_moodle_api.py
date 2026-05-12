import requests
import json

url = "https://essaygrade.moodlecloud.com/webservice/rest/server.php"
token = "b31af5ab7968d76559983a1df6dbfcf1"

# Test what functions are available
response = requests.post(
    url,
    data={
        "wstoken": token,
        "wsfunction": "core_webservice_get_site_functions",
        "moodlewsrestformat": "json"
    }
)

print(f"Status: {response.status_code}")
data = response.json()

print(f"Response type: {type(data)}")
print(f"Response: {json.dumps(data, indent=2)[:1000]}")

if isinstance(data, list):
    print(f"\nAvailable functions ({len(data)} total):")
    for f in data[:20]:
        print(f"  - {f.get('name')}")
    
    # Check for quiz-related functions
    quiz_functions = [f.get('name') for f in data if 'quiz' in f.get('name', '').lower() or 'question' in f.get('name', '').lower()]
    print(f"\nQuiz/Question related functions ({len(quiz_functions)}):")
    for f in quiz_functions:
        print(f"  - {f}")
else:
    print(f"Error or unexpected response format: {data}")
