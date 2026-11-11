# Canton integration — security

## Party-level authorisation
DAML enforces who may do what: the request is signed by legal aid and observed by the registry; only the
registry controls the decision choices; only legal aid can cancel; either may close an expired request. Canton
checks these signatures on every transaction — on LocalNet a Legal-Aid-authorised `ApproveOwnership` was refused
with `DAML_AUTHORIZATION_ERROR`, independently of Groundwork.

## User-to-party mapping
A party is an institution. Groundwork maps an **organisation** to a configured party through
`canton_party_bindings`, which only a platform admin can set and which must equal the configured party id.
Users never choose a party. Within the organisation, Groundwork's own roles and the two-person rule apply:
a paralegal proposes, a *different* attorney/admin approves the internal action before anything is submitted;
on the registry side only attorney/admin users decide. The individual who acted is recorded in each
organisation's hash-chained audit log alongside the ledger update id.

## Credentials and secrets
* One credential per participant, used only by that participant's client. The Legal Aid client never holds
  the registry credential's value and vice versa; LocalNet uses different HMAC secrets, so tokens are not
  interchangeable (verified: 401).
* Production: OIDC client credentials per participant from the operator's IdP; secrets in Secret Manager,
  injected into Cloud Run as env secrets; tokens fetched server-side, cached until shortly before expiry,
  never logged, never sent to the browser. HMAC (`*_SECRET_REF`) is refused in production.
* **Demonstration arrangement:** when one Groundwork deployment holds both credentials (LocalNet, or a hosted
  demo), it is two isolated clients in one process, not independent institutional custody. In a real deployment
  the registry operates its own participant, IdP and approval interface; only the Legal Aid block is configured
  in the Legal Aid organisation's Groundwork.

## Transport
Self-hosted participants: TLS on the Ledger/JSON API (custom CA via `CANTON_CA_BUNDLE`), admin APIs bound to
localhost/private networks only, Ledger API reachable only from the Groundwork service's egress (VPC connector +
firewall), static egress IP for participant ↔ synchronizer traffic. Production refuses non-https ledger URLs.

## Data minimisation and commitments
Only opaque random references, categories, codes and commitments go on the ledger (see architecture.md).
`commitment = "sha256:" + SHA256(salt ‖ canonical_json(manifest))` with a fresh 128-bit salt per commitment,
so the hash cannot be confirmed by guessing the manifest. The contract's `ensure` rejects any commitment field
that is not exactly `sha256:` + 64 hex characters. Salts and manifests stay in Groundwork's database and are
shown only to the requesting and reviewing organisations.

## Failure handling
Errors are classified (auth, timeout, unavailable, rate-limited, duplicate, stale contract, rejected) and only
sanitised messages with a correlation reference are stored or shown. An outage, a timeout, a reset or a stale
contract never produces an approval or rejection: outcomes come only from committed decision contracts seen in
the Legal Aid participant's own stream (or active contract set) carrying the proposal's commitments.

## Known limitations
* LocalNet runs participants with in-memory storage and HMAC tokens — development only.
* `3.4.0-rc2` is a release candidate; pin the network-supported SDK before DevNet.
* The registry's view of the manifest is served by the Legal Aid organisation's Groundwork; for full
  independence the registry should receive the manifest over its own channel and run its own front end.
