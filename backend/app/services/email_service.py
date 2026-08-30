"""Sends transactional email via AWS SES (SESv2 SendEmail).

Separate from Cognito's own emails (signup confirmation codes, password
reset codes), which are Cognito-internal and can't carry an arbitrary
custom message like an org invite.
"""

from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError

from app.core.aws_clients import get_ses_client
from app.core.config import settings


class EmailSendError(Exception):
    """Raised when SES fails to accept an email for delivery."""


def send_invite_email(to_email: str, org_slug: str, invite_url: str) -> None:
    client = get_ses_client()
    subject = f"You're invited to join {org_slug}"
    text_body = (
        f"You've been invited to join the '{org_slug}' organization.\n\n"
        f"Click the link below to create your account:\n{invite_url}\n\n"
        "This link expires in 7 days."
    )
    html_body = (
        f"<p>You've been invited to join the <strong>{org_slug}</strong> organization.</p>"
        f'<p><a href="{invite_url}">Click here to create your account</a></p>'
        "<p>This link expires in 7 days.</p>"
    )

    try:
        client.send_email(
            FromEmailAddress=settings.ses_sender_email,
            Destination={"ToAddresses": [to_email]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": text_body},
                        "Html": {"Data": html_body},
                    },
                }
            },
        )
    except client.exceptions.MessageRejected as exc:
        # The common sandbox-mode failure: recipient isn't a verified
        # identity. Surfaced with SES's own message so the admin UI can show
        # a specific, actionable reason rather than a generic failure.
        raise EmailSendError(str(exc)) from exc
    except (ClientError, BotoCoreError) as exc:
        raise EmailSendError(f"Failed to send invite email: {exc}") from exc


def send_admin_notification_email(to_email: str, subject: str, message: str) -> None:
    """Sends a plain notification email — used by the admin co-pilot agent
    (app/services/copilot_service.py) when it finds a specific, real issue
    worth flagging. The recipient is always the calling admin's own email,
    enforced by the caller, never a value this function or the LLM chooses.
    """
    client = get_ses_client()
    html_body = f"<p>{message}</p>"

    try:
        client.send_email(
            FromEmailAddress=settings.ses_sender_email,
            Destination={"ToAddresses": [to_email]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": message},
                        "Html": {"Data": html_body},
                    },
                }
            },
        )
    except client.exceptions.MessageRejected as exc:
        raise EmailSendError(str(exc)) from exc
    except (ClientError, BotoCoreError) as exc:
        raise EmailSendError(f"Failed to send notification email: {exc}") from exc
