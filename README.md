# Bunk Meter — MVSR Attendance Dashboard

Logs into the winnou/onEdu portal (`mvsr.winnou.net`) with a student's own
credentials, pulls their subject-wise attendance, and tells them how many
classes they can skip (or must attend) to stay at/reach 75%.

## How it works

- **`scraper.py`** — logs into winnou (handles the Joomla CSRF token
  automatically, and the portal's **randomly-appearing CAPTCHA**), then
  scrapes:
  - student info (name, father's name, roll no, section)
  - subject-wise attendance, including full absent-date lists (pulled
    from the table's tooltip data, since the visible text is truncated)
  - a day-by-day period-level history
  - bunk-meter math per subject and overall
- **`app.py`** — a small Flask backend. Login is a two-step relay:
  1. `GET /api/login/start` opens a session against winnou and reports
     whether a CAPTCHA is required this time (the portal enables it
     unpredictably per page load).
  2. `POST /api/login` submits username/password (+ CAPTCHA text if
     needed) using that same session.

  Each user's login creates an authenticated `requests.Session` kept
  server-side in memory (never the password itself), referenced by a
  random id in their browser cookie. Sessions auto-expire after 30
  minutes of inactivity, and login attempts are rate-limited per IP.
- **`templates/index.html` / `static/style.css` / `static/app.js`** — a
  themeable single-page app (login with CAPTCHA support, then a tabbed
  dashboard):
  - **Dashboard** — profile summary, overall attendance ring, automatic
    alert banners (🔴 below 75%, 🟡 75–80%, 🟢 above 80%), and per-subject
    cards with full absent-date lists.
  - **Analytics** — 🔥 attendance streak, weekly/monthly attendance bar
    charts, present-vs-absent day donut, and a subject-wise trend list —
    all computed client-side from the real day-by-day history.
  - **Period Log** — a day-wise record of attendance per time slot,
    built from the portal's real per-slot P/A data. It's intentionally
    *not* called a timetable: the portal does not expose which subject
    runs in each period, so this shows attendance status by time slot,
    not by class name (that mapping only exists in the subject-wise
    summary table).
  - **Skip calculator** — pick any subject (or overall) and see exactly
    how many classes you can miss, or must attend, to stay at 75%.
  - **Profile** — student details, theme picker (light / dark / glass),
    and logout.
  - Fully responsive: a floating top nav on desktop, a fixed bottom nav
    on mobile.

Because every student authenticates with their own roll number and
password, this works for anyone at the college, not just one class —
each person only ever sees their own data.

### About the CAPTCHA

The winnou login page hides a `checkcaptcha` flag that's `"0"` or
`"1"` unpredictably per page load. When it's `"1"`, the portal expects
a CAPTCHA image to be solved. The backend detects this, downloads the
image, and serves it to the frontend at login time — the student solves
it right there in the login form, same as they would on the real
portal. No OCR or CAPTCHA-bypassing is involved.

## Running it locally

```bash
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:5000`.

## Before you deploy this for real classmates to use

1. **Run it over HTTPS.** Passwords are sent from the browser to your
   server in the login request — if you deploy this, use a host that
   gives you HTTPS by default (Render, Railway, Fly.io, PythonAnywhere
   all do this easily) rather than plain HTTP.
2. **Don't log request bodies.** Make sure your hosting platform (or
   any logging you add) isn't capturing POST body content, since that
   would capture passwords in transit even though the app itself never
   stores them.
3. **Rate-limiting is already built in** (`app.py`'s `_login_attempts`,
   8 attempts per 5 minutes per IP) so this can't easily be used to
   brute-force winnou accounts. No extra library needed.
4. **Be considerate to winnou's servers** — don't poll `/api/attendance`
   on a timer; only fetch when the user opens the page, hits the manual
   refresh button/pull-to-refresh, or logs in. This is one student's
   app hitting a college ERP, not a product — keep it lightweight.
5. Consider quickly checking your college's IT/acceptable-use policy
   before sharing this widely. Scraping your own data via your own
   login is generally fine, but policies vary by institution.

## Deployment

### The one architectural constraint that matters

Everything that makes login work — `_active_sessions`, `_pending_logins`
(the CAPTCHA-in-progress state), and `_login_attempts` — lives in
**plain Python dicts in process memory** (see `app.py`). That's simple
and fast for a single long-running process, but it means:

- **This app must run as exactly one process/worker.** If two processes
  (or two serverless invocations) handle two requests from the same
  user, whichever one didn't create the session has no idea it exists,
  and the user gets "session expired" errors that seem to come and go
  at random. `Procfile` is already set to `--workers 1 --threads 8` for
  this reason — threads share memory, extra worker *processes* don't.
- **`SECRET_KEY` must be fixed via an environment variable** in any real
  deployment. Without it, each restart (or each instance, if you ever
  do run more than one) signs cookies with a different random key, and
  sessions from before the restart silently stop validating.

### Recommended hosts (no architecture changes needed)

Render, Railway, Fly.io, and PythonAnywhere all run your app as a
persistent single process — exactly what this app already assumes.
Deployment is just:

1. Push this repo (the `Procfile` and `requirements.txt` are already set up).
2. Set environment variables:
   - `SECRET_KEY` — generate one once with `python -c "import secrets; print(secrets.token_hex(32))"` and set it permanently.
   - `FLASK_DEBUG=0` — disables the Flask debugger in production.
3. Make sure the host runs a **single instance/dyno** (the free tiers of
   all four hosts above do this by default — you'd have to opt in to
   multiple instances).

That's it — no code changes needed beyond what's already in this repo.

### ⚠️ Vercel specifically: not compatible as-is

You mentioned wanting to deploy this on Vercel. **I'd recommend against
it for this app without a rearchitecture**, for a reason that has
nothing to do with Flask support (Vercel's Python runtime handles Flask
fine) and everything to do with statefulness:

- Vercel Python functions run as **stateless, ephemeral serverless
  invocations**. There is no guarantee two requests — e.g. your
  `GET /api/login/start` and the following `POST /api/login` — land on
  the same instance, or that any instance survives long between
  requests.
- This app's entire login flow depends on the *same process* remembering
  the pending CAPTCHA session between those two requests
  (`_pending_logins`), and remembering the authenticated portal session
  between every subsequent `/api/attendance` call (`_active_sessions`).
  On Vercel, that memory can vanish or be on a different instance by
  the very next request — you'd see intermittent "login session
  expired" and "not logged in" errors that are impossible to reproduce
  reliably, because it depends on Vercel's routing/cold-start behavior
  at that moment, not your code.
- The `SECRET_KEY` issue above is worse here: every cold-started
  instance would need the *same* fixed key (fixable with an env var),
  but that alone doesn't fix the missing shared session state.

**To actually make this work on Vercel**, the in-memory dicts would
need to move to a shared external store (e.g. Vercel KV / Upstash
Redis), storing serialized cookies rather than live `requests.Session`
objects, reconstructing a session from stored cookies on every request.
That's a genuine backend rearchitecture, not a config change — happy to
build it if you want to go that route, but it needs you to provision a
KV/Redis instance first (there's a generous free tier on Upstash that
integrates natively with Vercel).

**My recommendation:** deploy to Render or Railway (both have simple
free/hobby tiers, HTTPS by default, and need zero code changes beyond
setting the two environment variables above) unless you specifically
need Vercel for another reason.

## Notes on the target percentage

75% is hardcoded in a couple of places (`scraper.py`'s `bunk_meter()`
default, and it's implied in the frontend copy). If your college's
actual cutoff differs, or you want it configurable per-user, that's a
quick change — happy to wire up a settings input if you want it.
