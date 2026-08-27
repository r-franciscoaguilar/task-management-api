# Task Management API

A backend system of record for an operations team's work items: who created
them, who is responsible, where each one stands, and whether the person on the
hook was actually told about it.

Built for the Software Engineer III take-home assessment. **Backend only** —
there is no frontend, and other systems are expected to integrate with this API.

> **Complete.** The full workflow is implemented and tested, including real SMTP
> delivery of assignment emails, verified against an external provider.

---

## Quick start

Requires **Python 3.11+** (developed against 3.13).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Assignment emails are really sent, so run a local mail catcher in one terminal
and the API in another. No mail account is needed.

```bash
# terminal 1 — catches the emails and prints them
python -m aiosmtpd -n -l localhost:1025

# terminal 2 — the API
uvicorn app.main:app --reload      # http://127.0.0.1:8000
```

```bash
pytest -q                          # 161 tests, ~2 seconds
```

Assign a task and the message appears in terminal 1. With nothing listening on
1025, assignments still succeed and the notification is recorded `FAILED` with
the reason — designed behaviour, not a crash. See
[Email delivery](#email-delivery) to point it at a real inbox.

Interactive docs: `http://127.0.0.1:8000/docs`. The database is a SQLite file
(`app.db`), created and seeded with [demo data](#sample-data) on first boot.

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

The two event tables track **orthogonal axes**, which is why they are separate:
reassigning changes ownership without changing status, and starting changes
status without changing ownership. An assignment writes to both. That redundancy
buys every column being meaningful on every row; one wide event table with a
type discriminator would leave the notification columns null on most rows.

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

The brief says work should "not bounce backward **without a good reason**", and
that qualifier implies backward movement *with* a reason is acceptable. So there
is one backward edge: an assignee may **release** work from `IN_PROGRESS` to
`ASSIGNED`, supplying a reason that is persisted and readable afterwards.

Without it the model had two holes. A blocked worker could only abandon a task
in `IN_PROGRESS` or falsely mark it `DONE`; and since managers cannot reassign
in-flight work, anything picked up by the wrong person was stuck there. Release
fixes both.

`DONE` is terminal. Every other backward move is impossible because **no
operation exists for it** — enforced by absence, not by a bypassable guard.

### Identity is assumed; authorization is real

Accounts and authentication live in another system, per the brief. This API
therefore trusts an **`X-User-Id` header** to name the caller and looks the role
up from the database.

The limitation, stated plainly: **anyone who can reach the service can claim to
be anyone.** Acceptable only because the brief removes authentication from scope.
Authorization is a different matter — fully enforced server-side, with tests on
each boundary. Swapping the header for a verified token changes one dependency
function and nothing else.

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

Task 6 is the one to look at to understand the data model: two assignment
records, but only one corresponding status change, because reassigning moved
ownership while the status stayed `ASSIGNED`.

### Acting as a user

Every request identifies its caller by header:

```bash
curl -s localhost:8000/users/me -H 'X-User-Id: 1'   # as Alice, a manager
curl -s localhost:8000/users/me -H 'X-User-Id: 3'   # as Carol, a worker
```

Scoping is easiest to see by comparing two callers against the seeded data:

```bash
curl -s localhost:8000/tasks -H 'X-User-Id: 1'   # Alice, manager: total 7
curl -s localhost:8000/tasks -H 'X-User-Id: 4'   # Dave, worker:  total 3
```

Dave sees three tasks for two different reasons — he is the **assignee** of
tasks 4 and 6, and the **creator** of task 2, which nobody has been assigned
yet. Asking for a task that is neither is a 404:

```bash
curl -s localhost:8000/tasks/5 -H 'X-User-Id: 4'
# {"error":"not_found","message":"No task with id 5 is available to you."}
```

### A full walkthrough

Carol files a task, Alice routes it, and it changes hands halfway through.

```bash
# 1. Carol (worker) files it -> 201, UNASSIGNED. Use the returned id as $ID.
curl -s -X POST localhost:8000/tasks -H 'X-User-Id: 3' \
  -H 'Content-Type: application/json' -d '{"title": "Fix conveyor belt"}'

# 2. Alice (manager) assigns it to Carol, who is emailed -> 200, ASSIGNED
curl -s -X POST localhost:8000/tasks/$ID/assign -H 'X-User-Id: 1' \
  -H 'Content-Type: application/json' -d '{"assignee_id": 3}'

# 3. Carol picks it up -> 200, IN_PROGRESS
curl -s -X POST localhost:8000/tasks/$ID/start -H 'X-User-Id: 3'

# 4. Alice tries to hand it to Dave -> 409, work is under way
curl -s -X POST localhost:8000/tasks/$ID/assign -H 'X-User-Id: 1' \
  -H 'Content-Type: application/json' -d '{"assignee_id": 4}'

# 5. Carol releases it with a reason -> 200, back to ASSIGNED
curl -s -X POST localhost:8000/tasks/$ID/release -H 'X-User-Id: 3' \
  -H 'Content-Type: application/json' \
  -d '{"reason": "Waiting on a replacement bearing"}'

# 6. Now Alice can redirect it, and Dave finishes it
curl -s -X POST localhost:8000/tasks/$ID/assign -H 'X-User-Id: 1' \
  -H 'Content-Type: application/json' -d '{"assignee_id": 4}'
curl -s -X POST localhost:8000/tasks/$ID/start    -H 'X-User-Id: 4'
curl -s -X POST localhost:8000/tasks/$ID/complete -H 'X-User-Id: 4'

# 7. The whole story, from either axis
curl -s localhost:8000/tasks/$ID/history     -H 'X-User-Id: 1'
curl -s localhost:8000/tasks/$ID/assignments -H 'X-User-Id: 1'
```

Step 7 yields the lifecycle ledger:

```
UNASSIGNED   -> ASSIGNED      by Alice Nguyen
ASSIGNED     -> IN_PROGRESS   by Carol Diaz
IN_PROGRESS  -> ASSIGNED      by Carol Diaz       reason: Waiting on a replacement bearing
ASSIGNED     -> IN_PROGRESS   by Dave Lindqvist
IN_PROGRESS  -> DONE          by Dave Lindqvist
```

Steps 4 to 6 are the reason `release` exists: without it, work picked up by the
wrong person could never be redirected, and a blocked worker could only abandon
the task in `IN_PROGRESS` or falsely mark it `DONE`.

### How the demo data is built, and why it matters

The seed does not simply insert seven task rows. A task in `IN_PROGRESS` with no
history would contradict the invariants the API enforces — someone made
responsible with no record of being told, which is what the traceability
requirement forbids. So it walks each task through its lifecycle, writing the
same event rows the service layer writes.

It deliberately does **not** go through the service layer, even though that
would guarantee consistency: assigning sends a real email, so seeding that way
would mail fake addresses on every boot. `app/seed.py` mirrors the service
instead, and `tests/test_seed.py` catches drift — checking that status history
forms an unbroken chain, that `Task.assignee_id` agrees with the newest
assignment record, that only backward moves carry a reason, and that assignees
are always workers.

Those tests earned their place immediately: a freshly constructed `Task` has
`status = None` until flushed, because column defaults apply on INSERT rather
than in `__init__`, so the first version recorded a status change *from* nothing.

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
| `POST` | `/tasks` | any caller | `{title, description?}` → 201, always starts `UNASSIGNED` |
| `GET` | `/tasks` | scoped | `?status=&assignee_id=&creator_id=&limit=&offset=` |
| `GET` | `/tasks/{id}` | scoped | 404 if the caller may not see it; includes `latest_assignment` |
| `POST` | `/tasks/{id}/assign` | managers | `{assignee_id}` — notifies the assignee |
| `POST` | `/tasks/{id}/start` | assignee only | `ASSIGNED` → `IN_PROGRESS` |
| `POST` | `/tasks/{id}/complete` | assignee only | `IN_PROGRESS` → `DONE` |
| `POST` | `/tasks/{id}/release` | assignee only | `{reason}` required — `IN_PROGRESS` → `ASSIGNED` |
| `GET` | `/tasks/{id}/assignments` | scoped | Assignment and notification history, newest first |
| `GET` | `/tasks/{id}/history` | scoped | Lifecycle history including release reasons |

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

### Scoping

`GET /tasks` returns different rows depending on who asks:

- **Managers** see every task, and may filter by `assignee_id` or `creator_id`
  to inspect anyone's workload.
- **Workers** see tasks assigned to them *plus tasks they filed themselves*. A
  worker who reports a problem should not lose sight of it merely because it
  has not been routed to them yet.

Filters apply **inside** that scope, never around it: a worker filtering by
another person's `assignee_id` gets an empty page rather than their own tasks
back, which would be misleading. `total` is scoped too, so it cannot disclose
how much work exists team-wide.

### Pagination

Offset/limit with an envelope:

```json
{"items": [...], "total": 25, "limit": 20, "offset": 0}
```

`limit` defaults to 20, capped at 100 — an unbounded limit is a denial-of-service
lever. Ordering is newest-first with `id` breaking ties on `created_at`; without
that tiebreak, tasks sharing a timestamp can be duplicated or skipped across
pages. A test walks the whole set and asserts every row appears exactly once.

Cursor pagination would be better at scale but cannot answer "how many" cheaply
— see [Evolution](#evolution).

### Assignment rules

`POST /tasks/{id}/assign` is managers-only and rejects in four distinct ways, so
a client can tell the cases apart:

| Situation | Status | `error` |
|---|---|---|
| Caller is a worker | 403 | `manager_role_required` |
| No such user | 404 | `not_found` |
| Assignee is a manager | 422 | `invalid_assignee` |
| Already assigned to that person | 409 | `already_assigned` |
| Task is `IN_PROGRESS` or `DONE` | 409 | `invalid_state_transition` |

Assigning to a manager is 422 rather than 404 because the user *does* exist —
they are simply not someone work is given to.

Re-assigning to whoever already holds the task is rejected rather than treated as
a nudge, because **every assignment sends a real email** and a double-submitted
request would mail the same person twice. A separate "resend notification" action
would be the right way to nudge, and is listed as future work.

### Lifecycle rules

Progress is recorded by the person doing the work — never by a manager on their
behalf, which the brief calls out explicitly. So `start`, `complete` and
`release` are all **workers only, and only the assignee**.

Refusals distinguish three different situations, which is the whole point of
having them:

| Situation | Status | `error` |
|---|---|---|
| Caller is a manager | 403 | `worker_role_required` |
| Caller cannot see the task at all | 404 | `not_found` |
| Caller can see it but is not the assignee | 403 | `not_assignee` |
| The move is not legal from the current status | 409 | `invalid_state_transition` |
| Release without a real reason | 422 | `validation_error` |

The 404-versus-403 split is deliberate. Visibility is checked first, so a task
the caller may not see returns 404 and leaks nothing. Only then does being the
assignee matter, and failing *that* is 403 — they can already see the task, so
denying its existence would be nonsense.

Refusals carry `current_status`, which a client retrying after a timeout needs to
reconcile. Nothing is recorded when a transition is refused.

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

`error` is a stable machine code to branch on, `message` is text to surface, and
the rest is context explaining *why*. Domain code raises a typed `AppError` and
never builds a response, so services stay free of HTTP concerns. FastAPI's own
validation errors and stray `HTTPException`s are normalized into the same shape,
so a client parses one format.

| Exception | Status | Meaning |
|---|---|---|
| `UnauthenticatedError` | 401 | Caller could not be identified |
| `ForbiddenError` | 403 | Caller is known, but not allowed |
| `NotFoundError` | 404 | Does not exist, or caller may not know it does |
| `ConflictError` | 409 | Well-formed, but conflicts with current state |
| `InvalidStateTransitionError` | 409 | Lifecycle move the state machine forbids |
| `ValidationError` | 422 | Syntactically fine, semantically wrong |

### Why a bad identity header is 401 and not 422

`X-User-Id` is read as a string and parsed by hand. Typed as an `int`, FastAPI
would reject `bogus` with its own 422 before our code ran, so a malformed
credential and an unknown one would fail differently. From the caller's side both
are the same problem — an unusable credential — so both return 401.

### 404 rather than 403 for other people's tasks

A 403 would confirm the task exists, leaking information to someone with no
right to know. "The wrong person cannot" is read as *cannot observe*, not merely
*cannot modify*. The message wording is identical to a genuinely missing task,
so there is no oracle.

### POST for state transitions

Transitions are `POST /tasks/{id}/{action}` rather than `PUT` or `PATCH`.

- **Not PUT** — PUT means create-or-replace the resource at that URI, and
  `/tasks/{id}/start` names no fetchable representation. `PUT /tasks/{id}` would
  require a complete task representation, when clients may not set `status`,
  `creator_id` is immutable, and timestamps are server-owned.
- **Not PATCH** — PATCH means partial modification of the resource at that URI.
  Coherent for `PATCH /tasks/{id}` with `{"status": ...}` (a genuine alternative
  that was considered), incoherent for `/start`, which is not a resource.
- **POST** is the general method for commands that are not
  retrieve/replace/delete. It promises neither idempotency nor a fetchable URI,
  which matches a state transition. One handler per transition also keeps three
  role rules separate rather than branching inside one.

**These transitions are deliberately not idempotent.** `assign` appends an event
and sends an email on every call — recording each assignment *action* is the
traceability requirement. A repeated `complete` would overwrite `completed_at`
and corrupt when work finished. A repeat call also signals a stale or racing
client, and returning 200 would discard the signal the brief asked us to
surface. The cost is real: after a timeout a client cannot distinguish "already
applied" from "out of order". Mitigated by `current_status` in the 409 body; the
production answer is `Idempotency-Key` dedup.

### Assignments and notifications

Assignment emails are **really sent**, over SMTP, using the standard library.
`SmtpEmailSender` is the default. `NoopEmailSender` exists only for switching
email off (`EMAIL_BACKEND=noop`) and is never the default — that would record
every notification as `SENT` while nothing was delivered.

`NotificationSender` is a one-method protocol resolved as a **FastAPI
dependency**, which is what lets tests inject a sender that fails on demand. The
failure path below is proven, not asserted.

The ordering inside `assign_task` is the design:

1. Authorize, validate the assignee, and check the lifecycle allows it.
2. Update the task, and insert an `AssignmentEvent` with
   `notification_status = PENDING`.
3. **Commit.** The audit record now exists, before any delivery is attempted.
4. Attempt delivery. Success records `SENT` plus a timestamp; failure records
   `FAILED` plus the error text. Commit again.

Committing *before* sending is deliberate: if the process dies mid-send, the
record survives as `PENDING` — an assignment whose notification is unaccounted
for, visible and retryable. Sending first would lose that.

**A failed email never rolls back the assignment.** Responsibility for work must
not depend on a reachable mail server. But the failure is not silent — stored on
the event, returned by the API, and logged. Seeded task 7 ships in that state.

Every exception is caught during delivery, not only `NotificationSendError`: the
assignment is already committed, so letting one escape would return 500 for a
request that succeeded and leave the notification stuck at `PENDING`.

The trade-off: delivery is synchronous and single-attempt, so a slow mail server
slows the request and a transient failure needs manual intervention. A
connection is also opened per message. The fix is not connection pooling but
moving delivery off the request path entirely — see [Evolution](#evolution).
`SMTP_TIMEOUT` (default 10s) bounds the damage in the meantime; without it an
unresponsive server would hang the request thread indefinitely.

### Email delivery

| Variable | Default | Meaning |
|---|---|---|
| `EMAIL_BACKEND` | `smtp` | `smtp` delivers for real; `noop` discards without sending |
| `SMTP_HOST` / `SMTP_PORT` | `localhost` / `1025` | Where to deliver |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | empty | Sent only if both are set |
| `SMTP_FROM_ADDRESS` | `noreply@task-api.local` | Envelope sender |
| `SMTP_USE_TLS` | `false` | STARTTLS after connecting |
| `SMTP_TIMEOUT` | `10` | Seconds before giving up |
| `NOTIFY_OVERRIDE_ADDRESS` | unset | If set, all mail goes here instead of the assignee |

**Locally**, the defaults target a mail catcher on port 1025
(`python -m aiosmtpd -n -l localhost:1025`), so delivery genuinely crosses a
socket with no account required.

**Against a real inbox**, point it at a provider:

```bash
EMAIL_BACKEND=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-username
SMTP_PASSWORD=your-password
SMTP_FROM_ADDRESS=tasks@yourdomain.com
SMTP_USE_TLS=true
```

Known limitation: STARTTLS on port 587 is supported; implicit TLS on port 465
is not, since it needs `SMTP_SSL` rather than `SMTP`.

Every smtplib and socket failure is translated into `NotificationSendError`, so
callers depend on this module's contract rather than on smtplib's exception
hierarchy. The stored error names the host and port — "connection refused" alone
tells an operator nothing about which server was unreachable.

#### Verifying delivery

There is a CLI that sends one message using the current configuration, so SMTP
settings can be checked without involving the API or the database:

```bash
python -m app.send_test_email you@example.com
```

It prints the resolved configuration (never the password) and, on failure, the
usual causes by SMTP response code. Getting it to pass first separates "my
credentials are wrong" from "the application is wrong".

#### What has been verified, and what "SENT" actually means

Delivery has been exercised against a **real external provider** (Mailtrap's
SMTP sandbox) on port 587, not only a local catcher. That confirmed what a
catcher cannot: STARTTLS negotiation and SMTP authentication, which it skips
entirely. An assignment through the HTTP API produced an authenticated,
TLS-encrypted message to the assignee, which arrived intact.

A *failure* was informative too: the same config against Mailtrap's production
endpoint returned `550 Sending from domain task-api.local is not allowed` —
correct on their side, and the reason a real deployment needs the DNS work in
[Email in production](#email-in-production).

`notification_status = SENT` therefore means **the mail server accepted
responsibility** — an SMTP `250`. It does not claim delivery to a human inbox,
which the SMTP transaction cannot tell you: a message may still bounce, be
greylisted, or be filtered. Learning that needs provider webhooks, so the state
is named for what is known rather than what is hoped.

**`NOTIFY_OVERRIDE_ADDRESS`** redirects every notification to one address,
preserving the intended recipient in the subject:

```
Subject: [to: carol@example.com] Task assigned to you: Replace intake filter on pump 3
```

So you can receive real assignment emails at your own inbox without editing seed
data, and a staging deployment can guarantee it never mails real users. It wraps
whichever backend is configured rather than complicating delivery, and the app
warns at startup when active — an unnoticed redirect makes notifications look
delivered while nobody received one.

### Email in production

What is here is a correct, tested delivery path. Running it for real would need
the following, roughly in order of what would hurt first:

1. **Move delivery off the request path.** An outbox plus a background worker:
   the `AssignmentEvent` is already written as `PENDING` before any send is
   attempted, so a worker can claim pending rows, deliver, and update them with
   **no schema change**. This decouples assignment latency from SMTP and is the
   prerequisite for everything below.
2. **Retries with backoff, and a dead-letter path.** Transient failures
   (timeouts, connection refused, SMTP 4xx) should be retried; permanent ones
   (SMTP 5xx, malformed address) should not. Today every failure is a single
   attempt that needs a human.
3. **A verified sending domain with SPF, DKIM and DMARC.** The current
   `noreply@task-api.local` is deliberately fake, and a production provider
   rejects it outright — as we saw. Without DKIM signing and aligned SPF, mail
   that *is* accepted lands in spam, which is indistinguishable from not sending
   it at all.
4. **Secrets management.** Credentials in `.env` are fine on a laptop. Production
   wants a secret manager with rotation, and care that they never reach logs or
   crash dumps.
5. **Bounce and complaint handling.** A provider webhook is the only way to learn
   that an address is dead or that someone marked the mail as spam. That means a
   `BOUNCED` state beyond `SENT`, and probably a per-user "email is not
   deliverable" flag so the team can fix it — otherwise sender reputation decays
   silently.
6. **Distinguish accepted from delivered.** With webhooks in place, `SENT`
   becomes "accepted by the provider" and a separate `DELIVERED` reflects what
   actually landed. The audit trail then answers the business question — *was
   this person told?* — rather than the technical one.
7. **A provider API rather than raw SMTP.** `NotificationSender` makes this a
   swap rather than a rewrite. HTTP APIs return a message id, which is what
   correlates a send with its later webhook, and they avoid a TCP and TLS
   handshake per message.
8. **Observability with a business-level alert.** Metrics on attempts, failures
   by reason, and the age of the oldest `PENDING` row. Rising `FAILED` counts
   mean people are not being told about work assigned to them — a business
   incident, not a technical curiosity.
9. **Templates instead of string building, and notification preferences.** Text
   and HTML alternatives from real templates, with per-user preferences and
   digests: at team scale, one email per assignment becomes noise people filter
   away, which quietly defeats the whole feature.
10. **PII discipline.** The startup and failure logs currently contain email
    addresses. Production logging should scrub or tokenise them.

### Business rules live in a service layer

`app/services/tasks.py` holds everything that decides what is allowed; routers
only translate HTTP. The rules are then testable without an HTTP layer, and a
rule in one place cannot be forgotten by a second endpoint touching the same
data. Services raise typed `AppError`s and never construct responses.

### Timestamps are UTC, explicitly

SQLite has no native timezone support, so values written through a plain
`DateTime(timezone=True)` come back *naive* and would serialize as ambiguous bare
timestamps. `UtcDateTime` (`app/core/time.py`) normalizes on write and
re-attaches UTC on read, so clients always get an explicit offset.

### No migration tool

Tables are created with `Base.metadata.create_all()` at startup. For a greenfield
service on one SQLite file with no deployment in scope, Alembic would be setup
cost with no payoff — but it cannot evolve a schema holding data, so it is among
the first things to change before production.

---

## Project layout

```
app/
  main.py                    app wiring: lifespan, exception handlers, routers
  db.py                      engine, session factory, get_db dependency
  deps.py                    caller identity (X-User-Id) and role guards
  exceptions.py              AppError hierarchy + the single error envelope
  seed.py                    demo data; also runnable as `python -m app.seed`
  send_test_email.py         CLI to verify SMTP settings in isolation
  core/
    config.py                environment-backed settings
    time.py                  utcnow() and the UtcDateTime column type
  models/                    SQLAlchemy models (one per file)
  schemas/                   Pydantic request/response models
  routers/                   HTTP endpoints, thin over the service layer
  services/
    tasks.py                 lifecycle and visibility rules
    notifications.py         sender protocol, SMTP sender, message building
tests/
  conftest.py                in-memory DB and fixtures shared by all tests
  test_auth_and_errors.py    identity, role guards, error envelope
  test_seed.py               demo data obeys the domain invariants
  test_users.py              user endpoints
  test_task_creation.py      creating tasks and input validation
  test_task_visibility.py    who can see which tasks
  test_pagination.py         paging, and that it cannot widen scope
  test_assignment.py         assigning, reassigning, and notification outcomes
  test_lifecycle.py          transitions, refusals, and the release path
  test_smtp_delivery.py      real delivery against an in-process SMTP server
  test_health.py
```

### What `db.py` does

- **`Base`** — declarative base; subclassing it registers a table into
  `Base.metadata`, which is what `create_all()` reads.
- **`engine`** — connection pool and dialect, created once per process.
  `check_same_thread=False` is needed because FastAPI runs sync endpoints in a
  threadpool and SQLite's driver otherwise refuses cross-thread connections;
  guarded so it cannot leak into a non-SQLite config.
- **`SessionLocal`** — session factory. `autoflush=False` prevents surprising
  mid-function writes; `expire_on_commit=False` keeps attributes readable after
  `commit()`, since services commit and then hand the object back to be
  serialized. (That convenience has a cost — see the assignment bug in
  [What changed during the build](#what-changed-during-the-build).)
- **`get_db()`** — one session per request, always returned to the pool. Tests
  override it to point at an in-memory database.

---

## Testing

```bash
pytest -q
```

Each test runs against a fresh in-memory SQLite database. `StaticPool` is
required: every connection to `:memory:` otherwise gets its own empty database,
so the pool must hand the same one to the app, the fixtures, and the assertions.

Coverage targets the paths where a bug would be most costly — authorization
boundaries and lifecycle rules — not line count.

| File | Covers |
|---|---|
| `test_auth_and_errors.py` | Identity: valid, missing, blank, non-numeric, unknown. Role guards both directions. One error envelope, including framework validation errors and unmatched routes |
| `test_seed.py` | Demo-data invariants: unbroken status chain, assignee agreeing with the newest assignment, reasons on backward moves only, assignees always workers |
| `test_users.py` | Role filtering; responses exposing only declared fields |
| `test_task_creation.py` | Either role may create; a task cannot be created pre-assigned; title trimming and blank/overlong rejection |
| `test_task_visibility.py` | Manager sees all, worker sees own-and-filed; scoped `total`; filters narrow rather than widen; a hidden task's 404 is indistinguishable from a missing one |
| `test_pagination.py` | Default and explicit windows; full coverage with no duplicate or skipped rows; scope preserved across pages; out-of-range bounds rejected |
| `test_assignment.py` | Happy path; all five rejections; reassignment appends history and records *no* status change; a failed notification leaves the assignment intact, including an undeclared exception type |
| `test_lifecycle.py` | Full journey; every illegal transition refused with `current_status`; `DONE` immovable both ways; managers barred; the 404-vs-403 tiers; refused transitions record nothing. Release: reason stored and trimmed, blank rejected, assignee retained, and a previously unreassignable task becoming reassignable |
| `test_smtp_delivery.py` | A **real in-process SMTP server**: envelope, headers and body; Unicode round trip; refused connection and unresolvable host; end to end through the API; a dead mail server leaving the assignment intact. Plus redirect behaviour |

Tests run against the real application with the database dependency swapped out.
`TestClient` is used *without* its context manager on purpose: entering it would
run the lifespan, creating and seeding the developer's real `app.db` mid-test.
Config tests likewise build `Settings(_env_file=None)`, after real credentials in
`.env` broke four tests asserting defaults.

---

## Implementation status

| Step | Status |
|---|---|
| Data model (users, tasks, assignment + status-change events) | done |
| Identity, role guards, error envelope | done |
| Seed data | done |
| User endpoints | done |
| Task create / list / detail with role scoping | done |
| Assignment + notification abstraction | done |
| Lifecycle transitions (start / complete / release) | done |
| Pagination | done |
| Real SMTP delivery | done |

---

## Evolution

Known gaps, in rough priority order. These are deliberate scope decisions, not
oversights.

**Before this could be called production-ready**

- **Real authentication.** `X-User-Id` is a stand-in; anyone reaching the
  service can claim to be anyone. Replace with a verified token — one
  dependency function changes.
- **Asynchronous notification with retries.** Delivery is currently synchronous
  and single-attempt, so a slow mail server slows the request and a transient
  failure needs manual intervention. An outbox plus a background worker would
  fix both, and the `PENDING` state already exists for it.
- **Migrations.** `create_all()` cannot evolve a schema that already holds data.
  Alembic before the first real deployment.
- **Idempotency keys.** Transitions are deliberately non-idempotent, which
  leaves a client unable to distinguish "already applied" from "out of order"
  after a timeout. An `Idempotency-Key` header with server-side dedup is the
  proper fix.

**If the team or the workload grew**

- **Postgres instead of SQLite**, for concurrent writes and real indexing.
- **Cursor pagination** for the task list; offset paging drifts when rows are
  inserted mid-browse and degrades as the offset grows.
- **A merged timeline endpoint.** Reassignment appears in
  `/tasks/{id}/assignments` but not `/history`, because it changes ownership
  without changing status — so telling the whole story means reading both and
  merging by timestamp. Deliberately not built: the brief asks that notification
  not be invisible, which is satisfied, and most consumers want one axis or the
  other (a supervisor asks "who has this?", an auditor asks "how did it get
  here?"). If a unified view were needed I would add a read-side view over both
  tables rather than collapse them into one wide table.
- **A resend-notification action**, so nudging someone does not require the
  reassignment path.
- **Task editing and cancellation.** Neither is in the brief, but real
  operations teams need to correct a title and abandon obsolete work.
- **Richer queues** — due dates, priority, and filtering or sorting on them,
  which is what "supervisors have no single place to see what is in flight"
  eventually demands.

---

## Reflection

### How did you interpret the scenario? What assumptions did you make?

I read the brief as describing a **system of record for accountability**, not a
to-do list. Three phrases drove the design — work items must show "who created
them, who is responsible for them, and where each item stands"; the assignee
"must receive a real email"; work should "not bounce backward without a good
reason" — which map to a task with an owner, an auditable notification, and a
state machine.

The assumptions I had to make, and how I resolved them:

| Ambiguity | Resolution | Why |
|---|---|---|
| Who may create a task? | Either role | The brief frames creation role-agnostically ("someone identifies something that needs to be done"); only *assignment* is described as a management power |
| Do managers own their own tasks? | No — managers are peers, any manager can see and assign anything | "Managers need to see everything" reads as shared oversight |
| What does a worker's "own queue" contain? | Tasks assigned to them **plus tasks they filed** | A worker who reports a problem shouldn't lose sight of it before it's routed |
| What does "forward" mean? | `UNASSIGNED → ASSIGNED → IN_PROGRESS → DONE`, with one audited backward edge | The qualifier "without a good reason" implies backward movement *with* a reason is acceptable |
| Is `DONE` reopenable? | No, terminal | Nothing in the brief suggests reopening; a completed task that changes is really new work |
| When are notifications sent? | On assignment only | That is the one moment the brief names. Notifying on every transition would be inventing a requirement |
| What identifies the caller? | An `X-User-Id` header | Authentication is explicitly out of scope; something had to stand in for it |

I also decided what **not** to infer: no due dates, priorities, comments,
attachments, teams, or task editing. Each is plausible, none was asked for, and
the brief prefers a focused core to breadth. The two things I added beyond a
literal reading — the release transition and the second audit log — exist
because a specific sentence demanded them.

### What were the most important design decisions in your solution, and why?

**Two append-only event logs instead of mutable status fields.**
`assignment_events` records who was made responsible and whether they were told;
`status_change_events` records how the work moved and why it moved backward. The
axes are orthogonal — reassigning changes ownership without changing status,
starting changes status without changing ownership — so neither is a subset of
the other, and the task row is a projection over both. That makes traceability a
queryable fact rather than a claim. The cost: assigning writes to both tables,
and a unified timeline means reading both.

**All business rules live in a service layer.** `app/services/tasks.py` decides
what is allowed; routers only translate HTTP. The rules become testable without
HTTP, and a rule in one place cannot be forgotten by a second endpoint touching
the same data.

**One error envelope, with machine-readable codes.** Every failure returns
`{"error", "message", ...context}`, including FastAPI's own validation errors, so
a client never parses two formats. Refusals are *distinguishable*: assigning to a
manager is `invalid_assignee` (422), to a nonexistent user `not_found` (404) —
a client should be able to explain the difference to a person.

**Visibility is checked before ownership, and the two fail differently.** A task
the caller may not see returns 404, wording included, identical to one that never
existed; a 403 would confirm existence. A task they *can* see but don't own
returns 403, since denying its existence would be nonsense. I read "the wrong
person cannot" as cannot *observe*, not only cannot modify.

**The audit record is committed before the email is attempted, and a failed send
never rolls back the assignment.** A process that dies mid-send leaves a
`PENDING` row — visible and retryable — and responsibility for work must not
depend on a reachable mail server. Detail in
[Assignments and notifications](#assignments-and-notifications).

**Transitions are POST, and deliberately not idempotent.** `/start` names no
fetchable resource, so PUT and PATCH both misfit; POST is the method for
commands. Non-idempotency is a real choice with a real cost, argued in
[POST for state transitions](#post-for-state-transitions).

### How did you handle assignments and notifications?

`POST /tasks/{id}/assign` is managers-only and refuses in five distinguishable
ways: worker caller, unknown user, assignee is a manager, already that person's,
work already under way. Each success appends an `AssignmentEvent`, so
reassignment produces history rather than overwriting it.

The ordering inside the service is the design:

1. Authorize, validate the assignee, check the lifecycle permits it.
2. Update the task; insert an `AssignmentEvent` with `notification_status = PENDING`.
3. **Commit** — the audit record now exists, before any delivery is attempted.
4. Attempt delivery; record `SENT` with a timestamp or `FAILED` with the error
   text. Commit again.

Delivery failure is recorded, not swallowed: it appears on the task's
`latest_assignment`, in `GET /tasks/{id}/assignments`, and in the logs. Seeded
task 7 ships in that state so a reviewer can see it. Every exception is caught,
not only the declared one — the assignment is already committed, so letting one
escape would return 500 for a request that succeeded.

Sending sits behind a `NotificationSender` protocol resolved as a **FastAPI
dependency**, which is what makes the failure path provable: tests inject a
sender that raises and assert the assignment still stands. You cannot test
"email is broken" against a working sender.

Delivery is real: `SmtpEmailSender` uses stdlib `smtplib` and is the default
backend. Locally the defaults target a mail catcher on port 1025, so a reviewer
sees actual messages without needing a mail account. Proving this needed a test
that runs an SMTP server in-process and asserts the message arrives — asserting
`.send()` was called would say nothing about whether anything left the process.

It has also been run against a real external provider over port 587, which is
what exercises STARTTLS and SMTP authentication; a local catcher requires
neither, so those two branches were untested until then. Details, including what
`SENT` does and does not claim, are under
[What has been verified](#what-has-been-verified-and-what-sent-actually-means).

The honest limitation: delivery is **synchronous and single-attempt**, so a slow
mail server slows the request and a transient failure needs manual
intervention. `SMTP_TIMEOUT` bounds the damage, and the `PENDING` state exists
precisely so a retry mechanism can be added without a schema change.

### How would you evolve this if the team or workload grew?

**Immediately, on volume:** Postgres, for concurrent writes and real indexing —
SQLite serialises writers, fine for a demo and wrong for a team. Cursor
pagination for the task list, since offset paging drifts when rows are inserted
mid-browse and degrades as the offset grows. The visibility filter becomes the
hot query; both columns are already indexed.

**On the notification path:** move delivery to an outbox plus a background
worker with retry and backoff, so assignment latency stops depending on SMTP.
At team scale, people also stop wanting one email per assignment — that becomes
digests and per-user preferences, which is a new entity rather than a tweak.

**On the model:** managers are peers who all see everything, which becomes noise
past a certain size. Tasks would then belong to a team or area with visibility
scoped accordingly — one change, in `_visibility_conditions`, which is why that
logic is a single function.

**On the API:** a read-side view merging both event logs, since telling the full
story currently takes two calls. Then due dates, priority and sorting on them —
what "supervisors have no single place to see what is in flight" demands once
there is more in flight than fits on a screen.

The [Evolution](#evolution) section lists these with the reasoning attached.

### What trade-offs did you make given the timebox?

Each of these I would defend in context, rather than hope nobody noticed.

- **SQLite over Postgres.** Zero infrastructure means a reviewer runs one
  command. Costs concurrent writes and real index behaviour.
- **`create_all()` over Alembic.** Cannot evolve a schema holding data, but
  there is no deployment in scope and migration tooling would have been setup
  cost with no payoff.
- **Header-based identity.** Anyone who can reach the service can claim to be
  anyone. Acceptable only because authentication is explicitly out of scope, and
  isolated to one dependency function so replacing it is a contained change.
- **Offset pagination.** Simpler, and it answers "how many are there" cheaply,
  which cursors don't.
- **Synchronous email.** No queue infrastructure to run locally; the cost is
  request latency coupled to SMTP.
- **The seed writes rows directly instead of calling the services.** Going
  through the services would guarantee consistency, but assigning sends email,
  so seeding that way would fire notifications at fake addresses on every boot.
  I mirrored the service behaviour and wrote invariant tests to catch drift —
  and they immediately caught a real bug.
- **No task editing, cancellation, or deletion.** Not in the brief. A real
  operations team needs all three.
- **The user directory is readable by both roles.** Convenient for reviewers,
  and it exposes email addresses more widely than a production system should.
- **No rate limiting, metrics, or structured logging.** Operability here means
  "a person can run and inspect it locally", which the brief asks for; it does
  not yet mean observable in production.

What I did **not** trade away: the authorization boundaries and the lifecycle
rules. Those are the correctness requirements, enforced server-side in one place,
and they carry most of the 161 tests.

### What would you add or change before production?

Ranked by what would hurt first:

1. **Real authentication.** Replace the trusted header with a verified token.
   Nothing else about authorization changes — the role checks already run
   server-side.
2. **Asynchronous notification with retries**, then the rest of
   [Email in production](#email-in-production) — a verified sending domain with
   SPF/DKIM/DMARC, bounce webhooks, and separating "accepted by the provider"
   from "actually delivered".
3. **Migrations.** Alembic before the first deployment that holds real data.
4. **Postgres**, with the transaction and index review that implies.
5. **Idempotency keys.** Transitions are non-idempotent by design, which leaves
   a client unable to distinguish "already applied" from "out of order" after a
   timeout. An `Idempotency-Key` header with server-side dedup is the right fix
   — not making transitions silent no-ops, which would discard the signal the
   brief asked us to surface.
6. **Observability.** Structured logs with a request id, metrics on assignment
   and notification failure rates, and an alert when `FAILED` or `PENDING`
   notifications accumulate — that number going up is a business problem, not
   just a technical one.
7. **A PII review of logs and error messages.** Assignment failures currently
   log an email address.
8. **CI** running the test suite, plus a linter and type checker in the pipeline
   rather than only locally.

### What changed during the build

Worth recording, because none of it was in the first design:

- **The state machine was originally strictly forward-only.** Re-reading
  "without a good reason" made clear the qualifier was doing real work, and that
  a strict reading left two holes: a blocked worker could only abandon a task in
  `IN_PROGRESS` or falsely complete it, and since in-progress work cannot be
  reassigned, anything picked up by the wrong person was stuck there forever.
  The `release` transition with a mandatory reason closes both.
- **Worker-supplied list filters were originally ignored.** In practice that
  meant a worker filtering by someone else's id silently received their own
  tasks — actively misleading. Applying filters *inside* the visible scope is
  both less code and truthful: an empty page means "nothing you can see
  matches".
- **Transitions were originally `PATCH /tasks/{id}/start`.** That borrows
  resource-modification semantics for what is really a command, and `/start`
  names no resource to modify. POST is the honest verb.

---

## AI usage disclosure

**Tools used.** Claude (Opus, via the Claude Code CLI) throughout, as a pair
rather than a code generator: scaffolding, most of the implementation, the test
suite, and drafting this README.

**How the work was divided.** I directed the design and made the judgment calls;
the model did most of the typing. Mine are the shape of the domain, the choice of
what to leave out, and two changes that came from challenging the first
proposal:

- I questioned `PATCH` on the transition endpoints, which led to working through
  PUT/PATCH/POST semantics properly and switching to POST.
- I pushed back that a strictly forward-only state machine felt wrong for real
  work, which produced the `release` transition and the second event table.

Both are documented above under [What changed during the
build](#what-changed-during-the-build).

**What I validated rather than assumed.** Every endpoint was exercised manually
with curl against a running server as well as by tests; the two catch different
things. Four real bugs surfaced:

1. A freshly constructed `Task` has `status = None` until flushed, because
   column defaults apply on INSERT rather than in `__init__`. The seed was
   recording a status change *from nothing*. Caught by the seed invariant tests.
2. Setting `task.assignee_id` left an already-loaded `assignee` relationship
   stale, and because sessions use `expire_on_commit=False`, that stale `None`
   survived the commit and was serialised — a freshly assigned task reported no
   assignee. Fixed by assigning the relationship instead of the foreign key.
3. Two of my own test assertions were wrong rather than the code: a `release`
   also moves *to* `ASSIGNED`, so counting `ASSIGNED`-bound changes did not
   isolate reassignment; and an unassigned task filed by a manager is invisible
   to a worker, so 404 is right there, not 403.
4. Config tests read the developer's `.env`, so putting working SMTP credentials
   there broke four tests asserting defaults. Found by trying it before writing
   the setup instructions; fixed with `Settings(_env_file=None)`.

I list these because "the tests pass" is weak evidence alone. The seed invariant
tests exist because the seed duplicates service logic, and they earned their
place on the first run.

**What I would do differently without AI.** It would have taken considerably
longer and I would have written less: fewer tests, a shorter README, probably no
invariant tests over the demo data. I suspect I would also have shipped the
strictly forward-only state machine without noticing how much work the phrase
"without a good reason" was doing — reading a brief that closely is easier when
drafting is cheap.

I was most careful with generated output where it was security-relevant. The
404-versus-403 split, the scoped `total`, and the identical wording between
hidden and missing responses are all places where plausible-looking code leaks
information, so each has a test asserting the boundary rather than the happy
path.
