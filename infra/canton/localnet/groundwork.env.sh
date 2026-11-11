#!/usr/bin/env bash
# Print the Groundwork backend settings for the running LocalNet (source it: `source <(infra/canton/localnet/groundwork.env.sh)`).
HERE="$(cd "$(dirname "$0")" && pwd)"
set -a; . "$HERE/localnet.env"; set +a
python3 - "$HERE/state/localnet.json" <<'PY'
import json, os, sys
st = json.load(open(sys.argv[1]))
for k, v in {
    "GROUNDWORK_CANTON_ENABLED": "true", "GROUNDWORK_CANTON_NETWORK": "localnet", "CANTON_API_MODE": "json",
    "CANTON_LEGAL_AID_LEDGER_URL": "http://127.0.0.1:7011", "CANTON_LEGAL_AID_PARTY_ID": st["legal_aid_party"],
    "CANTON_LEGAL_AID_USER_ID": "groundwork-legalaid", "CANTON_LEGAL_AID_SECRET_REF": os.environ["LEGALAID_JWT_SECRET"],
    "CANTON_REGISTRY_LEDGER_URL": "http://127.0.0.1:7021", "CANTON_REGISTRY_PARTY_ID": st["registry_party"],
    "CANTON_REGISTRY_USER_ID": "groundwork-registry", "CANTON_REGISTRY_SECRET_REF": os.environ["REGISTRY_JWT_SECRET"],
    "CANTON_SYNCHRONIZER_ID": st["synchronizer_id"],
}.items():
    print(f"export {k}='{v}'")
PY
