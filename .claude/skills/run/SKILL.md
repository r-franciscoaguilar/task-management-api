---
name: run
description: Start the Task Management API locally so it can be exercised manually (e.g. with curl or an HTTP client).
---

# Run

1. Ensure a virtualenv exists (Python 3.11+, developed on 3.13) and
   dependencies are installed:
   ```bash
   python3.13 -m venv .venv   # only if .venv doesn't exist yet
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
2. On first run, copy the env template if `.env` doesn't exist yet:
   ```bash
   cp .env.example .env
   ```
3. Assignment emails are really sent, so start a local mail catcher first --
   otherwise notifications are recorded as FAILED (correct behaviour, but not
   what you want while developing):
   ```bash
   source .venv/bin/activate
   python -m aiosmtpd -n -l localhost:1025
   ```
4. Start the API in a second terminal:
   ```bash
   source .venv/bin/activate
   uvicorn app.main:app --reload
   ```
5. Verify it's up:
   ```bash
   curl http://127.0.0.1:8000/health
   # -> {"status":"ok"}
   ```

The database is seeded automatically on first boot. `rm app.db` to start clean.

To skip email entirely, set `EMAIL_BACKEND=noop` in `.env`.
