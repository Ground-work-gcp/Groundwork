# LocalNet setup

A real Canton 3.4 network (one synchronizer, two participants) and Groundwork connected to it. Verified in
this repository on 2026-11-11; transaction references from that run are in the final report and in
`infra/canton/localnet/state/last-e2e.json` after you run the live test.

## Prerequisites

Docker, Python 3.12+, Node 20. Java and the Daml SDK run inside the `digitalasset/daml-sdk` image, so nothing
else is installed on the host.

> **SDK version.** `daml.yaml` pins `3.4.0-rc2`, the newest 3.x SDK image published on Docker Hub. Before
> DevNet, check the SDK/protocol version the Global Synchronizer runs (docs.canton.network → network status),
> change `sdk-version` in both `daml.yaml` files and `DAML_SDK_IMAGE`, and rebuild. With the DPM toolchain the
> same commands are `dpm build` / `dpm test`; this repository calls `daml build` / `daml test` inside the image.

## 1 · Build and test the contracts

```bash
cd contracts
docker run --rm -v "$PWD":/work -w /work digitalasset/daml-sdk:3.4.0-rc2 \
  sh -c 'export PATH=/home/daml/.daml/bin:$PATH; daml build --all && cd groundwork-approvals-test && daml test'
```

Expect `Created .daml/dist/groundwork-approvals-1.0.0.dar` and eight `ok` lines.

## 2 · Start LocalNet

```bash
infra/canton/localnet/up.sh
```

It generates `localnet.env` (two JWT secrets and two console admin tokens, mode 600, git-ignored), starts
`groundwork-canton` with ports bound to 127.0.0.1 (Ledger API 5011/5021, JSON API 7011/7021), bootstraps the
synchronizer `groundwork-localnet`, connects both participants, uploads the DAR to each, allocates
`LegalAidParty` on the Legal Aid participant and `RegistryParty` on the Registry participant, creates one ledger
user per participant with rights only for its own party, waits until each sees the other's party, and writes
`state/localnet.json`. Admin APIs stay inside the container.

## 3 · Run Groundwork against it

```bash
cd backend
source <(../infra/canton/localnet/groundwork.env.sh)      # exports the CANTON_* settings for this LocalNet
alembic upgrade head
python -m uvicorn app.main:app --port 8000
```

## 4 · Prove it end to end

```bash
GROUNDWORK_CANTON_LIVE=1 python -m pytest tests/canton_live -v     # CASE-101 through the API, real ledger
DATABASE_URL=sqlite:///var/groundwork.db python ../scripts/canton_demo.py --stop-before-decision
```

Then sign in as `ravi` / `demo-password-1` → **Registry**, check the ✓ commitment line and press **Approve**;
sign in as `lana` → the case → **External approvals**: the status turns *Approved* once the Legal Aid
participant's stream shows the decision, with the decision update id and contract id.

Stop: `docker rm -f groundwork-canton` (state is in memory; the next `up.sh` is a fresh ledger with new party
ids — Groundwork's cursors are keyed by party, so it starts cleanly).
