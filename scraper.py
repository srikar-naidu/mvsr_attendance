"""
Scraper for MVSR's onEdu (winnou) ERP portal.

This logs in using a student's own credentials (never stored), and
parses their full attendance data: student info, subject-wise summary
(with absent dates), and a day-by-day period-level history.

One instance of a logged-in `requests.Session` = one student.

Login is a two-step flow because the portal randomly requires a CAPTCHA
(the hidden `checkcaptcha` field on the login page is "0" or "1" per
page load):

    pending = start_login()                 # GET login page
    if pending["captcha_required"]: ...     # show pending["captcha_bytes"] to the user
    finish_login(pending, username, password, captcha_text)  # POST login
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

USER_AGENT = "Mozilla/5.0 (compatible; AttendanceBunkMeter/1.0)"


class LoginError(Exception):
    pass


class ScrapeError(Exception):
    pass


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _is_login_form(html: str) -> bool:
    """True if this page is (still) showing the login form, i.e. not logged in."""
    return bool(BeautifulSoup(html, "html.parser").find("input", id="username"))


def start_login() -> dict:
    """
    Step 1 of login: fetches the login page and, if the portal is
    currently demanding a CAPTCHA for this session, downloads the
    CAPTCHA image too.

    Returns a dict to be held server-side and passed into finish_login().
    """
    session = _new_session()
    resp = session.get(LOGIN_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    token_input = soup.find("input", attrs={"name": TOKEN_NAME_RE})
    if not token_input:
        raise LoginError("Could not find login form. Portal may be down or changed structure.")

    token_name = str(token_input["name"])
    token_value = str(token_input.get("value", "1"))

    checkcaptcha_input = soup.find("input", id="checkcaptcha")
    checkcaptcha = str(checkcaptcha_input.get("value", "0")) if checkcaptcha_input else "0"
    captcha_required = checkcaptcha == "1"

    captcha_bytes = None
    captcha_content_type = None
    if captcha_required:
        captcha_img = soup.find("img", id="captchaimg")
        if not captcha_img or not captcha_img.get("src"):
            raise LoginError("Portal is requesting a captcha but none was found. Try again.")
        img_resp = session.get(str(captcha_img["src"]), timeout=15)
        img_resp.raise_for_status()
        captcha_bytes = img_resp.content
        captcha_content_type = img_resp.headers.get("Content-Type", "image/jpeg")

    return {
        "session": session,
        "token_name": token_name,
        "token_value": token_value,
        "checkcaptcha": checkcaptcha,
        "captcha_required": captcha_required,
        "captcha_bytes": captcha_bytes,
        "captcha_content_type": captcha_content_type,
    }


def finish_login(pending: dict, username: str, password: str, captcha_text: str = "") -> requests.Session:
    """
    Step 2 of login: submits the login form using the session/token
    captured by start_login(). Raises LoginError on bad credentials or
    a wrong/missing captcha. Returns the now-authenticated session.
    """
    session: requests.Session = pending["session"]

    if pending["captcha_required"] and not captcha_text.strip():
        raise LoginError("Captcha is required.")

    payload = {
        "username": username,
        "passwd": password,
        "checkcaptcha": pending["checkcaptcha"],
        "captcha": captcha_text.strip() if pending["captcha_required"] else "",
        "SubmitL": "Sign in",
        "option": "com_user",
        "task": "login",
        # base64 of "https://mvsr.winnou.net/index.php" - the post-login redirect target
        "return": "aHR0cHM6Ly9tdnNyLndpbm5vdS5uZXQvaW5kZXgucGhw",
    }
    payload[pending["token_name"]] = pending["token_value"]

    resp = session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)
    resp.raise_for_status()

    if _is_login_form(resp.text):
        raise LoginError("Login failed. Check your roll number, password, and captcha.")

    return session


def _clean_number(value: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", value)
    return float(cleaned) if cleaned else 0.0


def _parse_absent_dates(cell) -> dict:
    """
    The "Absent Dates" cell's visible text is truncated with "...".
    The full list lives in a `data-original-title` tooltip attribute,
    grouped by month like: "Mar:13th,20th,25th <br/> Jul:3rd".
    """
    tooltip_holder = cell.find(attrs={"data-original-title": True})
    raw = tooltip_holder["data-original-title"] if tooltip_holder else cell.get_text(strip=True)

    by_month: dict[str, list[str]] = {}
    flat: list[str] = []
    for chunk in raw.split("<br/>"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        month, _, days_part = chunk.partition(":")
        month = month.strip()
        days = [d.strip() for d in days_part.split(",") if d.strip()]
        if not days:
            continue
        by_month[month] = days
        flat.extend(f"{month} {d}" for d in days)

    return {"flat": flat, "by_month": by_month}


def _parse_student_info(soup: BeautifulSoup) -> dict:
    table = soup.find("table", class_="inner_table1")
    info = {"name": "", "father_name": "", "roll_no": "", "section": "", "address": ""}
    if not table:
        return info

    label_map = {
        "name:": "name",
        "father's name:": "father_name",
        "roll no.:": "roll_no",
        "section:": "section",
        "address:": "address",
    }

    cells = table.find_all(["td", "th"])
    for i, cell in enumerate(cells):
        label = cell.get_text(strip=True).lower()
        key = label_map.get(label)
        if key and i + 1 < len(cells):
            info[key] = cells[i + 1].get_text(strip=True)

    return info


def _find_viewtables(soup: BeautifulSoup) -> list:
    return soup.find_all("table", class_="viewtable")


def _parse_subjects_table(table) -> tuple[list[dict], dict | None]:
    subjects = []
    total = None

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 6:
            continue

        first_text = cells[0].get_text(strip=True)
        row_text_upper = row.get_text(" ", strip=True).upper()

        if cells[0].name == "th" and "TOTAL" in row_text_upper:
            values = [c.get_text(strip=True) for c in cells]
            nums = [_clean_number(v) for v in values[1:] if re.search(r"\d", v)]
            # [held, attended, percentage, events_held, events_attended, events_pct, overall_pct]
            if len(nums) >= 3:
                total = {
                    "held": nums[0],
                    "attended": nums[1],
                    "percentage": nums[2],
                    "events_held": nums[3] if len(nums) > 3 else 0.0,
                    "events_attended": nums[4] if len(nums) > 4 else 0.0,
                    "events_percentage": nums[5] if len(nums) > 5 else 0.0,
                    "overall_percentage": nums[6] if len(nums) > 6 else nums[2],
                }
            continue

        if not first_text.isdigit():
            continue

        subject_cell = cells[1]
        subject_id = None
        if subject_cell.get("id", "").startswith("subject-"):
            subject_id = subject_cell["id"].split("-", 1)[1]

        try:
            held = _clean_number(cells[2].get_text(strip=True))
            attended = _clean_number(cells[3].get_text(strip=True))
            absent = _clean_number(cells[4].get_text(strip=True))
            absent_info = _parse_absent_dates(cells[5]) if len(cells) > 5 else {"flat": [], "by_month": {}}
            percentage = _clean_number(cells[6].get_text(strip=True)) if len(cells) > 6 else (
                round((attended / held) * 100, 2) if held else 0.0
            )
            events_held = _clean_number(cells[7].get_text(strip=True)) if len(cells) > 7 else 0.0
            events_attended = _clean_number(cells[8].get_text(strip=True)) if len(cells) > 8 else 0.0
            events_percentage = _clean_number(cells[9].get_text(strip=True)) if len(cells) > 9 else 0.0
            overall_percentage = _clean_number(cells[10].get_text(strip=True)) if len(cells) > 10 else percentage
        except (ValueError, IndexError):
            continue

        subjects.append({
            "subject_id": subject_id,
            "subject": subject_cell.get_text(strip=True),
            "held": held,
            "attended": attended,
            "absent": absent,
            "percentage": percentage,
            "events_held": events_held,
            "events_attended": events_attended,
            "events_percentage": events_percentage,
            "overall_percentage": overall_percentage,
            "absent_dates": absent_info["flat"],
            "absent_dates_by_month": absent_info["by_month"],
        })

    return subjects, total


DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})\s*\((\w+)\)")


def _parse_calendar_table(table) -> list[dict]:
    rows = table.find_all("tr")
    days = []

    for row in rows:
        if row.get("class") and "sectiontableheader" in row.get("class"):
            continue

        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        date_text = cells[1].get_text(strip=True)
        match = DATE_RE.search(date_text)
        if not match:
            continue

        day, month, year, weekday = match.groups()
        date_iso = f"{year}-{month}-{day}"

        segments = []
        has_present = has_absent = has_class = False
        for cell in cells[2:]:
            status = cell.get_text(strip=True)
            width = int(cell.get("colspan", 1))
            segments.append({"status": status, "width": width})
            if status == "P":
                has_present = True
                has_class = True
            elif status == "A":
                has_absent = True
                has_class = True

        if not has_class:
            summary = "no_classes"
        elif has_absent:
            summary = "absent"
        elif has_present:
            summary = "present"
        else:
            summary = "no_classes"

        days.append({
            "date": date_iso,
            "weekday": weekday,
            "label": date_text,
            "segments": segments,
            "summary": summary,
        })

    return days


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


def get_dashboard_data(session: requests.Session, target: float = 75.0) -> dict:
    """
    Full pipeline: fetch the attendance page and parse student info,
    subject-wise summary (with bunk-meter verdicts + absent dates),
    and the day-by-day calendar history.
    """
    resp = session.get(ATTENDANCE_URL, timeout=15, allow_redirects=True)
    resp.raise_for_status()

    if _is_login_form(resp.text):
        raise ScrapeError("Session expired. Please log in again.")

    soup = BeautifulSoup(resp.text, "html.parser")

    student = _parse_student_info(soup)

    viewtables = _find_viewtables(soup)
    subjects_table = None
    calendar_table = None
    for table in viewtables:
        header_text = table.find("tr").get_text(" ", strip=True).lower() if table.find("tr") else ""
        if "subject" in header_text:
            subjects_table = table
        elif "attendance date" in header_text:
            calendar_table = table

    if subjects_table is None:
        raise ScrapeError("Could not find attendance table. Portal layout may have changed.")

    subjects, portal_total = _parse_subjects_table(subjects_table)
    if not subjects:
        raise ScrapeError("Attendance table was found but no subject rows could be parsed.")

    for s in subjects:
        s["bunk_meter"] = bunk_meter(s["attended"], s["held"], target)

    total_held = sum(s["held"] for s in subjects)
    total_attended = sum(s["attended"] for s in subjects)
    overall = bunk_meter(total_attended, total_held, target)
    overall["total_held"] = total_held
    overall["total_attended"] = total_attended
    if portal_total:
        overall["portal_percentage"] = portal_total["percentage"]

    calendar = _parse_calendar_table(calendar_table) if calendar_table is not None else []

    return {
        "student": student,
        "subjects": subjects,
        "overall": overall,
        "calendar": calendar,
    }
