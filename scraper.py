"""
Scraper for MVSR's onEdu (winnou) ERP portal.

This logs in using a student's own credentials (never stored),
and parses their attendance table. One instance of a logged-in
`requests.Session` = one student.
"""

import re
import math
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://mvsr.winnou.net"
LOGIN_URL = f"{BASE_URL}/index.php"
ATTENDANCE_URL = f"{BASE_URL}/index.php?option=com_base_attendancereport&Itemid=98"

# Joomla's CSRF token field is a random 32-char hex name with value "1".
# It changes per session, so we scrape it fresh every login instead of
# hardcoding it.
TOKEN_NAME_RE = re.compile(r"^[0-9a-f]{32}$")


class LoginError(Exception):
    pass


class ScrapeError(Exception):
    pass


def login(username: str, password: str) -> requests.Session:
    """
    Logs into the winnou portal with the given credentials.
    Returns an authenticated requests.Session on success.
    Raises LoginError if credentials are rejected.

    NOTE: the password is used exactly once here, in-memory, to build
    the POST request. It is never written to disk or logged anywhere.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; AttendanceBunkMeter/1.0)"
    })

    # Step 1: GET the login page to find the current CSRF token
    resp = session.get(LOGIN_URL, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    token_input = soup.find("input", attrs={"name": TOKEN_NAME_RE})
    if not token_input:
        raise LoginError("Could not find login form / CSRF token. Portal may be down or changed structure.")

    token_name = token_input["name"]
    token_value = token_input.get("value", "1")

    # Step 2: POST the login form, matching the exact fields winnou expects
    payload = {
        "username": username,
        "passwd": password,
        "checkcaptcha": "0",
        "captcha": "",
        "SubmitL": "Sign in",
        "option": "com_user",
        "task": "login",
        # base64 of "https://mvsr.winnou.net/index.php" - the post-login redirect target
        "return": "aHR0cHM6Ly9tdnNyLndpbm5vdS5uZXQvaW5kZXgucGhw",
        token_name: token_value,
    }

    resp = session.post(LOGIN_URL, data=payload, timeout=15)

    if "Log Out" not in resp.text and "Welcome" not in resp.text:
        raise LoginError("Login failed. Check your roll number / password.")

    return session


def get_attendance(session: requests.Session) -> list[dict]:
    """
    Fetches and parses the subject-wise attendance table for the
    currently logged-in student.
    """
    resp = session.get(ATTENDANCE_URL, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table", id="fd-table-1")
    if not table:
        raise ScrapeError("Could not find attendance table. Session may have expired - try logging in again.")

    subjects = []
    rows = table.find_all("tr")

    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 7:
            continue

        first_cell = cells[0].get_text(strip=True)
        # Skip the header row and the TOTAL row - both have non-numeric
        # or th-based first cells instead of a row number.
        if not first_cell.isdigit():
            continue

        subject_name = cells[1].get_text(strip=True)
        try:
            held = float(cells[2].get_text(strip=True))
            attended = float(cells[3].get_text(strip=True))
            percentage = float(cells[6].get_text(strip=True))
        except ValueError:
            continue

        subjects.append({
            "subject": subject_name,
            "held": held,
            "attended": attended,
            "percentage": percentage,
        })

    if not subjects:
        raise ScrapeError("Attendance table was found but no subject rows could be parsed.")

    return subjects


def bunk_meter(attended: float, held: float, target: float = 75.0) -> dict:
    """
    Given classes attended/held, returns how many more classes can be
    skipped (or must be attended) to hit the target percentage.
    """
    if held == 0:
        return {"status": "no_data", "current_pct": 0, "can_bunk": 0, "must_attend": 0}

    current_pct = (attended / held) * 100
    t = target / 100

    if current_pct >= target:
        # Solve for x: attended / (held + x) >= t  =>  x <= (attended - t*held) / t
        can_bunk = math.floor((attended - t * held) / t)
        return {
            "status": "safe",
            "current_pct": round(current_pct, 2),
            "can_bunk": max(can_bunk, 0),
        }
    else:
        # Solve for y: (attended + y) / (held + y) >= t  =>  y >= (t*held - attended) / (1 - t)
        must_attend = math.ceil((t * held - attended) / (1 - t))
        return {
            "status": "danger",
            "current_pct": round(current_pct, 2),
            "must_attend": must_attend,
        }


def get_attendance_with_bunk_meter(session: requests.Session, target: float = 75.0) -> dict:
    """
    Full pipeline: scrape attendance, attach a bunk-meter verdict to
    each subject, and compute an overall summary.
    """
    subjects = get_attendance(session)

    for s in subjects:
        s["bunk_meter"] = bunk_meter(s["attended"], s["held"], target)

    total_held = sum(s["held"] for s in subjects)
    total_attended = sum(s["attended"] for s in subjects)
    overall = bunk_meter(total_attended, total_held, target)
    overall["total_held"] = total_held
    overall["total_attended"] = total_attended

    return {
        "subjects": subjects,
        "overall": overall,
    }
