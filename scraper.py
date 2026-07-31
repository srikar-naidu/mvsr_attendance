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


def _looks_logged_in(html: str) -> bool:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    markers = [
        "log out",
        "logout",
        "sign out",
        "my profile",
        "welcome",
    ]
    return any(marker in text for marker in markers)


def _looks_like_login_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    has_user = bool(soup.find("input", attrs={"name": re.compile(r"username", re.IGNORECASE)}))
    has_pass = bool(soup.find("input", attrs={"name": re.compile(r"pass|passwd|password", re.IGNORECASE)}))
    return has_user and has_pass


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

    resp = session.get(LOGIN_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    token_input = soup.find("input", attrs={"name": TOKEN_NAME_RE})
    if not token_input:
        raise LoginError("Could not find login form / CSRF token. Portal may be down or changed structure.")

    token_name = str(token_input["name"])
    token_value = str(token_input.get("value", "1"))

    payload = {
        "username": username,
        "passwd": password,
        "checkcaptcha": "0",
        "captcha": "",
        "SubmitL": "Sign in",
        "option": "com_user",
        "task": "login",
        "return": "aHR0cHM6Ly9tdnNyLndpbm5vdS5uZXQvaW5kZXgucGhw",
    }
    payload[token_name] = token_value

    resp = session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)
    resp.raise_for_status()

    if _looks_like_login_page(resp.text) and not _looks_logged_in(resp.text):
        raise LoginError("Login failed. Check your roll number / password.")

    attendance_probe = session.get(ATTENDANCE_URL, timeout=15, allow_redirects=True)
    attendance_probe.raise_for_status()
    if _looks_like_login_page(attendance_probe.text) and not _looks_logged_in(attendance_probe.text):
        raise LoginError("Login failed. Check your roll number / password.")

    return session


def _clean_number(value: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", value)
    if not cleaned:
        raise ValueError(f"Could not parse number from {value!r}")
    return float(cleaned)


def _find_attendance_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        header_text = " | ".join(headers)
        if any("subject" in h for h in headers) and any("attend" in h for h in headers):
            return table
        if "subject" in header_text and ("percentage" in header_text or "%" in header_text):
            return table
    return None


def get_attendance(session: requests.Session) -> list[dict]:
    """
    Fetches and parses the subject-wise attendance table for the
    currently logged-in student.
    """
    resp = session.get(ATTENDANCE_URL, timeout=15, allow_redirects=True)
    resp.raise_for_status()
    if _looks_like_login_page(resp.text) and not _looks_logged_in(resp.text):
        raise ScrapeError("Session expired or login did not succeed. Please log in again.")

    soup = BeautifulSoup(resp.text, "html.parser")

    table = _find_attendance_table(soup)
    if not table:
        raise ScrapeError("Could not find attendance table. Portal layout may have changed.")

    subjects = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if len(values) < 4:
            continue

        first_cell = values[0].strip().lower()
        row_text = " ".join(values).lower()
        if first_cell in {"s.no", "sno", "sl.no", "subject"}:
            continue
        if "total" in row_text and len(values) <= 7:
            continue

        subject_name = None
        number_values = []
        for value in values:
            compact = value.strip()
            if subject_name is None and compact and not compact.isdigit() and not re.fullmatch(r"[0-9.]+%?", compact):
                subject_name = compact
            try:
                number_values.append(_clean_number(compact))
            except ValueError:
                pass

        if not subject_name or len(number_values) < 3:
            continue

        held = None
        attended = None
        percentage = None

        for i in range(len(values) - 1):
            label = values[i].strip().lower()
            next_value = values[i + 1].strip()
            try:
                parsed = _clean_number(next_value)
            except ValueError:
                continue

            if held is None and any(key in label for key in ["held", "conducted", "total classes", "classes held"]):
                held = parsed
            elif attended is None and any(key in label for key in ["attended", "present"]):
                attended = parsed
            elif percentage is None and any(key in label for key in ["%", "percent", "percentage"]):
                percentage = parsed

        if held is None or attended is None:
            numeric_tail = number_values[-3:]
            if len(numeric_tail) >= 2:
                held = held if held is not None else numeric_tail[0]
                attended = attended if attended is not None else numeric_tail[1]
                if percentage is None and len(numeric_tail) >= 3:
                    percentage = numeric_tail[2]

        if held is None or attended is None:
            continue

        if percentage is None:
            percentage = round((attended / held) * 100, 2) if held else 0.0

        subjects.append({
            "subject": subject_name,
            "held": held,
            "attended": attended,
            "percentage": round(percentage, 2),
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
