# Registration acceptance tests

- Registration creates a pending account, without issuing a session.
- Only an approved account may sign in.
- Administrator can approve or reject pending accounts.
- Pending and blocked accounts cannot access procurement APIs or documents.
- User A cannot read or modify User B's private queues and memos.
- Shared verified EIS originals are not duplicated.
- Existing evidence, SQLite database and counters remain unchanged.
- Public registration must not be enabled until server-side access control protects all legacy routes.
