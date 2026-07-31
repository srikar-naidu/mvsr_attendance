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
  - **Period Log** — the real, per-day sequence of Present/Absent/Gap
    blocks exactly as scraped from the portal for that date, browsable
    day by day. It's intentionally *not* called a timetable: the portal
    does not expose which subject runs in each period, so this shows
    the raw attendance record, not a class schedule. Earlier versions
    tried to normalize every day into a fixed 7-slot grid, which made
    different real days look artificially similar — this view now shows
    the literal scraped blocks with no reinterpretation.
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

### Deploying to Render

`render.yaml` in this repo is a Render **Blueprint** — Render reads it
automatically:

1. Push this repo to GitHub.
2. In the Render dashboard: **New > Blueprint**, point it at the repo.
3. Render reads `render.yaml` and creates a free web service with:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 1 --threads 8 --timeout 30`
   - `SECRET_KEY` auto-generated for you (`generateValue: true`)
   - `FLASK_DEBUG=0` already set
4. Deploy. Render gives you an HTTPS URL immediately.

If you'd rather configure it manually instead of using the Blueprint,
just set the Build/Start commands above and add the two env vars
yourself (`SECRET_KEY` — generate one with
`python -c "import secrets; print(secrets.token_hex(32))"` — and
`FLASK_DEBUG=0`).

**Free tier note:** Render's free web services spin down after 15
minutes of inactivity and take ~30-60s to wake back up on the next
request. That's fine for personal/classmate use, just expect a slow
first load after idle periods.

### Deploying to Railway

Railway auto-detects Python + the `Procfile` via its Nixpacks builder,
so no extra config file is required:

1. Push this repo to GitHub.
2. In Railway: **New Project > Deploy from GitHub repo**, pick this repo.
3. Railway detects `requirements.txt` and `Procfile` automatically and
   runs `gunicorn app:app --workers 1 --threads 8 --timeout 30`.
4. In the service's **Variables** tab, add:
   - `SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
   - `FLASK_DEBUG` = `0`
5. Under **Settings > Networking**, click **Generate Domain** to get a
   public HTTPS URL.

Railway's free tier doesn't sleep the way Render's does, but it runs on
a usage-based trial credit rather than being unconditionally free
long-term — check their current pricing before relying on it.

### Either way, double-check these two things after deploying

- **Only one instance/replica is running.** Both Render's and Railway's
  free tiers default to exactly one instance, so you shouldn't need to
  change anything — just don't manually scale to multiple instances
  without also moving `_active_sessions`/`_pending_logins` to shared
  storage first (see the architecture note above).
- **Outbound HTTPS to `mvsr.winnou.net` isn't blocked.** Both platforms
  allow normal outbound requests by default, but this is worth
  confirming with a real login attempt right after your first deploy,
  since it's the one thing I can't verify without an active deployment.

### ⚠️ Why not Vercel (for reference)

Vercel's Python runtime handles Flask fine — the problem is
statefulness, not the framework. Vercel Python functions are
**stateless, ephemeral serverless invocations** with no guarantee two
requests (e.g. `GET /api/login/start` then `POST /api/login`) land on
the same instance or that any instance survives between requests. This
app's entire login flow depends on the *same process* remembering the
pending CAPTCHA session and the authenticated portal session in memory
— on Vercel that memory can vanish or differ per-instance from one
request to the next, producing intermittent, hard-to-reproduce "session
expired" errors driven by Vercel's routing, not the code. Making this
work on Vercel would mean moving that state to an external store (e.g.
Upstash Redis) and storing serialized cookies instead of live
`requests.Session` objects — a real rearchitecture. Render/Railway need
none of that, which is why this repo is set up for them instead.

## Notes on the target percentage

75% is hardcoded in a couple of places (`scraper.py`'s `bunk_meter()`
default, and it's implied in the frontend copy). If your college's
actual cutoff differs, or you want it configurable per-user, that's a
quick change — happy to wire up a settings input if you want it.
