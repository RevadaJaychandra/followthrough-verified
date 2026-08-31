"""Escalation email tool. Gated behind human approval in the API layer —
this function itself just sends; the approval check happens before it's called.
"""
import smtplib
from email.mime.text import MIMEText

import config


def send_escalation_email(recipient: str, subject: str, body: str) -> dict:
    """Send an escalation email. Only call this after explicit human approval
    has been granted for this specific action — this is a sensitive,
    external-facing action.

    Args:
        recipient: Email address to send to.
        subject: Email subject line.
        body: Email body text.
    """
    if config.OFFLINE_GMAIL:
        print(f"[offline] would send email to {recipient}: {subject}")
        return {"status": "success", "offline": True}

    if not config.GMAIL_SENDER or not config.GMAIL_APP_PASSWORD:
        return {"status": "error", "message": "Gmail credentials not configured"}

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = config.GMAIL_SENDER
        msg["To"] = recipient

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.GMAIL_SENDER, config.GMAIL_APP_PASSWORD)
            server.sendmail(config.GMAIL_SENDER, [recipient], msg.as_string())

        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
