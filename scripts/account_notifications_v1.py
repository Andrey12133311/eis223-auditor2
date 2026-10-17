"""Durable registration-address email alerts for access and thematic findings.

All enqueue operations use the caller's PostgreSQL transaction.  Messages
remain queued if an email provider is unavailable and are retried; never
send from a web request or include original procurement evidence in an email.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)

SMTP_FIELDS = ("EIS223_SMTP_HOST", "EIS223_SMTP_USER",
               "EIS223_SMTP_PASSWORD", "EIS223_SMTP_FROM")


def smtp_configured():
    return all(os.getenv(k, "").strip() for k in SMTP_FIELDS)


def resend_configured():
    return bool(os.getenv("EIS223_RESEND_API_KEY", "").strip()
                and os.getenv("EIS223_RESEND_FROM", "").strip())


def email_provider():
    """HTTPS APIs work on Railway Hobby; SMTP requires Railway Pro."""
    if resend_configured():
        return "resend"
    if smtp_configured():
        return "smtp"
    return None


def send_resend(address, subject, body, message_id):
    """Send with an outbox-scoped stable idempotency key via Resend HTTPS."""
    if not resend_configured():
        raise RuntimeError("Resend mail is not configured")
    import httpx
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": "Bearer " + os.environ["EIS223_RESEND_API_KEY"],
                "Content-Type": "application/json",
                "Idempotency-Key": "eis223-outbox-" + str(message_id),
            },
            json={
                "from": os.environ["EIS223_RESEND_FROM"],
                "to": [address],
                "subject": subject,
                "text": body,
            },
            timeout=12.0,
        )
    except httpx.RequestError as exc:
        raise OSError("Mail HTTPS provider temporarily unavailable") from exc
    if not 200 <= response.status_code < 300:
        raise RuntimeError("Mail HTTPS provider returned HTTP " + str(response.status_code))
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Mail HTTPS provider returned invalid JSON") from exc
    if not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError("Mail HTTPS provider did not acknowledge receipt")


def send_email(address, subject, body, message_id):
    provider = email_provider()
    if provider == "resend":
        return send_resend(address, subject, body, message_id)
    if provider == "smtp":
        return send_smtp(address, subject, body)
    raise RuntimeError("Mail provider is not configured")


def enqueue(db, user_id, event_key, subject, body, batch_id=None):
    """Idempotently queue for an approved account; never trust client-supplied mail."""
    if not event_key or len(event_key) > 220:
        raise ValueError("Invalid notification key")
    db.execute(
        """INSERT INTO account_mail_outbox
           (user_id,event_key,subject,body,batch_id)
           SELECT id,%s,%s,%s,%s FROM account_users
           WHERE id=%s AND status='approved'
           ON CONFLICT (user_id,event_key) DO NOTHING""",
        (event_key, subject[:200], body[:10000], batch_id, user_id),
    )


def enqueue_access(db, user_id, event, role="user"):
    if event == "approved":
        subject = "ЕИС Аудитор — доступ предоставлен"
        body = ("Ваша регистрация одобрена. Теперь вы можете войти "
                "в ЕИС Аудитор, используя электронную почту и пароль.")
        key = "access:approved"
    else:
        raise ValueError("Unknown access event")
    origin = os.getenv("EIS223_PUBLIC_ORIGIN", "").rstrip("/")
    if origin.startswith("https://"):
        body += "\n\nВойти: " + origin + "/account/login"
    enqueue(db, user_id, key, subject, body)


def enqueue_role_change(db, user_id, role, event_id):
    if role not in ("manager", "user", "admin"):
        raise ValueError("Invalid role")
    if not event_id:
        raise ValueError("Role change requires its audit id")
    caption = {"manager": "руководителя", "user": "сотрудника",
               "admin": "администратора"}[role]
    body = "Ваши права в ЕИС Аудитор изменены. Установлена роль: " + caption + "."
    origin = os.getenv("EIS223_PUBLIC_ORIGIN", "").rstrip("/")
    if origin.startswith("https://"):
        body += "\n\nВойти: " + origin + "/account/login"
    enqueue(db, user_id, "access:role:" + str(event_id),
            "ЕИС Аудитор — изменены права доступа", body)


def send_smtp(address, subject, body):
    """Send one UTF-8 plain-text message over TLS; credentials never get logged."""
    if not smtp_configured():
        raise RuntimeError("SMTP is not configured")
    host = os.environ["EIS223_SMTP_HOST"]
    port = int(os.getenv("EIS223_SMTP_PORT", "465"))
    if port != 465:
        raise RuntimeError("SMTP SSL port 465 is required")
    message = EmailMessage()
    message["From"] = os.environ["EIS223_SMTP_FROM"]
    message["To"] = address
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(),
                          timeout=12) as smtp:
        smtp.login(os.environ["EIS223_SMTP_USER"],
                   os.environ["EIS223_SMTP_PASSWORD"])
        smtp.send_message(message)


def dispatch_pending(connect, limit=3):
    """Dispatch with SKIP LOCKED to coordinate replicas and retry failures.

    Resend receives a stable per-message Idempotency-Key across retries.
    Database unique keys prevent repeated event notifications.
    """
    if not email_provider():
        return 0
    sent = 0
    with connect() as db:
        rows = db.execute(
            """SELECT m.id,u.email,m.subject,m.body
               FROM account_mail_outbox m
               JOIN account_users u ON u.id=m.user_id
               WHERE m.sent_at IS NULL AND m.next_attempt_at <= CURRENT_TIMESTAMP
                 AND m.attempts < 12 AND u.status='approved'
                 AND (m.batch_id IS NULL OR EXISTS (
                      SELECT 1 FROM account_thematic_batches b
                      WHERE b.user_id=m.user_id AND b.batch_id=m.batch_id))
               ORDER BY m.id
               FOR UPDATE OF m SKIP LOCKED LIMIT %s""",
            (max(1, min(int(limit), 10)),),
        ).fetchall()
        for identifier, address, subject, body in rows:
            try:
                send_email(address, subject, body, identifier)
            except (OSError, smtplib.SMTPException, RuntimeError, ValueError) as exc:
                # Only the exception type, never credentials or message bodies.
                log.warning("Mail send failed: %s", type(exc).__name__)
                db.execute(
                    """UPDATE account_mail_outbox
                       SET attempts=attempts+1,
                           last_error=%s,
                           next_attempt_at=CURRENT_TIMESTAMP +
                             (LEAST(360, POWER(2, LEAST(attempts+1, 8))::INT)
                              * INTERVAL '1 minute')
                       WHERE id=%s""",
                    (type(exc).__name__, identifier),
                )
            else:
                db.execute(
                    """UPDATE account_mail_outbox
                       SET sent_at=CURRENT_TIMESTAMP,attempts=attempts+1,
                           last_error=NULL WHERE id=%s""",
                    (identifier,),
                )
                sent += 1
    return sent
