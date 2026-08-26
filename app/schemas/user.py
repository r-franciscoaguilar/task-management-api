"""Response shapes for users.

Users are read-only over this API: accounts are managed elsewhere, so there is
no create/update/delete surface here. These endpoints exist so a client (or a
reviewer with curl) can discover who exists and which ids to act as.
"""

from pydantic import BaseModel, ConfigDict

from app.models import UserRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: UserRole
