# Isolated thematic audit worker (staging only)

The account worker is opt-in and **must not be deployed as the production web start command**.

## Prerequisites
- A separate staging PostgreSQL database migrated with `scripts.account_migrations_v1`.
- A separate worker process with `requirements-accounts.txt`.
- A trusted adapter implemented as a Python function `audit(owner_id, batch_id, reg_number) -> bool` inside a `scripts.*` module.
- The adapter must use the EIS connector and real documents, apply legal checks, and durably persist evidence-backed findings scoped to the owner and batch **before** returning `True`. It must return `False` or raise if evidence is unavailable or persistence fails. No adapter is provided yet.
- The callback must never treat missing documents as confirmed violations.
- The current callback signature does not include the attempt number; an adapter that persists findings must look up and fence the active attempt via `save_findings`. Do not enable the worker until this integration is tested.

## Example separate-process configuration

```sh
export EIS223_WORKER_ENABLED=true
export EIS223_IDENTITY_DATABASE_URL='postgresql://...'
export EIS223_AUDIT_ADAPTER='scripts.your_verified_eis_adapter:audit'
python -m scripts.account_worker_service_v1
```

Never commit credentials. Do not use `claim_next` or another low-level database primitive as an audit adapter: such a function does not perform an audit. The service refuses to start unless explicitly enabled, a database URL exists, and an adapter is configured.

## Status

The queue, claims, bounded stale-job recovery, and finding storage exist. **Real EIS ingestion, the legal analysis adapter, end-to-end verification, and ODT memo generation are not yet connected.** Do not claim completed procurement audits from this staging module until those stages are integrated and tested.
