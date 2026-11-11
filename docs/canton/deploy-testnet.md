# Deploying to Canton TestNet

**Status: not deployed. Access not yet granted.** TestNet participation requires a formal application, committee
approval and an authorised Super Validator sponsor. None of that exists for this project yet; nothing may be
labelled TestNet until a real TestNet transaction has been verified.

## Prerequisites (external)

1. Submit the TestNet network-access application (see <https://guide.canton.foundation/>).
2. Obtain committee approval and a Super Validator sponsor.
3. Provision **separate** TestNet infrastructure: its own validator(s), its own static egress IP(s), its own IdP
   clients and secrets. Nothing is reused from DevNet.
4. Get the TestNet egress IP(s) allowlisted; receive the sponsor-issued one-time onboarding secret.
5. Deploy the validator with the TestNet-compatible release; confirm the protocol and SDK versions; pin the SDK in
   both `daml.yaml` files and rebuild.

## Groundwork configuration

`infra/canton/testnet/groundwork.env.example` → fill, store secrets as `groundwork-canton-testnet-*`, then
`infra/canton/configure-cloudrun.sh testnet …` against a **separate** Cloud Run service (or a separate revision
tag) from DevNet. Upload and verify the DAR on both participants, allocate parties and users, bind the
organisations, schedule `canton-sync`.

## Verification

Run the same CASE-101 workflow as DevNet (deploy-devnet.md §4) and record: synchronizer id, package id, both
party ids, the request update id, the decision update id and contract id — read independently from each
participant. Only then switch any public material to "TestNet".
