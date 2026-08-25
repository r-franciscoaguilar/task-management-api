---
name: run
description: Start the Task Management API locally so it can be exercised manually (e.g. with curl or an HTTP client).
---

# Run

1. Ensure a virtualenv exists (Python 3.11+; this project was set up against 3.13) and
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
3. Start the API:
   ```bash
   source .venv/bin/activate
   uvicorn app.main:app --reload
   ```
4. Verify it's up:
   ```bash
   curl http://127.0.0.1:8000/health
   # -> {"status":"ok"}
   ```

Update this skill once a DB migration step, seed-data loading, or a local mail-catcher
(for the assignment-notification emails) become part of the run flow — those will need
to start alongside the API for reviewers to exercise the full scenario.