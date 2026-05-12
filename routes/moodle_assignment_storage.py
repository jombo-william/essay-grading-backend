import json
import os
from typing import Optional, Dict

LINKS_FILE = "data/moodle_assignment_links.json"

def load_links() -> Dict:
    """Load assignment-moodle links from JSON file"""
    if not os.path.exists(LINKS_FILE):
        return {}
    try:
        with open(LINKS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_links(links: Dict):
    """Save assignment-moodle links to JSON file"""
    with open(LINKS_FILE, 'w') as f:
        json.dump(links, f, indent=2)

def save_moodle_link(assignment_id: int, moodle_course_id: int, moodle_assignment_id: int):
    """Save a Moodle link for an assignment"""
    links = load_links()
    links[str(assignment_id)] = {
        "moodle_course_id": moodle_course_id,
        "moodle_assignment_id": moodle_assignment_id,
        "synced_at": str(__import__('datetime').datetime.now())
    }
    save_links(links)

def get_moodle_link(assignment_id: int) -> Optional[Dict]:
    """Get Moodle link for an assignment"""
    links = load_links()
    return links.get(str(assignment_id))

def delete_moodle_link(assignment_id: int):
    """Delete Moodle link for an assignment"""
    links = load_links()
    if str(assignment_id) in links:
        del links[str(assignment_id)]
        save_links(links)
