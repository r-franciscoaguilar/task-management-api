"""Request and response shapes for tasks."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import TaskStatus
from app.schemas.user import UserOut


class TaskCreate(BaseModel):
    title: str = Field(
        max_length=200, description="Short summary of the work to be done."
    )
    description: str | None = Field(
        default=None, description="Any further detail; optional."
    )

    @field_validator("title")
    @classmethod
    def _title_must_have_content(cls, value: str) -> str:
        """Reject whitespace-only titles, which a length check alone allows."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def _blank_description_is_absent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class TaskOut(BaseModel):
    """A task as clients see it.

    Creator and assignee are nested rather than exposed as bare ids: a client
    rendering a queue needs names, and both relationships are already eager
    loaded, so this costs no extra queries. Fields are listed explicitly so a
    new model column can never leak into a response unnoticed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: TaskStatus

    creator: UserOut
    assignee: UserOut | None

    created_at: datetime
    updated_at: datetime
    assigned_at: datetime | None
    completed_at: datetime | None
