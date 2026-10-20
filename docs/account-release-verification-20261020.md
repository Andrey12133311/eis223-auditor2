# Account release verification (2026-10-20)

The integrated production branch 8fa39044 contains both clarification/fact-write repairs (#103) and dashboard single-flight recovery (#104). Its full Account module tests run 39483083665 exposed seven assertion failures and four errors. These are not evidence that all these runtime features are broken: the existing tests still used the removed persistent cookie, expected obsolete entry-form redirects/UI controls, treated an async memo endpoint as synchronous, and supplied a pre-catalog-query migration stub.

This change updates the tests to execute the current contracts without changing production authentication or evidence processing:
- The real current session cookie must open protected pages after login; it has Secure/HttpOnly/SameSite=Strict and no persistent expiry attributes. Logout must revoke its server-side digest and reject replay. Expired/blocked/pending cases now send the actual current cookie instead of accidentally testing an unrelated absent cookie. Legacy cookies remain rejected.
- Explicit login/register requests remain visible as required by the current gate, without doing a session lookup or revoking a valid session. The same session still opens the protected dashboard and cabinet.
- Memo denial is awaited and still requires owner-scoped 404, never a storage path.
- Migration catalog reads have a real fetchone-compatible stub; single-statement DDL, conditional old-constraint upgrades and no destructive row operations are tested.
- Personal summaries exercise all four owner-bound counts. The current thematic history UI is checked separately from retained owner-scoped legacy queue APIs.

The old Live V257 probe followed an anonymous redirect to the login page and then falsely required private dashboard markers in it. The replacement performs only bounded GET requests, follows no redirects, sends no cookies/tokens, requires explicit 401 on protected APIs and reports authenticated_dashboard_verified=false. An anonymously readable dashboard fails this probe. Live probing is manual, not repeated against the old production image for every source PR; five network-free contract tests validate it in CI.

Before merging, require the complete Python 3.12 Account module tests plus all applicable PR checks. Local changed suites passed 49 account tests and five probe tests; this is not a runtime throughput measurement. No production queue, original, result, counter or account policy is changed here. A new production deployment, its authenticated page response and real recheck progress must be verified separately; Railway was still pausing new deployments during preparation.
