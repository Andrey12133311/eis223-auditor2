# Complete buyer names

The verified-original parser previously stopped a buyer name at the first line.
A line containing only `Государственное бюджетное учреждение` was accepted as
complete, so both the main list and the detailed card faithfully displayed an
incomplete stored value. This is independent of the quoted-initial capitalization
formatter.

The parser now joins bounded, explicitly quoted continuations, including numbered
DOCX table cells. A unique full-name completion can replace a legal-form-only or
unfinished-quote prefix only from existing verified original-document processing.
Conflicting names, confirmed identities, and INNs do not authorize a substitution.
The existing bounded background reconciliation includes these incomplete records;
no network source, audit findings, document state, or user ownership is changed.
A missing original remains unresolved. Names over 400 characters are accepted up
to a defensive bound of 2000 and displayed without truncation.

The redundant employee-workspace footer link is removed only from the personal
cabinet's manual-checks block. The employee workspace and primary navigation remain.

## Checks

- New completion regressions cover wrapped prose, numbered and labeled cells,
  four quote pairs, long names, candidate/INN conflicts, sweep eligibility,
  idempotent repair, and preservation of unrelated fields/findings.
- Main dashboard and detailed-card tests preserve full long names and escape HTML.
- Cabinet regression preserves employee navigation and other cabinet links.
- New completion tests run in the Docker build and throughput CI workflow.
- Full local pytest comparison against pristine f45de7d had the same 17 existing
  failures (accounts/legacy contracts), with no newly failing tests. Targeted tests
  and production build gates are checked separately.
