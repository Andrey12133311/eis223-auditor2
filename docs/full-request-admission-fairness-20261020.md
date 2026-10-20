# Bounded admission fairness for request-owned originals

## Reproduction

The existing original-recovery bridge and the public/INN discovery drain share the automatic queue ceiling, `MAX_QUEUE - RESERVED_MANUAL_SLOTS` (350 - 8 = 342). Discovery has a durable alternating allocation between its two sources, but the originals bridge previously had no place in that allocation.

A deterministic local fixture starts at that ceiling with durable discovery candidates, then repeats the real terminal `report` → worker `claim`/candidate drain → due originals `pump` sequence. On the parent implementation, each of 12 released places goes to discovery; zero request-owned originals enter. This proves a permitted starvation schedule. It does not imply that every interleaving starves originals or that any live queue is completely blocked.

## Change

- A freshly probed eligible original that encounters full automatic capacity can register one request-owned admission turn, only when automatic discovery candidates also wait.
- The existing drain leaves at most one automatic place free while that turn is valid. It continues its existing discovery/INN alternating split over every remaining place.
- The original bridge still takes its physical execution lock, probes current local receipts, checks the frozen request and durable engine target, checks source-specific identity and terminal policy, and inserts through the existing queue.
- A successful contested originals admission yields to a real discovery/INN admission before taking another contested turn. The same fairness rule applies if the bridge happens to run before the next drain. Binding an already-existing job does not count as a new automatic admission; an empty automatic backlog yields immediately.
- A hold lasts at most 90 seconds (the existing 60-second capacity delay plus a 30-second pump interval). Repeated capacity observations, drain calls and process initialization do not renew it. After expiry, waiting automatic work gets its turn before a new hold.
- Disabled or changed request state, a mismatched durable target, an ineligible waiter, the four-owned-job cap, a competing existing job, or an empty automatic backlog releases a hold. A fresh present/catalogue-unavailable/terminal observation also releases it. A losing peer's execution-busy observation cannot revoke the actual physical lock owner's existing turn; it can neither create a hold nor extend its original expiry. There is no file, source or JSON parsing work in admission arbitration under a database writer.

The guarantee is a bounded queue-admission opportunity for an enabled, eligible bridge that continues its existing local turns. It does not guarantee successful remote retrieval or analysis completion. Expired or unavailable work never holds a place indefinitely.

## Storage and rollback

The only schema addition is `full_recheck_originals_turn`, an operational singleton containing the request, waiting registry, engine signature/generation, hold expiry and automatic-yield flag. Creation is idempotent. Existing queue, receipt, source, evidence and request-member schemas remain unchanged. Old code ignores this table and can continue all its old queue operations; rollback loses the fairness improvement without changing stored jobs, claims, reports or source identities. A later compatible startup does not extend a saved hold.

No queue row is cleared, dropped or reset. The total queue cap, eight manual places, two-new-jobs-per-pump limit, four-active-originals limit, downloader leases, same-job retries and permanent-terminal policy are unchanged. This admission change does not alter the request epoch, cohort or legal engine signature. The combined price-provenance correction independently advances the same request to generation 4. There are no new workers, source polls, authentication changes or external connections.

## Local regression coverage

- Repeated real report → claim/drain → pump: four originals, four public-discovery and four INN-derived admissions over 12 completions; active automatic jobs never exceed 342.
- Real discovery and successful INN-report drain entry points honor a live hold.
- Reverse scheduling order still gives automatic work its turn.
- Non-renewal across repeated pressure and restart; idle expiry; disabled/current-target/durable-target mismatch; completed or execution-busy member; existing ownership cap.
- Fresh source change uses the new fingerprint; active jobs remain idempotent; permanent terminal results never receive another attempt.
- Manual places remain usable, and manual-only pressure does not create a hold.
- Separate-process bridge and drainers serialize under the existing SQLite writer and admit exactly one original into one free place.
- A rolling peer that loses the execution lock, including with an intervening drainer, cannot revoke the actual probe owner's held opportunity. Repeated busy observations never extend the hold; an abandoned physical owner yields at the original expiry.
- Old queue admission remains compatible with the additional table; the arbitration path performs no filesystem or JSON work under its writer.
