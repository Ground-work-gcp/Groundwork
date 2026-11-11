"""Canton Network integration: one authenticated client per participant, never shared between organisations.

* ``auth``         — ledger API tokens: OIDC client credentials (DevNet/TestNet/MainNet) or a LocalNet HMAC secret
* ``client``       — JSON Ledger API v2 client (submit-and-wait, active contracts, update stream, ledger end)
* ``participants`` — the Legal Aid and Registry participants, built from configuration, isolated by role

The React app never talks to a participant. Every ledger call goes through these clients, and a
command for a party is only ever submitted through that party's own participant and credentials.
"""
