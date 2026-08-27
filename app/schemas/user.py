"""Response shapes for users. Read-only: accounts are managed elsewhere."""

from pydantic import BaseModel, ConfigDict

from app.models import UserRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: UserRole
