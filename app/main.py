import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models
from app.db import Base, SessionLocal, engine
from app.exceptions import register_exception_handlers
from app.core.config import get_settings
from app.routers import tasks, users
from app.seed import seed_if_empty

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # No Alembic: greenfield, single SQLite file, no deployment in scope.
    Base.metadata.create_all(bind=engine)

    # Seeding on boot means `uvicorn` alone gives a reviewer usable data. It is
    # a no-op once any user exists, so restarts never duplicate.
    with SessionLocal() as session:
        seed_if_empty(session)

    # Loud on purpose: an unnoticed redirect makes notifications look delivered
    # while nobody who should have received one did.
    settings = get_settings()
    if settings.notify_override_address:
        logger.warning(
            "NOTIFY_OVERRIDE_ADDRESS is set: all notifications will go to %s "
            "instead of their real assignee.",
            settings.notify_override_address,
        )

    yield


app = FastAPI(title="Task Management API", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(users.router)
app.include_router(tasks.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
