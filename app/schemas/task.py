"""Request and response shapes for tasks."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import TaskStatus
from app.schemas.assignment_event import AssignmentEventOut
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
        """A length check alone would allow a whitespace-only title."""
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

    Creator and assignee are nested rather than bare ids -- a queue needs names,
    and both are already eager loaded. Fields are explicit so a new model column
    cannot leak into a response.
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


class TaskDetailOut(TaskOut):
    """A single task with its most recent assignment.

    Detail only: on lists this would load history per row -- an N+1 per page.
    """

    latest_assignment: AssignmentEventOut | None = None


class AssignRequest(BaseModel):
    assignee_id: int = Field(description="Id of the worker to make responsible.")


class ReleaseRequest(BaseModel):
    """Handing work back requires saying why.

    Checked here as well as in the service, so a client gets a clean 422.
    """

    reason: str = Field(
        max_length=500,
        description="Why the work cannot be continued right now.",
    )

    @field_validator("reason")
    @classmethod
    def _reason_must_have_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped
