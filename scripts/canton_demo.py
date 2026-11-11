"""CASE-101 end to end over Canton, through a running Groundwork server (no mocks).

usage:  python scripts/canton_demo.py [--base http://127.0.0.1:8000] [--stop-before-decision]

Creates two tenants — "Groundwork Legal Aid" (legal_aid party) and "Registry Authority" (registry
party) — binds them (the first user is made platform admin directly in the database the server uses,
so run it against your own LocalNet deployment only), uploads the synthetic evidence, has a person
review the facts, proposes the ownership resolution, has a second person approve the internal action,
and — unless --stop-before-decision — has the registry officer approve from the registry portal.
Every ledger reference printed comes from the server's projection of the real participants. The
script refuses to continue if the server reports Canton disabled or a participant unreachable.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

PASSWORD = "demo-password-1"
WEB = {"x-groundwork-client": "web"}
DOCS = {
    "case101-deed-1998.txt": "WARRANTY DEED\nGrantor: Mohan Lal\nGrantee: Raj Kumar\nParcel No.: 22-104-0009\nRecorded: 03/14/1998\nProperty address: 12 Banyan Lane",
    "case101-death-certificate-raj.txt": "CERTIFICATE OF DEATH\nDecedent: Raj Kumar\nDate of death: 08/02/2021\nParcel No.: 22-104-0009",
    "case101-inheritance-order.txt": "LETTERS OF ADMINISTRATION\nDecedent: Raj Kumar\nHeir: Priya Kumar\nDate: 01/10/2022\nParcel No.: 22-104-0009",
}


def check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        sys.exit(f"{r.request.method} {r.request.url} → {r.status_code}: {r.text}")
    return r.json() if r.content else {}


def login(base: str, username: str, org: str, role_users: list[tuple[str, str, str]]) -> tuple[httpx.Client, dict, dict[str, httpx.Client]]:
    c = httpx.Client(base_url=base, headers=WEB, timeout=60)
    r = c.post("/auth/sign-in", json={"username": username, "password": PASSWORD})
    if r.status_code != 200:
        r = c.post("/auth/sign-up", json={"organization": org, "username": username, "full_name": username.title(), "email": f"{username}@example.org", "password": PASSWORD})
        check(r)
        for u, name, role in role_users:
            check(c.post("/settings/users", json={"username": u, "full_name": name, "email": f"{u}@example.org", "password": PASSWORD, "role": role}))
    me = check(c.get("/auth/me"))["user"]
    others = {}
    for u, _, _ in role_users:
        o = httpx.Client(base_url=base, headers=WEB, timeout=60)
        check(o.post("/auth/sign-in", json={"username": u, "password": PASSWORD}))
        others[u] = o
    return c, me, others


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--stop-before-decision", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/") + "/api/v1"

    legal, legal_me, legal_users = login(base, "lana", "Groundwork Legal Aid", [("arun", "Arun Attorney", "attorney")])
    registry, reg_me, reg_users = login(base, "rita", "Registry Authority", [("ravi", "Ravi Registrar", "attorney")])
    if not legal_me.get("is_platform_admin"):
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            sys.exit("set DATABASE_URL to the server's database so the demo can make 'lana' a platform admin (LocalNet only)")
        from sqlalchemy import create_engine, text

        with create_engine(url).begin() as conn:
            conn.execute(text("UPDATE users SET is_platform_admin = true WHERE username = 'lana'"))
    status = check(legal.get("/canton/status"))
    if not status["enabled"]:
        sys.exit("the server reports Canton disabled — nothing would be real; aborting")
    check(legal.post("/admin/canton/bindings", json={"organization_id": legal_me["organization_id"], "role": "legal_aid"}))
    check(legal.post("/admin/canton/bindings", json={"organization_id": reg_me["organization_id"], "role": "registry"}))
    status = check(legal.get("/canton/status"))
    bad = [k for k, v in status["participants"].items() if not v.get("ok")]
    if bad:
        sys.exit(f"participant(s) unreachable: {bad} — aborting rather than pretending")
    print(f"Canton {status['network'].upper()} · participants reachable: {', '.join(status['participants'])}")

    case = check(legal.post("/cases", json={"title": "CASE-101 — Raj Kumar estate, 12 Banyan Lane", "description": "Raj died in 2021; Priya seeks recognition as successor.",
                                            "property": {"address_line": "12 Banyan Lane", "parcel_number": "22-104-0009"}, "parties": [{"name": "Priya Kumar", "roles": ["client", "heir"]}]}))["case"]
    cid = case["id"]
    for name, text_ in DOCS.items():
        check(legal.post(f"/cases/{cid}/documents", files={"file": (name, text_.encode(), "text/plain")}))
    for f in check(legal.get(f"/cases/{cid}/facts"))["facts"]:
        check(legal.patch(f"/facts/{f['id']}", json={"status": "confirmed"}))
    print(f"{case['reference']}: 3 documents uploaded, facts reviewed by a person")
    a = check(legal.post(f"/cases/{cid}/external-approvals", json={"action_type": "ConfirmSuccession", "rationale": "Priya Kumar is the sole heir"}))["approval"]
    act = next(x for x in check(legal.get(f"/cases/{cid}/workflow"))["actions"] if x["action"] == "external.submit_to_registry" and x["status"] == "proposed")
    check(legal_users["arun"].post(f"/approvals/{act['id']}/approve", json={"confirmation": f"approve action {act['seq']}"}))
    a = check(legal.get(f"/external-approvals/{a['id']}"))["approval"]
    print(f"proposal {a['proposal_id']} → {a['status']} · request contract {a['ledger']['request_contract_id'][:24]}… · update {a['ledger']['request_update_id']}")
    for _ in range(20):
        check(legal.post("/external-approvals/sync"))
        a = check(legal.get(f"/external-approvals/{a['id']}"))["approval"]
        if a["status"] == "awaiting_review":
            break
        time.sleep(1)
    print(f"registry participant received it → {a['status']}")
    if args.stop_before_decision:
        print(f"\nOpen the registry portal as ravi / {PASSWORD} (#registry) to decide; case: #case/{cid}/external")
        return
    check(reg_users["ravi"].post(f"/registry/external-approvals/{a['id']}/approve", json={"reason_code": "EvidenceSufficient"}))
    for _ in range(20):
        check(legal.post("/external-approvals/sync"))
        a = check(legal.get(f"/external-approvals/{a['id']}"))["approval"]
        if a["status"] in ("approved", "rejected", "changes_requested"):
            break
        time.sleep(1)
    print(f"Legal Aid participant shows the decision → {a['status']} · decision contract {a['ledger']['decision_contract_id'][:24]}… · update {a['ledger']['decision_update_id']}")
    print(f"evidence commitment {a['evidence_commitment']} · synchronizer {a['ledger']['synchronizer_id']}")


if __name__ == "__main__":
    main()
