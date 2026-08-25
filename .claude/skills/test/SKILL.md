---
name: test
description: Run the automated test suite for the Task Management API.
---

# Test

```bash
source .venv/bin/activate
pytest -q
```

Useful flags while iterating:
- `-k <expr>` — run only tests matching an expression
- `--maxfail=1` — stop at the first failure
- `-x` — stop on first error or failure, same as `--maxfail=1`

Keep this in sync as the suite grows — e.g. add a `-m "not slow"` convention if
integration tests (real DB, real SMTP) get marked separately from unit tests.