# The case lifecycle

| # | Stage | What happens | Gate (computed, never ticked by hand) |
|---|---|---|---|
| 1 | Intake | Case, property identifiers, parties; or a verified referral converted | property identified and ≥1 party |
| 2 | Evidence | Upload → store (SHA-256) → extract pages → classify → facts with page + snippet + confidence → PII counts | ≥1 document extracted |
| 3 | Ownership | Deeds, deaths, wills, probate, tax rolls → dated events → chain checks | ≥1 ownership event |
| 4 | Financial | Obligations derived from tax bills/liens; payments imported; reconciliation run | ≥1 reconciliation run |
| 5 | Discrepancies | Detection across identifiers, chain, owners, decedents, money | detection has run |
| 6 | Review | Acknowledge/resolve findings; confirm/correct/reject facts; accept/reject proposed matches | no open critical/high finding |
| 7 | Resolution | Tasks, comments, decisions; evidence requests and closing as approved actions | ≥1 decision and no open tasks |
| 8 | History | Resolved/closed; hash-chained audit; Markdown case report | case resolved or closed |

Stages 1–6 advance directly (one at a time, gate required, audited). Entering resolution and
resolving or closing a case are actions that execute only after an attorney approves them by
typing `approve action N`. `tests/integration/test_end_to_end.py` walks every stage through the API.
