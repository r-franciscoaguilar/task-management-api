# Task Management API

A backend system of record for an operations team's work items: who created
them, who is responsible, where each one stands, and whether the person on the
hook was actually told about it.

Built for the Software Engineer III take-home assessment. **Backend only** —
there is no frontend, and other systems are expected to integrate with this API.

> **Build status:** in progress, implemented step by step.
> Done: data model, identity/authorization layer, error handling, demo data,
> user endpoints.
> Not yet: task endpoints and real email delivery.
> See [Implementation status](#implementation-status) for the current state.

---

## Quick start

Requires **Python 3.11+** (developed against 3.13).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

uvicorn app.main:app --reload      # http://127.0.0.1:8000
pytest -q                          # run the test suite
```

Interactive API docs are at `http://127.0.0.1:8000/docs` once running.

The database is a local SQLite file (`app.db`), created **and populated with
demo data** on first boot — no extra setup step. See
[Sample data](#sample-data) for the cast of users and how to act as them.

```bash
rm app.db                     # start completely clean
python -m app.seed            # seed explicitly (no-op if users already exist)
python -m app.seed --reset    # drop everything and rebuild
```

---

## Interpretation of the scenario

The brief describes the problem but deliberately leaves the system's shape
undefined. These are the calls made, and why. Anything marked **assumption** was
genuinely ambiguous and resolved rather than clarified.

### The domain is a task, its ownership, and its history

Four tables:

| Table | Answers |
|---|---|
| `users` | Who exists, and are they a manager or a worker? |
| `tasks` | What is the work, whose is it *now*, and where does it stand? |
| `assignment_events` | Who assigned it to whom, and did the notification actually reach them? |
| `status_change_events` | How did the work move through its lifecycle, who moved it, and why did it ever move backward? |

The two event tables track **orthogonal axes**, which is why they are separate.
Reassigning a task changes ownership without changing status; starting a task
changes status without changing ownership. Neither table is a subset of the
other. An assignment writes to both, and that small redundancy is the accepted
cost of keeping every column meaningful on every row — the alternative, one
wide event table with a type discriminator, leaves notification columns null
on most rows.

### Two roles, with genuinely different powers

- **Managers** define work, decide who does it, and see everything. They
  explicitly **cannot** progress a task, because the brief says they "should
  not be doing someone else's work on their behalf". This is enforced, not
  merely undocumented.
- **Workers** see their own queue and record their own progress. They can only
  affect work that is actually theirs.

**Assumption — anyone can create a task.** The brief frames creation as
role-agnostic ("work starts when someone identifies something that needs to be
done") and only *assignment* as a management power. So either role may file a
task; `creator_id` is always recorded.

**Assumption — managers are peers, not silos.** Any manager can see and assign
any task, rather than only tasks they created. "Managers need to see
everything" reads as shared oversight.

### Work moves forward, with exactly one audited exception

```
UNASSIGNED  --(manager assigns)-->  ASSIGNED  --(assignee starts)-->  IN_PROGRESS  --(assignee completes)-->  DONE
                                        ^                                  |
                                        +---(assignee releases, reason required)---+
```

The brief says work should "not bounce backward **without a good reason**".
That qualifier is doing real work: it implies backward movement *with* a reason
is acceptable. So there is one backward edge — an assignee may **release** work
from `IN_PROGRESS` back to `ASSIGNED`, and only by supplying a reason that gets
persisted and is readable afterward. The business rule becomes an auditable
record instead of an unenforced expectation.

Without it the model had two real holes: a worker who is blocked or who picked
up the wrong item could only abandon it in `IN_PROGRESS` or falsely mark it
`DONE`; and since managers cannot reassign in-flight work, anything picked up by
the wrong person was stuck with them permanently. Release fixes both — released
work returns to the pool, where a manager can redirect it.

`DONE` is terminal. Every other backward move is impossible because **no
operation exists for it** — the invariant is enforced by absence rather than by
a guard that could be bypassed.

### Identity is assumed; authorization is real

Accounts and authentication live in another system, per the brief. This API
therefore trusts an **`X-User-Id` header** to name the caller and looks the role
up from the database.

This is a stand-in, and its limitation should be stated plainly: **anyone who
can reach the service can claim to be anyone.** That is acceptable here only
because the brief explicitly removes authentication from scope. Authorization is
a different matter and is fully enforced server-side, with tests covering each
boundary. Swapping the header for a verified JWT would mean changing one
dependency function and nothing else.

---

## Sample data

Seeded automatically on first boot. User ids are deterministic, so they can be
copied straight into the `X-User-Id` header.

| id | Name | Role |
|---|---|---|
| 1 | Alice Nguyen | manager |
| 2 | Bob Okafor | manager |
| 3 | Carol Diaz | worker |
| 4 | Dave Lindqvist | worker |
| 5 | Erin Sokolov | worker |

Seven tasks cover the whole lifecycle plus the cases that are easy to get wrong:

| id | Task | Status | Why it is here |
|---|---|---|---|
| 1 | Replace intake filter on pump 3 | `UNASSIGNED` | Filed, waiting to be routed |
| 2 | Loading bay door sticks when closing | `UNASSIGNED` | **Filed by a worker** — creation is not manager-only |
| 3 | Audit safety signage in warehouse B | `ASSIGNED` | Routed, not yet picked up |
| 4 | Reconcile October delivery invoices | `IN_PROGRESS` | Being worked on |
| 5 | Update onboarding checklist | `DONE` | Complete, end to end |
| 6 | Recalibrate scale in the loading bay | `ASSIGNED` | **Started, released with a reason, then reassigned** |
| 7 | Spot-check inventory counts on aisle 7 | `ASSIGNED` | **Notification failed** — the assignment still stands, and the failure is visible |

Task 6 is the one to look at to understand the data model: it has two
assignment records but only one of them corresponds to a status change, because
reassigning it moved ownership while the status stayed `ASSIGNED`.

### Acting as a user

Every request identifies its caller by header:

```bash
curl -s localhost:8000/users/me -H 'X-User-Id: 1'   # as Alice, a manager
curl -s localhost:8000/users/me -H 'X-User-Id: 3'   # as Carol, a worker
```

Omitting the header is a 401, as is an unknown or non-numeric id:

```json
{"error": "unauthenticated", "message": "Missing X-User-Id header. Every request must identify the acting user."}
```

A full task walkthrough will be added here once those endpoints exist.

### How the demo data is built, and why it matters

The seed does not simply insert seven task rows. A task sitting in
`IN_PROGRESS` with no assignment or status history would contradict the
invariants the API enforces — it would mean someone was made responsible for
work with no record of them being told, which is exactly what the traceability
requirement forbids. So the seed walks each task through its lifecycle and
writes the same event rows the service layer writes.

It deliberately does **not** call the service layer to do that, even though
that would guarantee consistency for free: assigning a task sends a real email,
so seeding through the services would fire notifications at fake addresses on
every fresh boot. The helpers in `app/seed.py` mirror the service behaviour
instead, and `tests/test_seed.py` asserts the result satisfies the invariants —
so drift is caught by a failing test rather than assumed away.

Those tests are worth more than they might appear. They check that status
history forms an unbroken chain, that `Task.assignee_id` always agrees with the
newest assignment record, that only backward moves carry a reason, and that
assignees are always workers. They already caught one real bug: a freshly
constructed `Task` has `status = None` until it is flushed, because column
defaults are applied on INSERT rather than in `__init__`, so the first version
of the seed recorded a status change *from* nothing.

---

## API

Every endpoint requires the `X-User-Id` header. Responses and errors all use
the shapes described under [Design notes](#design-notes).

### Implemented

| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `/health` | anyone | Liveness check; no identity required |
| `GET` | `/users/me` | any caller | The caller's own record and role |
| `GET` | `/users` | any caller | Optional `?role=MANAGER\|WORKER` filter (uppercase) |

`GET /users` returns a plain array rather than the paginated envelope planned
for tasks. Users are a small bounded reference set; the brief asks specifically
for a practical way to browse *work items* as they grow.

Both roles can read the directory. A manager needs it to choose an assignee,
and it is a small non-sensitive set, so restricting it would add friction
without protecting anything meaningful here. In a production system this would
likely be narrowed — manager-only, or excluding email addresses.

Response models are explicit rather than derived from the ORM, so adding a
column to a model can never silently leak it into an API response. There is a
test asserting exactly that.

### Planned

| Method | Path | Who |
|---|---|---|
| `POST` | `/tasks` | any caller |
| `GET` | `/tasks` | scoped by role |
| `GET` | `/tasks/{id}` | creator, assignee, or any manager |
| `POST` | `/tasks/{id}/assign` | managers |
| `POST` | `/tasks/{id}/start` | assignee |
| `POST` | `/tasks/{id}/complete` | assignee |
| `POST` | `/tasks/{id}/release` | assignee, reason required |
| `GET` | `/tasks/{id}/assignments` | scoped |
| `GET` | `/tasks/{id}/history` | scoped |

---

## Design notes

### One error envelope for everything

The business asked that invalid actions "fail in a way a client application
could explain to a user". Every error response — domain rejection, malformed
body, unmatched route — has the same shape:

```json
{
  "error": "invalid_state_transition",
  "message": "Cannot start a task that is not ASSIGNED.",
  "current_status": "IN_PROGRESS"
}
```

`error` is a stable machine code the client branches on; `message` is text it
can surface; any remaining fields are context explaining *why*. Domain code
raises a typed `AppError` and never builds an HTTP response, so the service
layer stays free of web-framework concerns. FastAPI's own validation errors and
stray `HTTPException`s are normalized into the same envelope, so a client never
has to parse two formats.

| Exception | Status | Meaning |
|---|---|---|
| `UnauthenticatedError` | 401 | Caller could not be identified |
| `ForbiddenError` | 403 | Caller is known, but not allowed |
| `NotFoundError` | 404 | Does not exist, or caller may not know it does |
| `ConflictError` | 409 | Well-formed, but conflicts with current state |
| `InvalidStateTransitionError` | 409 | Lifecycle move the state machine forbids |
| `ValidationError` | 422 | Syntactically fine, semantically wrong |

### Why a bad identity header is 401 and not 422

`X-User-Id` is read as a string and parsed by hand. Had it been typed as an
`int`, FastAPI would have rejected `X-User-Id: bogus` with its own 422 before
any of our code ran — so a malformed credential and an unknown one would fail
in two different ways. From the caller's side they are the same problem: an
unusable credential. Both return 401.

### 404 rather than 403 for other people's tasks

When a worker asks for a task that is not theirs, the answer is 404, not 403. A
403 would confirm the task exists, which leaks information to someone with no
right to know. "The wrong person cannot" is read as *cannot observe*, not just
*cannot modify*.

### POST for state transitions

Transitions are `POST /tasks/{id}/{action}` rather than `PUT` or `PATCH`.

- **Not PUT** — PUT means create-or-replace the resource at that URI.
  `/tasks/{id}/start` names no fetchable representation. And `PUT /tasks/{id}`
  would require the client to send a complete task representation, when in fact
  clients may not set `status` directly, `creator_id` is immutable, and
  timestamps are server-owned.
- **Not PATCH** — PATCH means partial modification of the resource at that URI.
  That is coherent for `PATCH /tasks/{id}` with `{"status": ...}` (a genuine
  alternative that was considered), but incoherent for `/start`, which is not a
  resource at all.
- **POST** is specified as resource-specific processing of the payload: the
  general method for commands that are not retrieve/replace/delete. It promises
  neither idempotency nor a fetchable URI, which matches a state transition
  exactly. One handler per transition also keeps three different role rules
  separate instead of branching inside one generic handler.

**These transitions are deliberately not idempotent.** `assign` appends an
event and sends an email on every call — recording each assignment *action* is
the traceability requirement, so repeat calls must not silently collapse. A
repeated `complete` would overwrite `completed_at` and corrupt the record of
when work actually finished. A repeat call also signals a stale or racing
client, and silently returning 200 would discard exactly the signal the brief
asked us to surface. The cost is real: a client retrying after a network
timeout cannot distinguish "already applied" from "out of order". It is
mitigated by returning `current_status` in the 409 body so the client can
reconcile; the production answer is `Idempotency-Key` dedup, noted below.

### Timestamps are UTC, explicitly

SQLite has no native timezone support, so values written through a plain
`DateTime(timezone=True)` come back timezone-*naive* and would serialize into
API responses as ambiguous bare timestamps. A small `UtcDateTime` type
(`app/core/time.py`) normalizes to UTC on write and re-attaches it on read, so
clients always receive an explicit offset.

### No migration tool

Tables are created with `Base.metadata.create_all()` at startup. For a
greenfield service on a single SQLite file with no deployment in scope, Alembic
would be setup cost with no payoff. This is the first thing to change before
production.

---

## Project layout

```
app/
  main.py                    app wiring: lifespan, exception handlers, routers
  db.py                      engine, session factory, get_db dependency
  deps.py                    caller identity (X-User-Id) and role guards
  exceptions.py              AppError hierarchy + the single error envelope
  seed.py                    demo data; also runnable as `python -m app.seed`
  core/
    config.py                environment-backed settings
    time.py                  utcnow() and the UtcDateTime column type
  models/                    SQLAlchemy models (one per file)
  schemas/                   Pydantic request/response models
  routers/                   HTTP endpoints, thin over the service layer
tests/
  conftest.py                in-memory DB and fixtures shared by all tests
  test_auth_and_errors.py    identity, role guards, error envelope
  test_seed.py               demo data obeys the domain invariants
  test_users.py              user endpoints
  test_health.py
```

### What `db.py` does

- **`Base`** — declarative base; subclassing it registers a table into
  `Base.metadata`, which is what `create_all()` reads.
- **`engine`** — connection pool and dialect, created once per process.
  `check_same_thread=False` is required because FastAPI runs sync endpoints in a
  threadpool while SQLite's driver otherwise refuses cross-thread connections;
  it is guarded so the setting cannot leak into a non-SQLite config.
- **`SessionLocal`** — session factory. `autoflush=False` prevents surprising
  mid-function writes; `expire_on_commit=False` keeps attributes readable after
  `commit()`, which matters because services commit and then hand the object
  back for serialization.
- **`get_db()`** — yields one session per request and always returns the
  connection to the pool. Tests override it to point at an in-memory database.

---

## Testing

```bash
pytest -q
```

Each test runs against a fresh in-memory SQLite database. `StaticPool` is what
makes that work: by default every connection to `:memory:` gets its own empty
database, so the pool must hand the same connection to the app, the fixtures,
and the assertions.

Coverage is aimed at the paths where a bug would be most costly — authorization
boundaries and lifecycle rules — rather than at line count.

Currently covered:

- Identity resolution: valid, missing, blank, non-numeric, and unknown
  `X-User-Id`, all resolving the way a caller would expect
- Role guards in both directions, including that a manager is *rejected* from
  worker actions
- The error envelope: domain errors, context fields, unmatched routes, and
  framework validation errors all producing one consistent shape
- Demo data integrity: unbroken status history, assignee agreeing with the
  newest assignment record, reasons present on backward moves only, completion
  timestamps, and assignees always being workers
- User endpoints: identity resolution, role filtering, and that responses
  expose only the declared fields

Tests run against the real application with the database dependency swapped for
an in-memory one. `TestClient` is deliberately used *without* its context
manager: entering it would run the app's lifespan, which would create and seed
the developer's real `app.db` during a test run.

---

## Implementation status

| Step | Status |
|---|---|
| Data model (users, tasks, assignment + status-change events) | done |
| Identity, role guards, error envelope | done |
| Seed data | done |
| User endpoints | done |
| Task create / list / detail with role scoping | not started |
| Assignment + notification abstraction | not started |
| Lifecycle transitions (start / complete / release) | not started |
| Pagination | not started |
| Real SMTP delivery | not started (deliberately last) |

---

## Reflection

_To be completed once the system is finished — see the assessment's required
prompts. Notes are being captured in the design sections above as decisions are
made, so these answers reflect the reasoning at the time rather than
reconstruction afterwards._

---

## AI usage disclosure

_To be completed._
