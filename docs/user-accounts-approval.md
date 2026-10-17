# Личные кабинеты: свободная регистрация с одобрением администратора

Статус: подготовка, НЕ подключено к production. Ветка: feature/user-accounts-approval.

## Принцип
Публичный POST /auth/register создаёт аккаунт в статусе pending. Регистрация НЕ выдаёт рабочую сессию. Администратор в отдельном защищённом интерфейсе одобряет или отклоняет заявку. Только approved + active может получить доступ к API закупок. Нельзя защищать лишь HTML: авторизация нужна на уровне всех API, WebSocket, выдачи ODT и оригиналов ЕИС.

## Схема данных (отдельная PostgreSQL, без миграции существующей /data/eis223.db)
users(id UUID PK, email CITEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, status TEXT CHECK IN ('pending','approved','rejected','blocked'), role TEXT CHECK IN ('admin','user'), email_verified_at TIMESTAMPTZ, approved_by UUID, approved_at TIMESTAMPTZ, created_at TIMESTAMPTZ, last_login_at TIMESTAMPTZ);
user_sessions(id UUID PK, user_id UUID REFERENCES users, token_hash TEXT UNIQUE, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ);
user_procurements(user_id UUID REFERENCES users, reg_number TEXT, added_at TIMESTAMPTZ, PRIMARY KEY(user_id,reg_number));
user_thematic_batches(id UUID PK, user_id UUID REFERENCES users, batch_id TEXT, created_at TIMESTAMPTZ, UNIQUE(user_id,batch_id));
user_memos(id UUID PK, user_id UUID REFERENCES users, reg_number TEXT, storage_key TEXT, created_at TIMESTAMPTZ);
auth_audit(id BIGSERIAL PK, actor_id UUID, subject_id UUID, action TEXT, created_at TIMESTAMPTZ, metadata JSONB);

## API
POST /auth/register (email, password): normalize email, Argon2id password hash, rate-limit, anti-enumeration, pending status, no access token.
POST /auth/login: approved + verified + not blocked; generic failure for other states; session cookie Secure HttpOnly SameSite=Lax, CSRF protection for mutations.
POST /auth/logout: revoke session.
GET /auth/me: account status and role only.
GET /admin/users?status=pending: admin only.
POST /admin/users/{id}/approve: admin only, atomic status transition + audit record.
POST /admin/users/{id}/reject: admin only, audit record.
POST /admin/users/{id}/block: admin only, revoke all sessions.
GET/POST/DELETE /me/procurements: current-user scoped list, linking to shared purchase registry without duplicating EIS originals.
GET /me/thematic-batches and /me/memos: current-user ownership check for every read/write/download.

## Security & deployment gates
- Bootstrap first admin from explicit one-time command or secret, NEVER public registration or a hardcoded email. No default password.
- Enforce authorization on ALL legacy /api/* and download routes before enabling public signup; fail closed if auth store unavailable.
- Separate machine-to-machine collector credentials and scopes from interactive users; bridge and health endpoints must be individually classified.
- Restrict admin actions server-side; never trust role/user_id in request JSON or frontend.
- Verify email ownership before approval. Add rate limits, account enumeration protection, password reset, CSRF, HTTPS-only cookies, and session rotation.
- Shared verified EIS facts are read-only for normal users; per-user review decisions and ODT documents remain private.
- No new audit findings or counters created by user signup; no duplication of originals.
- Existing /data volume is nearly full. Use a separate managed PostgreSQL for identity, do not write new user data into the full SQLite volume.
- Feature flag AUTH_ENFORCE defaults OFF until endpoint inventory, migration, admin bootstrap, and tests pass. Never open registration while existing API remains unprotected.

## Acceptance tests
1. Pending user cannot read any /api/ purchase, original, memo, thematic queue, or other user's resources.
2. Anonymous requests to protected APIs fail 401; non-admin approval fails 403.
3. Approved user can access only own queues and memos; cannot change another user's IDs.
4. Blocking user immediately invalidates active sessions.
5. EIS bridge continues with scoped machine credentials and no browser login.
6. Existing purchase, original, violation and checked-document counts remain unchanged.
7. Disk-full /data does not break registration store and cannot trigger deletion of evidence.
8. Existing dashboard routes are covered by middleware, including legacy aliases and direct file URLs.

## Rollout
A. Inventory routes and distinguish public, user, admin and collector endpoints.
B. Add isolated identity database, schema, password hashing and session tests.
C. Add admin approval panel and personal dashboards behind a flag.
D. Protect every legacy route and direct download; test horizontal access control.
E. Bootstrap admin and deploy with AUTH_ENFORCE disabled, verify health.
F. Enable enforcement and registration only after test suite and admin access are verified.
