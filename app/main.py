from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models
from app.db import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # No Alembic: this is a greenfield single-file SQLite service with no
    # deployment in scope, so create_all is enough. See the README for why,
    # and what would change before production.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Task Management API", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
