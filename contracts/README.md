# Groundwork approval contracts (DAML)

* `groundwork-approvals/` — the production package (no daml-script dependency): `ApprovalTypes`, `OwnershipApproval`.
* `groundwork-approvals-test/` — Daml Script tests against the built DAR.

```bash
docker run --rm -v "$PWD":/work -w /work digitalasset/daml-sdk:3.4.0-rc2 \
  sh -c 'export PATH=/home/daml/.daml/bin:$PATH; daml build --all && cd groundwork-approvals-test && daml test'
```

With DPM installed: `dpm build --all` and `(cd groundwork-approvals-test && dpm test)`. Design: docs/canton/architecture.md.
