"""Mock-only email delivery verification. No real external emails sent."""
from unittest.mock import Mock, patch
import os
from scripts import account_notifications_v1 as mail


def run():
    resend = {
        "EIS223_RESEND_API_KEY": "re_test_secret",
        "EIS223_RESEND_FROM": "EIS Auditor <noreply@example.org>",
    }
    with patch.dict(os.environ, resend, clear=True):
        assert mail.email_provider() == "resend"
        result = Mock(status_code=200)
        result.json.return_value = {"id": "accepted-id"}
        with patch("httpx.post", return_value=result) as request:
            mail.send_email("user@example.org", "Найдено нарушение", "Закупка 32615969056", 42)
        info = request.call_args.kwargs
        assert info["headers"]["Idempotency-Key"] == "eis223-outbox-42"
        assert info["json"]["from"] == resend["EIS223_RESEND_FROM"]
        assert info["json"]["to"] == ["user@example.org"]
        assert info["json"]["text"] == "Закупка 32615969056"
        result.status_code = 403
        with patch("httpx.post", return_value=result):
            try:
                mail.send_email("user@example.org", "Test", "Test", 42)
            except RuntimeError as exc:
                assert "HTTP 403" in str(exc)
                assert "re_test_secret" not in str(exc)
            else:
                raise AssertionError("An HTTP 403 must not count as delivered")
    with patch.dict(os.environ, {}, clear=True):
        assert mail.email_provider() is None
    smtp = {
        "EIS223_SMTP_HOST": "smtp.example.org",
        "EIS223_SMTP_USER": "sender",
        "EIS223_SMTP_PASSWORD": "fake",
        "EIS223_SMTP_FROM": "noreply@example.org",
    }
    with patch.dict(os.environ, smtp, clear=True):
        assert mail.email_provider() == "smtp"
        with patch.object(mail, "send_smtp") as method:
            mail.send_email("recipient@example.org", "Тема", "Текст", 41)
            method.assert_called_once()
    print("V374_EMAIL_DELIVERY_SELFTEST_OK https=True stable_key=True safe_status=True smtp_fallback=True")


if __name__ == "__main__":
    run()
