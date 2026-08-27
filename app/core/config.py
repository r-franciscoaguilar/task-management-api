from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./app.db"

    # "smtp" actually opens a socket and delivers. "noop" performs no I/O and
    # is only for situations where email must be switched off deliberately --
    # the default is real delivery, because the brief requires a real message.
    email_backend: Literal["smtp", "noop"] = "smtp"

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_address: str = "noreply@task-api.local"
    smtp_use_tls: bool = False
    # Without a timeout an unresponsive mail server would hang the request
    # thread indefinitely, since delivery is synchronous.
    smtp_timeout: float = 10.0

    # When set, every notification goes to this address instead of the real
    # assignee, with the intended recipient preserved in the subject line. Two
    # uses: testing delivery against your own inbox without editing seed data,
    # and making it impossible for a staging environment to email real people.
    notify_override_address: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
