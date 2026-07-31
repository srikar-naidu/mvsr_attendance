"""
TEMPORARY debug script — NOT part of the app.

Logs into winnou with credentials passed as CLI args, saves the raw
HTML of the login response and the attendance page to local files so
we can inspect the real DOM structure. Delete this file (and the
saved .html files) once the scraper is finalized — they may contain
a captured session's page content.

Usage:
    python _debug_fetch.py <username> <password>
"""

import re
import sys
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://mvsr.winnou.net"
LOGIN_URL = f"{BASE_URL}/index.php"
ATTENDANCE_URL = f"{BASE_URL}/index.php?option=com_base_attendancereport&Itemid=98"

TOKEN_NAME_RE = re.compile(r"^[0-9a-f]{32}$")


def main():
    if len(sys.argv) != 3:
        print("Usage: python _debug_fetch.py <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; AttendanceBunkMeter/1.0)"
    })

    print("[1] GET login page...")
    resp = session.get(LOGIN_URL, timeout=15)
    print(f"    status={resp.status_code} len={len(resp.text)}")
    with open("_debug_login_page.html", "w", encoding="utf-8") as f:
        f.write(resp.text)

    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find("input", attrs={"name": TOKEN_NAME_RE})
    if not token_input:
        print("    !! No 32-char hex token field found. Saved HTML for inspection.")
        sys.exit(1)

    token_name = token_input["name"]
    token_value = token_input.get("value", "1")
    print(f"    token field: {token_name}={token_value}")

    payload = {
        "username": username,
        "passwd": password,
        "checkcaptcha": "0",
        "captcha": "",
        "SubmitL": "Sign in",
        "option": "com_user",
        "task": "login",
        "return": "aHR0cHM6Ly9tdnNyLndpbm5vdS5uZXQvaW5kZXgucGhw",
        token_name: "1",
    }

    print("[2] POST login...")
    resp = session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)
    print(f"    status={resp.status_code} final_url={resp.url} len={len(resp.text)}")
    with open("_debug_login_result.html", "w", encoding="utf-8") as f:
        f.write(resp.text)

    has_logout = "Log Out" in resp.text or "logout" in resp.text.lower()
    has_welcome = "Welcome" in resp.text
    print(f"    contains 'Log Out'-ish: {has_logout}  contains 'Welcome': {has_welcome}")

    print("[3] GET attendance page...")
    resp = session.get(ATTENDANCE_URL, timeout=15, allow_redirects=True)
    print(f"    status={resp.status_code} final_url={resp.url} len={len(resp.text)}")
    with open("_debug_attendance_page.html", "w", encoding="utf-8") as f:
        f.write(resp.text)

    soup = BeautifulSoup(resp.text, "html.parser")
    t1 = soup.find("table", id="fd-table-1")
    t2 = soup.find("table", id="fd-table-2")
    print(f"    fd-table-1 found: {t1 is not None}")
    print(f"    fd-table-2 found: {t2 is not None}")
    print("\nSaved: _debug_login_page.html, _debug_login_result.html, _debug_attendance_page.html")


if __name__ == "__main__":
    main()
