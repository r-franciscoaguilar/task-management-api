from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models
from app.db import Base, SessionLocal, engine
from app.exceptions import register_exception_handlers
from app.routers import tasks, users
from app.seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # No Alembic: this is a greenfield single-file SQLite service with no
    # deployment in scope, so create_all is enough. See the README for why,
    # and what would change before production.
    Base.metadata.create_all(bind=engine)

    # Seeding on boot is a deliberate operability choice: `uvicorn app.main:app`
    # alone leaves a reviewer with data to exercise, no extra step to discover.
    # It is a no-op once any user exists, so restarts never duplicate or clobber.
    with SessionLocal() as session:
        seed_if_empty(session)

    yield


app = FastAPI(title="Task Management API", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(users.router)
app.include_router(tasks.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
