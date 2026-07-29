# Bunk Meter — MVSR Attendance Dashboard

Logs into the winnou/onEdu portal (`mvsr.winnou.net`) with a student's own
credentials, pulls their subject-wise attendance, and tells them how many
classes they can skip (or must attend) to stay at/reach 75%.

## How it works

- **`scraper.py`** — logs into winnou (handles the Joomla CSRF token
  automatically), scrapes the attendance table, and computes the bunk
  meter math per subject and overall.
- **`app.py`** — a small Flask backend. Each user's login creates an
  authenticated `requests.Session` kept server-side in memory (never the
  password itself), referenced by a random id in their browser cookie.
  Sessions auto-expire after 30 minutes of inactivity.
- **`templates/index.html`** — single-page login + dashboard UI.

Because every student authenticates with their own roll number and
password, this works for anyone at the college, not just one class —
each person only ever sees their own data.

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
