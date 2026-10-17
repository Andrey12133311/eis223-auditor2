# Isolated identity staging — deployment checklist

This is **not** the EIS production startup and must not be attached to the existing `web` service.

## Infrastructure

1. Create a **separate staging environment** and PostgreSQL database; confirm billing and retention before provisioning.
2. Configure `EIS223_IDENTITY_DATABASE_URL` as a **private** PostgreSQL URL.
3. Configure `EIS223_PUBLIC_ORIGIN` to the **exact HTTPS staging origin**.
4. Install `requirements-accounts.txt`.
5. Run a one-time schema migration against staging PostgreSQL using `scripts.account_migrations_v1.initialize_schema`; do not use the production SQLite volume.
6. Run `python -m scripts.account_bootstrap_v1` in a secure interactive operator terminal to create the first admin.
7. Set `EIS223_AUTH_ENFORCE=true` before making the staging service public.
8. Start with `uvicorn scripts.account_staging_app:application --host 0.0.0.0 --port $PORT`.
9. Verify registration -> pending -> admin approval -> login -> private procurement list -> logout; verify rejected and blocked users cannot log in.
10. Verify anonymous access to private endpoints is rejected, including WebSocket and document downloads.

## Stop conditions before production merge

- Per-IP and per-account **distributed rate limiting** is not implemented.
- Legacy EIS endpoints have not been audited for ownership checks; global authentication is not sufficient for per-user isolation.
- EIS collectors need independent, narrowly scoped machine authentication; do not grant collector access using ordinary user cookies.
- Document and ODT download authorization has not been verified.
- Automated email verification/recovery, session cleanup, logging/redaction, and DB backup/restore need implementation and testing.
- GitHub CI status and real PostgreSQL integration tests must be green.
- Do not merge PR #29 or enable production registration before all conditions are satisfied.

## Existing service

Keep `scripts/railway_start_v282.py` and production environment unchanged. Never set `EIS223_AUTH_ENFORCE=true` on the existing service before completing the compatibility review.
