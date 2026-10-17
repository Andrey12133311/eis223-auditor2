# Isolated identity staging — deployment checklist

This staging app is separate from the production EIS web service. Do not replace the production startup or database.

## Registration policy

- Registration is open; each new account starts with status `pending`.
- An administrator must approve the account before login succeeds.
- No email messages, confirmation links, or SMTP sender are required. The email address is validated for syntax and domain mail deliverability, **not** verified as belonging to the registrant.
- Do not describe this as proof that the mailbox exists or that the applicant controls it.

## Isolated staging setup

1. Obtain approval for any new infrastructure cost; provision a separate environment and PostgreSQL database.
2. Set `EIS223_IDENTITY_DATABASE_URL` to the staging PostgreSQL connection string using protected variables.
3. Set `EIS223_PUBLIC_ORIGIN` to the exact public HTTPS origin, and `EIS223_AUTH_ENFORCE=true`.
4. Generate a distinct `EIS223_RATE_LIMIT_SECRET` (at least 32 random characters), shared by staging workers. Install `requirements-accounts.txt`.
5. Initialize schema once with `scripts.account_migrations_v1.initialize_schema`; alternatively set `EIS223_IDENTITY_INIT_SCHEMA=true` only for initial staging setup. Never run this against the production SQLite volume.
6. Bootstrap the first administrator in a secure operator terminal with `python -m scripts.account_bootstrap_v1`.
7. Start `uvicorn scripts.account_staging_app:application --host 0.0.0.0 --port $PORT`.
8. Test registration → pending status → admin approval → login → private lists → logout. Test rejection and blocking, unauthorized access, and isolation between two distinct accounts.
9. Confirm CSRF/origin checks, cookie security, HTTPS proxy configuration, rate limits, and private data cache headers.
10. Verify database backups and recovery before accepting real user data.

## Known limitations / production stop conditions

- The personal thematic queue endpoint creates an owner-bound placeholder only. It does **not** start EIS processing. The legacy thematic engine is not connected.
- Bookmarking a legacy queue identifier does not establish ownership of that engine's data. Do not expose shared queue results based solely on bookmarks.
- Private memo download currently returns HTTP 501 for owned documents pending a secure storage adapter.
- End-to-end PostgreSQL tests, real multi-worker throttling, and trusted proxy behavior still need validation.
- Legacy EIS APIs and collectors require separate ownership and machine-authentication review.
- Do not merge the account feature branch or enable production registration until isolation, download authorization, and deployment tests are complete.

Keep `scripts/railway_start_v282.py` and the production Railway environment unchanged.
