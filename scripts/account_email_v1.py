"""Verified email challenge and SMTP delivery; never log plaintext tokens."""
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import quote
from uuid import uuid4
from scripts.account_security_v1 import digest_session_token


def create_challenge(db, user_id):
    token = secrets.token_urlsafe(36)
    db.execute(
        """INSERT INTO account_email_challenges(id,user_id,token_digest,expires_at)
           VALUES (%s,%s,%s,%s)""",
        (uuid4(), user_id, digest_session_token(token), datetime.now(timezone.utc)+timedelta(hours=24)),
    )
    return token


def verify_challenge(db, token):
    if not isinstance(token, str) or len(token) < 40:
        return False
    row = db.execute(
        """UPDATE account_email_challenges SET consumed_at=CURRENT_TIMESTAMP
           WHERE token_digest=%s AND consumed_at IS NULL
           AND expires_at>CURRENT_TIMESTAMP RETURNING user_id""",
        (digest_session_token(token),),
    ).fetchone()
    if not row:
        return False
    db.execute(
        """UPDATE account_users SET email_verified_at=CURRENT_TIMESTAMP
           WHERE id=%s AND email_verified_at IS NULL""", (row[0],),
    )
    return True


def send_verification_email(address, token):
    host = os.environ["EIS223_SMTP_HOST"]
    port = int(os.environ.get("EIS223_SMTP_PORT", "465"))
    username = os.environ["EIS223_SMTP_USER"]
    password = os.environ["EIS223_SMTP_PASSWORD"]
    sender = os.environ["EIS223_SMTP_FROM"]
    origin = os.environ["EIS223_PUBLIC_ORIGIN"].rstrip("/")
    if not origin.startswith("https://") or port != 465:
        raise RuntimeError("Verification mail requires HTTPS origin and SMTP TLS port 465")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = address
    message["Subject"] = "Подтвердите электронную почту — ЕИС Аудитор"
    message.set_content(
        "Для подтверждения регистрации откройте ссылку в течение 24 часов:\n"
        + origin + "/auth/verify-email?token=" + quote(token, safe="")
        + "\nЕсли вы не регистрировались, проигнорируйте письмо."
    )
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)
