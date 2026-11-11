# Deploying to Canton DevNet

**Status: not deployed.** DevNet requires onboarding steps that cannot be done from this repository
(an allowlisted static egress IP, an onboarding secret from a Super Validator sponsor, and a running validator).
Everything on the Groundwork side is ready; the remaining steps are below. Do not label anything "DevNet" in
the UI until a real DevNet transaction has been observed (the network badge shows `GROUNDWORK_CANTON_NETWORK`).

## 1 · Decide how each organisation hosts its participant

* **Self-hosted validator** (each organisation runs a Splice validator: participant + validator app) on
  infrastructure *separate from* the Groundwork Cloud Run service — e.g. a GKE cluster or VM per organisation
  with a reserved static external IP used for all egress to the Global Synchronizer.
* **Node-as-a-Service** from an approved provider: the provider supplies the Ledger/JSON API endpoint, the party
  and OIDC client credentials.

For a genuine multi-party demo the registry's participant should belong to a different operator. If, for a
demonstration, one team runs both, document it as such (see security.md).

## 2 · Onboarding (per organisation, per network)

1. Read the current requirements at <https://guide.canton.foundation/> and <https://docs.canton.network/>; note the
   DevNet protocol and SDK versions, the sponsor process and the egress-IP allowlisting process.
2. Reserve the static egress IP; submit it for DevNet allowlisting through the official process; wait for confirmation.
3. Obtain the DevNet onboarding secret through your Super Validator sponsor (DevNet secrets are short-lived).
4. Deploy the validator per the official instructions for that release, with Ledger API auth bound to your IdP
   (an audience for the Ledger API, and a client-credentials client for Groundwork).
5. Allocate the party (`LegalAidParty` or `RegistryParty`) on the validator and a ledger user with `actAs` and
   `readAs` for it only.
6. Pin the SDK to the DevNet-supported version, rebuild, and upload `groundwork-approvals-1.0.0.dar` to **both**
   participants (`POST /v2/dars` with an admin token, or your validator's package upload). Record the package id.

## 3 · Configure Groundwork

Copy `infra/canton/devnet/groundwork.env.example`, fill every `<placeholder>` from your onboarding (never invent
an endpoint or synchronizer id — read the synchronizer id from `GET /v2/state/connected-synchronizers`), put the
client secrets in Secret Manager as `groundwork-canton-devnet-legal-aid-client-secret` and
`…-registry-client-secret`, then:

```bash
infra/canton/configure-cloudrun.sh devnet infra/canton/devnet/groundwork.env <service> <region>
```

Production refuses to start with `localnet`, non-https ledger URLs, HMAC secrets or missing OIDC settings.
Bind the organisations (`POST /api/v1/admin/canton/bindings` as a platform admin) and schedule `canton-sync`.

## 4 · Verify

1. `GET /api/v1/canton/status` → both participants `ok`.
2. Run `scripts/canton_demo.py --base https://<service>` (or the UI flow): proposal → second approver →
   *Submitted* with a request update id.
3. The registry officer approves from the portal (registry participant's credentials).
4. The case shows *Approved* only after the Legal Aid participant's stream delivers the decision; record the
   decision update id and confirm it on the Legal Aid participant with `POST /v2/updates/update-by-id`.

DevNet is reset periodically: expect new party ids and a fresh ledger. Groundwork keys cursors by party,
detects offsets ahead of the ledger end, reconciles, and marks orphaned approvals `sync_delayed`; re-run
onboarding steps 5–6, update the party ids, re-bind, and resubmit.
