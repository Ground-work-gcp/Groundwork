# Canton multi-party approvals — architecture

## Why Canton

An ownership resolution is decided by **two independent institutions**: the legal-aid organisation that
investigated the case and the registry that changes the record. Groundwork's internal two-person approval
proves that two people *inside one organisation* agreed; it cannot prove what another organisation decided.
Canton gives each organisation its own participant node and its own party, and DAML makes the rules of the
exchange part of the shared contract:

* only the registry party can decide, and Canton's authorisation check (not Groundwork) refuses anything else —
  a Legal-Aid-signed `ApproveOwnership` was refused on LocalNet with `DAML_AUTHORIZATION_ERROR`;
* a decision consumes the request, so there is never a second terminal decision;
* each side holds its own copy of the decision, reads it from its own participant, and can prove it later;
* Canton's sub-transaction privacy means unrelated parties never see the contracts at all.

Groundwork stays the system of record for the investigation (evidence, facts, findings, money, workflow).
Canton is authoritative only for the cross-organisation decision. PostgreSQL is Groundwork's read projection
of it. Cloud Storage keeps the original documents; Gemini keeps assisting with evidence. No part of this
replaces legal verification or professional judgement.

## Participants and parties

```
                  ┌────────────── Canton synchronizer (sequencer + mediator) ──────────────┐
                  │                                                                         │
     Legal Aid participant                                                      Registry participant
     party LegalAidParty                                                        party RegistryParty
     JSON Ledger API  ◄── JWT signed for Legal Aid only                         JSON Ledger API ◄── its own JWT
            ▲                                                                           ▲
            │ legal_aid client                                                          │ registry client
   ┌────────┴───────────────────────── Groundwork backend (FastAPI) ───────────────────┴───────┐
   │ external_approvals domain · outbox · update-stream sync per participant · projection      │
   └──────────────▲──────────────────────────────────────────────────────────▲──────────────────┘
                  │ session cookie (Legal Aid tenant)                         │ session cookie (Registry tenant)
            React: case → External approvals                           React: Registry portal
```

* **LocalNet** (`infra/canton/localnet`): a real Canton 3.4 network in one container — one synchronizer and
  two participants, each with its own Ledger/JSON API port and **its own HS256 secret**, so a Legal Aid token
  is rejected (401) by the Registry participant.
* **DevNet / TestNet / MainNet**: each organisation's participant is a validator node (self-hosted or
  node-as-a-service) connected to the Global Synchronizer; tokens come from each operator's IdP (OIDC).
* The React app never talks to a participant. Organisations are mapped to parties by a platform admin
  (`POST /api/v1/admin/canton/bindings`), validated against the configured party ids; users cannot pick a party.

## Contract lifecycle (`contracts/groundwork-approvals`)

| Template | Signatory | Observer | Purpose |
|---|---|---|---|
| `OwnershipApprovalRequest` | legal aid | registry | the proposal: opaque ids, action, case version, evidence & payload commitments, expiry |
| `OwnershipApprovalDecision` | registry | legal aid | the terminal decision: status, reason code, requested evidence, ledger-time timestamp, commitments |
| `OwnershipApprovalClosed` | legal aid | registry | a request closed without a decision (cancelled or expired) |

Choices on the request — all **consuming**:

| Choice | Controller | Checks |
|---|---|---|
| `ApproveOwnership` | registry | not expired (ledger time); the reviewer echoes both commitments and they must match |
| `RejectOwnership` | registry | as above; a reason other than `EvidenceSufficient` |
| `RequestAdditionalEvidence` | registry | as above; at least one `EvidenceItem` |
| `CancelRequest` | legal aid | — |
| `MarkExpired` | legal aid or registry | ledger time ≥ `expiresAt` (expiry needs a transaction; nothing archives itself) |

`ensure` refuses malformed proposals: commitments must be `sha256:` + 64 hex (so free text — a name, a
document — cannot be smuggled into those fields), the two parties must differ, expiry must follow creation.
The **commitment echo** is the notable design choice: the registry must state which evidence it reviewed,
so a decision about a different version of the evidence than the one proposed is impossible on the ledger.

Tests: `contracts/groundwork-approvals-test` (8 Daml Script tests — creation, visibility to the registry
and invisibility to outsiders, approve, reject, request changes, registry-only decisions, outsider refused,
second decision refused, commitment mismatch, expiry, cancellation, malformed inputs).

## Backend integration

* `app/infrastructure/canton/` — `auth` (HMAC for LocalNet, OIDC client credentials elsewhere), `client`
  (JSON Ledger API v2: `submit-and-wait-for-transaction`, `state/active-contracts`, `updates`, `state/ledger-end`;
  shapes taken from the participant's own `/docs/openapi`), `errors` (classified, sanitised), `participants`
  (one client per role, built from configuration, cached separately).
* `app/domain/external_approvals/` — proposal, manifest and commitments; the **outbox**; the **update-stream
  synchroniser**; reconciliation; the projection into the case.
* The internal two-person rule is preserved: a proposal is a `WorkflowAction` (`external.submit_to_registry`)
  that a *different* attorney/admin must approve by typing the phrase before anything reaches the ledger.
  On the registry side only attorney/admin users of the bound registry organisation can decide; the human who
  clicked is recorded in Groundwork's audit chain (a DAML party is an institution, not a person).

### Reliable messaging and synchronisation

1. In one database transaction: status change + outbox row with a deterministic command id
   `gw:<network>:<proposal>:<command>`.
2. The outbox submits through the right participant. `submit-and-wait-for-transaction` returns only after
   commit; a retry of the same command id is deduplicated by the participant (`DUPLICATE_COMMAND` ⇒ done).
   Transient errors back off (5 s × 2ⁿ, max 10 min, 8 attempts); terminal ones mark the approval
   `submission_failed` with a sanitised reason.
3. Each participant's update stream is read from a persisted offset (`canton_cursors`, keyed by network,
   role and party). Events are idempotent (`external_approval_events` unique on approval, kind, update id).
4. **Status changes only on committed updates seen by the right participant**: `awaiting_review` when the
   Registry participant's stream shows the request; `approved/rejected/changes_requested` only when the
   *Legal Aid* participant's stream shows the decision — and only if the decision carries this proposal's
   commitments.
5. Offset ahead of the ledger end ⇒ reset detected ⇒ cursor reset and reconciliation against the active
   contract set; approvals whose contracts vanished become `sync_delayed`, never approved. An unreachable
   participant records an error on in-flight approvals; it is never a decision.

The loop runs as a daemon thread (`CANTON_BACKGROUND_SYNC`), as the `canton-sync` job
(`python -m app.jobs canton-sync` / `POST /api/v1/ops/jobs/canton-sync`, signed), or on demand
(`POST /api/v1/external-approvals/sync`).

## Privacy model

On the ledger: proposal id and case reference (random, unlinkable to Groundwork ids), action type, case
version, two salted SHA-256 commitments, reason codes, evidence *categories*, timestamps, party ids.
Off the ledger: the evidence manifest (document hashes, file names, reviewed facts), the salts, the rationale,
the names. The manifest and salts are shown only to the two organisations in the workflow, so the registry
can recompute the commitment (the portal shows ✓/✗ and blocks approval on a mismatch).
