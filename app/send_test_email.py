"""Send one test email using the configured settings.

Run this before touching the API. It isolates "are my SMTP settings correct"
from "does the application work", which otherwise get debugged together and
waste time -- a FAILED notification tells you delivery broke, not why.

    python -m app.send_test_email you@example.com
"""

import argparse
import sys

from app.core.config import get_settings
from app.services.notifications import (
    EmailMessage,
    NotificationSendError,
    build_sender,
)


def _describe(settings) -> str:
    lines = [
        f"  backend        {settings.email_backend}",
        f"  host:port      {settings.smtp_host}:{settings.smtp_port}",
        f"  from           {settings.smtp_from_address}",
        f"  STARTTLS       {settings.smtp_use_tls}",
        f"  timeout        {settings.smtp_timeout}s",
    ]
    if settings.smtp_username:
        # Never print the password; confirming it is *present* is what matters.
        lines.append(f"  username       {settings.smtp_username}")
        lines.append(
            f"  password       {'set' if settings.smtp_password else 'MISSING'}"
        )
    else:
        lines.append("  auth           none (no username set)")
    if settings.notify_override_address:
        lines.append(f"  redirecting to {settings.notify_override_address}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a single test email with the current configuration."
    )
    parser.add_argument("address", help="Where to send it.")
    args = parser.parse_args()

    settings = get_settings()
    print("Configuration in use:")
    print(_describe(settings))

    if settings.email_backend == "noop":
        print(
            "\nEMAIL_BACKEND is 'noop', so nothing will be delivered. "
            "Set EMAIL_BACKEND=smtp in .env to send for real."
        )
        return 1

    message = EmailMessage(
        to=args.address,
        subject="Task Management API — test message",
        body=(
            "This is a test message from the Task Management API.\n\n"
            "If you are reading it, SMTP delivery is configured correctly and "
            "assignment notifications will reach their assignee."
        ),
    )

    print(f"\nSending to {args.address} ...")
    try:
        build_sender(settings).send(message)
    except NotificationSendError as error:
        print(f"FAILED: {error}")
        print("\nCommon causes:")
        print(
            "  550 / 'domain not allowed' -- the provider will not accept your\n"
            "      SMTP_FROM_ADDRESS. Production senders require a verified\n"
            "      sending domain; a sandbox or catcher accepts anything.\n"
            "  535 / 'authentication failed' -- wrong SMTP_USERNAME or\n"
            "      SMTP_PASSWORD, or credentials from a different product than\n"
            "      the host you are pointing at.\n"
            "  Connection refused -- nothing is listening. Start a local\n"
            "      catcher: python -m aiosmtpd -n -l localhost:1025\n"
            "  Timeout or handshake error -- wrong port or TLS mode. Use port\n"
            "      587 with SMTP_USE_TLS=true; implicit TLS on 465 is not\n"
            "      supported."
        )
        return 1

    print("Sent. The mail server accepted the message.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
