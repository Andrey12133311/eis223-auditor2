# Isolated thematic audit worker (staging only)

The account worker is opt-in and **must not be deployed as the production web start command**.

## Prerequisites
- A separate staging PostgreSQL database migrated with `scripts.account_migrations_v1`.
- A separate worker process with `requirements-accounts.txt`.
- A trusted adapter implemented as a Python function `audit(owner_id, batch_id, reg_number, attempts) -> bool` inside a `scripts.*` module.
- The adapter must use the EIS connector and real documents, apply legal checks, and durably persist evidence-backed findings scoped to the owner and batch **before** returning `True`. It must return `False` or raise if evidence is unavailable or persistence fails. An initial adapter is available in `scripts.account_eis_adapter_v1:audit`; it still requires staging integration tests against live EIS and the database.
- The callback must never treat missing documents as confirmed violations.
- The callback receives the claimed attempt number and must use it to fence result persistence. Do not enable the worker until this integration is tested.

## Example separate-process configuration

```sh
export EIS223_WORKER_ENABLED=true
export EIS223_IDENTITY_DATABASE_URL='postgresql://...'
export EIS223_AUDIT_ADAPTER='scripts.account_eis_adapter_v1:audit'
python -m scripts.account_worker_service_v1
```

Never commit credentials. Do not use `claim_next` or another low-level database primitive as an audit adapter: such a function does not perform an audit. The service refuses to start unless explicitly enabled, a database URL exists, and an adapter is configured.

## Status

The queue, claims, bounded stale-job recovery, and finding storage exist. **An initial EIS analysis adapter is connected in code, but live EIS ingestion and end-to-end verification have not been confirmed; ODT memo generation remains unconnected.** Do not claim completed procurement audits from this staging module until those stages are integrated and tested.
