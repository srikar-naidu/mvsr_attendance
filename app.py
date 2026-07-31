"""
Backend for the Bunk Meter app.

Design:
- Each student logs in with THEIR OWN winnou credentials.
- Login is a two-step, captcha-aware relay:
    1. GET /api/login/start  - opens a session against winnou, returns
       whether winnou is demanding a captcha this time (it's random).
    2. POST /api/login       - submits username/password (+ captcha if
       required) using that same session, and promotes it to an
       authenticated session on success.
- We never persist passwords. The password is used once, in-memory,
  to authenticate a requests.Session, then discarded.
- The authenticated session is kept server-side in memory, keyed by
  a random session id stored in the user's browser cookie (Flask's
  signed session cookie holds only that random id, nothing sensitive).
- Sessions expire after SESSION_TTL_MINUTES of inactivity.
- Login attempts are rate-limited per IP to discourage brute-forcing
  winnou accounts through this relay.

This is a personal/small-scale project pattern - fine for you + your
classmates. If this ever needs to scale to hundreds of concurrent
users, swap the in-memory dicts for Redis with a TTL.
"""

import os
import time
import uuid
import secrets
from flask import Flask, request, jsonify, session, render_template, Response
from werkzeug.middleware.proxy_fix import ProxyFix

from scraper import start_login, finish_login, get_dashboard_data, LoginError, ScrapeError

# Render and Railway both terminate HTTPS at a reverse proxy in front of
# this app, forwarding plain HTTP with X-Forwarded-* headers. ProxyFix
# makes Flask/Werkzeug trust exactly one hop of those headers, so
# request.remote_addr (used for rate-limiting) and url_for's scheme are
# correct instead of showing the proxy's own address.
IS_PRODUCTION = os.environ.get("FLASK_DEBUG", "1") != "1"

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# IMPORTANT: set a fixed SECRET_KEY env var in any real deployment.
# A random per-process key breaks signed session cookies the moment you
# run more than one worker/instance (each would sign with a different
# key), and this app relies entirely on in-memory dicts keyed by that
# session cookie -- see the architecture note in README.md before
# deploying anywhere beyond a single local `python app.py` process.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

app.config.update(
    SESSION_COOKIE_SECURE=IS_PRODUCTION,   # only send the cookie over HTTPS in production
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

SESSION_TTL_MINUTES = 30
PENDING_LOGIN_TTL_MINUTES = 5

RATE_LIMIT_WINDOW_SECONDS = 5 * 60
RATE_LIMIT_MAX_ATTEMPTS = 8

# In-memory store: { session_uuid: {"session": requests.Session, "last_used": timestamp} }
_active_sessions: dict[str, dict] = {}

# In-memory store: { pending_uuid: {..pending login state.., "created": timestamp} }
_pending_logins: dict[str, dict] = {}

# In-memory store: { ip: [timestamps] } for login rate-limiting
_login_attempts: dict[str, list] = {}


def _cleanup_expired():
    cutoff = time.time() - (SESSION_TTL_MINUTES * 60)
    expired = [k for k, v in _active_sessions.items() if v["last_used"] < cutoff]
    for k in expired:
        del _active_sessions[k]

    pending_cutoff = time.time() - (PENDING_LOGIN_TTL_MINUTES * 60)
    expired_pending = [k for k, v in _pending_logins.items() if v["created"] < pending_cutoff]
    for k in expired_pending:
        del _pending_logins[k]


def _client_ip() -> str:
    # ProxyFix (see app setup above) already rewrites request.remote_addr
    # from X-Forwarded-For when running behind Render/Railway's proxy.
    return request.remote_addr or "unknown"


def _rate_limited(ip: str) -> bool:
    cutoff = time.time() - RATE_LIMIT_WINDOW_SECONDS
    attempts = [t for t in _login_attempts.get(ip, []) if t > cutoff]
    _login_attempts[ip] = attempts
    return len(attempts) >= RATE_LIMIT_MAX_ATTEMPTS


def _record_attempt(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/login/start")
def api_login_start():
    _cleanup_expired()

    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({"error": "Too many login attempts. Please wait a few minutes and try again."}), 429

    try:
        pending = start_login()
    except LoginError as e:
        return jsonify({"error": str(e)}), 502
    except Exception:
        return jsonify({"error": "Could not reach the attendance portal. Try again shortly."}), 502

    pending_id = str(uuid.uuid4())
    pending["created"] = time.time()
    _pending_logins[pending_id] = pending
    session["pending_id"] = pending_id

    return jsonify({
        "captcha_required": pending["captcha_required"],
        "captcha_url": "/api/login/captcha" if pending["captcha_required"] else None,
    })


@app.route("/api/login/captcha")
def api_login_captcha():
    pending_id = session.get("pending_id")
    pending = _pending_logins.get(pending_id) if pending_id else None

    if not pending or not pending.get("captcha_bytes"):
        return jsonify({"error": "No active captcha. Refresh the page and try again."}), 404

    return Response(pending["captcha_bytes"], mimetype=pending.get("captcha_content_type", "image/jpeg"))


@app.route("/api/login", methods=["POST"])
def api_login():
    _cleanup_expired()

    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({"error": "Too many login attempts. Please wait a few minutes and try again."}), 429
    _record_attempt(ip)

    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    captcha = data.get("captcha", "")

    if not username or not password:
        return jsonify({"error": "Roll number and password are required."}), 400

    pending_id = session.pop("pending_id", None)
    pending = _pending_logins.pop(pending_id, None) if pending_id else None

    if not pending:
        return jsonify({"error": "Login session expired. Please try again.", "need_restart": True}), 400

    try:
        winnou_session = finish_login(pending, username, password, captcha)
    except LoginError as e:
        return jsonify({"error": str(e), "need_restart": True}), 401
    except Exception:
        return jsonify({"error": "Could not reach the attendance portal. Try again shortly.", "need_restart": True}), 502

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
        data = get_dashboard_data(entry["session"])
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
    # Only used for local development (`python app.py`). Render/Railway run
    # this app via the Procfile's gunicorn command instead, which never
    # executes this block.
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=not IS_PRODUCTION, port=port, host="0.0.0.0")
