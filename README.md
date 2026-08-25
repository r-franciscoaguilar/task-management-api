# Task Management API

Backend take-home assessment: a task management API for an operations team, covering
work-item creation, assignment, status progression, and email notifications on assignment.

> Status: scaffold only. Domain model, endpoints, auth, and email delivery are not yet implemented.

## Prerequisites

- Python 3.11+

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Run

```bash
uvicorn app.main:app --reload
```

## Test

```bash
pytest
```

## Design notes

_TODO_

## Reflection

**How did you interpret the scenario? What assumptions did you make?**

_TODO_

**What were the most important design decisions in your solution, and why?**

_TODO_

**How did you handle assignments and notifications?**

_TODO_

**How would you evolve this if the team or workload grew?**

_TODO_

**What trade-offs did you make given the timebox?**

_TODO_

**What would you add or change before production?**

_TODO_

## AI usage disclosure

_TODO_
