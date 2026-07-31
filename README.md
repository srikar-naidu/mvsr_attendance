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
  - **Timetable** — an honest "today's periods" view built from the
    portal's real per-slot P/A data. The portal does not expose which
    *subject* runs in each period, so periods are labeled by time slot,
    not by class name.
  - **Skip calculator** — pick any subject (or overall) and see exactly
    how many classes you can miss, or must attend, to stay at 75%.
  - **Profile** — student details, theme picker (light / dark / midnight
    / glass / system) with an accent color picker, and logout.
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

A few things worth doing, roughly in priority order:

1. **Run it over HTTPS.** Passwords are sent from the browser to your
   server in the login request — if you deploy this, use a host that
   gives you HTTPS by default (Render, Railway, Fly.io, PythonAnywhere
   all do this easily) rather than plain HTTP.
2. **Don't log request bodies.** Make sure your hosting platform (or
   any logging you add) isn't capturing POST body content, since that
   would capture passwords in transit even though the app itself never
   stores them.
3. **Rate-limit login attempts** per IP (e.g. with `flask-limiter`) so
   this can't be used to brute-force winnou accounts.
4. **Be considerate to winnou's servers** — don't poll `/api/attendance`
   on a timer; only fetch when the user opens the page or explicitly
   refreshes. This is one student's app hitting a college ERP, not a
   product — keep it lightweight.
5. Consider quickly checking your college's IT/acceptable-use policy
   before sharing this widely. Scraping your own data via your own
   login is generally fine, but policies vary by institution.

## Notes on the target percentage

75% is hardcoded in a couple of places (`scraper.py`'s `bunk_meter()`
default, and it's implied in the frontend copy). If your college's
actual cutoff differs, or you want it configurable per-user, that's a
quick change — happy to wire up a settings input if you want it.
