#!/usr/bin/env bash
# Add the Canton settings for one network to the existing Cloud Run service (does not touch anything else).
# usage: infra/canton/configure-cloudrun.sh <devnet|testnet|mainnet> <env-file> [service] [region]
set -euo pipefail
NET="$1"; ENV_FILE="$2"; SERVICE="${3:-groundwork}"; REGION="${4:-us-central1}"
case "$NET" in devnet|testnet|mainnet) ;; *) echo "localnet is not deployed to Cloud Run" >&2; exit 1;; esac
grep -q "<" "$ENV_FILE" && { echo "$ENV_FILE still has <placeholders>" >&2; exit 1; }
VARS=$(grep -vE '^\s*(#|$)' "$ENV_FILE" | grep -v SECRET | paste -sd, -)
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "$VARS" \
  --update-secrets "CANTON_LEGAL_AID_OIDC_CLIENT_SECRET=groundwork-canton-$NET-legal-aid-client-secret:latest,CANTON_REGISTRY_OIDC_CLIENT_SECRET=groundwork-canton-$NET-registry-client-secret:latest"
echo "Schedule the sync job (every minute) — Cloud Run allocates CPU only during requests:"
echo "  gcloud scheduler jobs create http groundwork-canton-sync --schedule='* * * * *' --uri=https://<service>/api/v1/ops/jobs/canton-sync --http-method=POST --oidc-service-account-email=<scheduler SA> --headers=x-groundwork-signature=<hmac of empty body>"
echo "or run it as a Cloud Run Job: python -m app.jobs canton-sync"
