# Canton troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `GENERIC_CONFIG_ERROR … http-ledger-api.address: Unknown key` | JSON API config shape differs between Canton versions | 3.4 uses `http-ledger-api.server { address, port }` |
| bootstrap fails with `PERMISSION_DENIED … administrative services` | Ledger API auth is on and the console token lacks the admin claim | `admin-token-config { fixed-admin-token = …, admin-claim = true }` (console only) |
| `401` from a participant | token signed for another participant, expired, or wrong user | each participant has its own secret/IdP; check `CANTON_<ROLE>_*`; HMAC tokens live 5 min |
| `403 PERMISSION_DENIED` on submit | ledger user lacks `actAs` for the party | grant rights to the user on that participant |
| `DAML_AUTHORIZATION_ERROR` | the submitting party is not a controller/signatory | expected for forged decisions; otherwise check the role → client mapping |
| `CONTRACT_NOT_FOUND` / stale contract | the request was already decided, cancelled, expired — or the network was reset | Groundwork marks `sync_delayed`; reconcile (`POST /api/v1/external-approvals/sync`) |
| approval stuck in *Submitted* | the Registry participant's stream has not been read | check `GET /api/v1/canton/status`; run the sync job; confirm the registry party is hosted and the DAR uploaded on the registry participant |
| stuck in *Registry decision committed — syncing* | Legal Aid participant unreachable | the error is shown on the approval; it resolves on the next successful sync |
| `PACKAGE_NOT_FOUND` / unknown template | DAR not uploaded on that participant, or package name differs | upload the DAR to both participants; `CANTON_PACKAGE_NAME=groundwork-approvals` |
| party not found on the other side | topology not yet propagated | `up.sh` waits for both parties; on DevNet allow a few seconds after allocation |
| `database is locked` (local SQLite) | background sync and requests writing at once | SQLite busy-timeout is 30 s; use PostgreSQL for anything beyond a laptop |
| SDK / protocol mismatch on upload | DAR compiled for an LF/protocol the network does not support | pin `sdk-version` to the network's SDK and rebuild |
