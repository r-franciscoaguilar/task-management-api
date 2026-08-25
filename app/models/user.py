import enum
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UtcDateTime, utcnow
from app.db import Base


class UserRole(str, enum.Enum):
    MANAGER = "MANAGER"
    WORKER = "WORKER"


class User(Base):
    """A person who already exists in the organization.

    Authentication lives elsewhere (see the README): the API trusts an
    X-User-Id header to identify the caller, and this row supplies the role
    that authorization is based on.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole, native_enum=False))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role.value}>"
