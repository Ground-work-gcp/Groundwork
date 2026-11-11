#!/usr/bin/env bash
# Start Groundwork LocalNet: a real Canton 3.4 network with two participants in one container.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../../.." && pwd)"
IMAGE="${DAML_SDK_IMAGE:-digitalasset/daml-sdk:3.4.0-rc2}"
mkdir -p "$HERE/state"
[ -f "$HERE/localnet.env" ] || { printf "LEGALAID_JWT_SECRET=%s\nREGISTRY_JWT_SECRET=%s\nLEGALAID_ADMIN_TOKEN=%s\nREGISTRY_ADMIN_TOKEN=%s\n" "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" > "$HERE/localnet.env"; chmod 600 "$HERE/localnet.env"; }
docker rm -f groundwork-canton >/dev/null 2>&1 || true
docker run -d --name groundwork-canton --env-file "$HERE/localnet.env" \
  -p 127.0.0.1:5011:5011 -p 127.0.0.1:7011:7011 -p 127.0.0.1:5021:5021 -p 127.0.0.1:7021:7021 \
  -v "$ROOT":/work:ro -v "$HERE/state":/state --user root "$IMAGE" \
  java -Xmx2g -jar /home/daml/.daml/sdk/3.4.0-rc2/canton/canton.jar daemon \
  -c /work/infra/canton/localnet/localnet.conf --bootstrap /work/infra/canton/localnet/bootstrap.canton >/dev/null
echo "waiting for the synchronizer and both participants…"
for _ in $(seq 1 120); do
  if docker logs groundwork-canton 2>&1 | grep -q GROUNDWORK_LOCALNET_READY; then docker logs groundwork-canton 2>&1 | grep GROUNDWORK_LOCALNET_READY; cat "$HERE/state/localnet.json"; echo; exit 0; fi
  if ! docker ps -q -f name=groundwork-canton | grep -q .; then docker logs groundwork-canton 2>&1 | tail -40; exit 1; fi
  sleep 3
done
docker logs groundwork-canton 2>&1 | tail -40; exit 1
