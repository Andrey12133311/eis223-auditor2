# Isolated identity staging — deployment checklist

This is **not** the EIS production startup and must not be attached to the existing `web` service.

## Infrastructure

1. Create a **separate staging environment** and PostgreSQL database; confirm billing and retention before provisioning.
2. Configure `EIS223_IDENTITY_DATABASE_URL` as a **private** PostgreSQL URL.
3. Configure `EIS223_PUBLIC_ORIGIN` to the **exact HTTPS staging origin**.
4. Configure a real SMTP sender: `EIS223_SMTP_HOST`, `EIS223_SMTP_PORT=465`, `EIS223_SMTP_USER`, `EIS223_SMTP_PASSWORD`, `EIS223_SMTP_FROM`; verify domain SPF/DKIM/DMARC and actual inbox delivery. Configure `EIS223_SMTP_HOST`, `EIS223_SMTP_PORT=465`, `EIS223_SMTP_USER`, `EIS223_SMTP_PASSWORD`, `EIS223_SMTP_FROM` for outbound confirmation messages. Set `EIS223_RATE_LIMIT_SECRET` to a separately generated random secret of at least 32 characters, identical across staging workers. Install `requirements-accounts.txt`.
5. Run a one-time schema migration against staging PostgreSQL using `scripts.account_migrations_v1.initialize_schema`; do not use the production SQLite volume.
6. Run `python -m scripts.account_bootstrap_v1` in a secure interactive operator terminal to create the first admin.
7. Set `EIS223_AUTH_ENFORCE=true` before making the staging service public.
8. Start with `uvicorn scripts.account_staging_app:application --host 0.0.0.0 --port $PORT`.
9. Verify registration -> delivery to inbox -> one-time email confirmation -> pending -> admin approval -> login -> private procurement list -> logout; verify rejected and blocked users cannot log in.
10. Verify anonymous access to private endpoints is rejected, including WebSocket and document downloads.

## Stop conditions before production merge

- Shared PostgreSQL rate limiting is implemented for registration/login, but real multi-worker concurrency and trusted proxy-IP behavior are not yet verified.
- Legacy EIS endpoints have not been audited for ownership checks; global authentication is not sufficient for per-user isolation.
- EIS collectors need independent, narrowly scoped machine authentication; do not grant collector access using ordinary user cookies.
- Document and ODT download authorization has not been verified.
- Email verification code is present but real SMTP delivery, resend/recovery, session cleanup, logging/redaction, and DB backup/restore still require testing/implementation.
- GitHub CI status and real PostgreSQL integration tests must be green.
- Do not merge PR #29 or enable production registration before all conditions are satisfied.

## Existing service

Keep `scripts/railway_start_v282.py` and production environment unchanged. Never set `EIS223_AUTH_ENFORCE=true` on the existing service before completing the compatibility review.


## Mail.ru sender for isolated staging

Use dedicated sender `eisauditor@mail.ru` with `EIS223_SMTP_HOST=smtp.mail.ru`, `EIS223_SMTP_PORT=465`, `EIS223_SMTP_USER=eisauditor@mail.ru`, `EIS223_SMTP_FROM=eisauditor@mail.ru`. Set `EIS223_SMTP_PASSWORD` only through protected Railway variables using a Mail.ru application-specific password; do not commit credentials. Users may register with addresses at any mail provider; email ownership requires clicking the verification link. Real SMTP delivery is unverified until staging credentials and a separate recipient mailbox are configured.
