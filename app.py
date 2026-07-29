"""
Backend for the Bunk Meter app.

Design:
- Each student logs in with THEIR OWN winnou credentials.
- We never persist passwords. The password is used once, in-memory,
  to authenticate a requests.Session, then discarded.
- The authenticated session is kept server-side in memory, keyed by
  a random session id stored in the user's browser cookie (Flask's
  signed session cookie holds only that random id, nothing sensitive).
- Sessions expire after SESSION_TTL_MINUTES of inactivity.

This is a personal/small-scale project pattern - fine for you + your
classmates. If this ever needs to scale to hundreds of concurrent
users, swap the in-memory dict for Redis with a TTL.
"""

import time
import uuid
import secrets
from flask import Flask, request, jsonify, session, render_template

from scraper import login, get_attendance_with_bunk_meter, LoginError, ScrapeError

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)  # rotates on restart -> logs everyone out, that's fine

SESSION_TTL_MINUTES = 30

# In-memory store: { session_uuid: {"session": requests.Session, "last_used": timestamp} }
_active_sessions: dict[str, dict] = {}


def _cleanup_expired():
    cutoff = time.time() - (SESSION_TTL_MINUTES * 60)
    expired = [k for k, v in _active_sessions.items() if v["last_used"] < cutoff]
    for k in expired:
        del _active_sessions[k]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/login", methods=["POST"])
def api_login():
    _cleanup_expired()

    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Roll number and password are required."}), 400

    try:
        winnou_session = login(username, password)
    except LoginError as e:
        return jsonify({"error": str(e)}), 401
    except Exception:
        return jsonify({"error": "Could not reach the attendance portal. Try again shortly."}), 502

    # password is now out of scope and garbage-collected; we only keep
    # the authenticated requests.Session (cookies), never the password.
    session_id = str(uuid.uuid4())
    _active_sessions[session_id] = {"session": winnou_session, "last_used": time.time()}
    session["sid"] = session_id

    return jsonify({"ok": True})


@app.route("/api/attendance")
def api_attendance():
    _cleanup_expired()

    sid = session.get("sid")
    entry = _active_sessions.get(sid) if sid else None
    if not entry:
        return jsonify({"error": "Not logged in or session expired. Please log in again."}), 401

    entry["last_used"] = time.time()

    try:
        data = get_attendance_with_bunk_meter(entry["session"])
    except ScrapeError as e:
        return jsonify({"error": str(e)}), 502
    except Exception:
        return jsonify({"error": "Unexpected error fetching attendance."}), 500

    return jsonify(data)


@app.route("/api/logout", methods=["POST"])
def api_logout():
    sid = session.pop("sid", None)
    if sid and sid in _active_sessions:
        del _active_sessions[sid]
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
