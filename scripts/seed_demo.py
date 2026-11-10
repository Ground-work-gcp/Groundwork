"""Seed a demo organisation and one synthetic heirs'-property case through the live HTTP API.

usage:  python scripts/seed_demo.py [--base http://127.0.0.1:8000]

Creates an admin, a paralegal ("pat", password "demo-password-1") and an attorney
("alex", same password), opens a case, uploads the synthetic evidence, extracts it,
rebuilds ownership, derives obligations, imports payments, reconciles and runs detection.
It stops short of review and approval so those can be done by a person in the UI.
All names and records are fictitious.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

DOCS = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "case_documents"
PASSWORD = "demo-password-1"
WEB = {"x-groundwork-client": "web"}


def check(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        sys.exit(f"{response.request.method} {response.request.url} → {response.status_code}: {response.text}")
    return response.json() if "json" in response.headers.get("content-type", "") else {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    base = parser.parse_args().base.rstrip("/") + "/api/v1"

    admin = httpx.Client(base_url=base, headers=WEB, timeout=60)
    signed = admin.post("/auth/sign-in", json={"username": "avery", "password": PASSWORD})
    if signed.status_code != 200:
        check(admin.post("/auth/sign-up", json={"organization": "Hollins County Legal Aid", "username": "avery", "full_name": "Avery Admin", "email": "avery@example.org", "password": PASSWORD}))
        for username, name, role in (("pat", "Pat Paralegal", "paralegal"), ("alex", "Alex Attorney", "attorney")):
            check(admin.post("/settings/users", json={"username": username, "full_name": name, "email": f"{username}@example.org", "password": PASSWORD, "role": role}))

    pat = httpx.Client(base_url=base, headers=WEB, timeout=60)
    check(pat.post("/auth/sign-in", json={"username": "pat", "password": PASSWORD}))
    case = check(pat.post("/cases", json={
        "title": "Parker heirs — 418 Magnolia Rd",
        "description": "Denise Parker Holmes wants to keep the family home. Taxes are delinquent and an LLC claims an interest.",
        "property": {"address_line": "418 Magnolia Road", "county": "Hollins", "state": "GA", "parcel_number": "14-022-0113"},
        "parties": [{"name": "Denise Parker Holmes", "roles": ["client", "heir"]}],
    }))["case"]
    case_id = case["id"]
    print(f"case {case['reference']}  {case_id}")
    for path in sorted(DOCS.glob("0*.txt")):
        document = check(pat.post(f"/cases/{case_id}/documents", files={"file": (path.name, path.read_bytes(), "text/plain")}))["document"]
        print(f"  {document['filename']:42} {document['doc_type']:18} {document['status']:10} facts={document.get('fact_count')}")
    for stage in ("evidence",):
        check(pat.post(f"/cases/{case_id}/stage", json={"to_stage": stage}))
    ownership = check(pat.post(f"/cases/{case_id}/ownership/reconstruct"))
    print(f"  ownership: {len(ownership['events'])} events, {len(ownership['issues'])} issues, owners {ownership['current_owners']}")
    check(pat.post(f"/cases/{case_id}/stage", json={"to_stage": "ownership"}))
    derived = check(pat.post(f"/cases/{case_id}/obligations/derive"))["created"]
    imported = check(pat.post(f"/cases/{case_id}/transactions/import", files={"file": ("payments.csv", (DOCS / "payments.csv").read_bytes(), "text/csv")}, data={"date_format": "ISO"}))
    run = check(pat.post(f"/cases/{case_id}/reconciliation/runs"))
    print(f"  financials: {len(derived)} obligations, {imported['imported']} payments ({len(imported['rejections'])} rejected), recon {run['run']['stats']['byOutcome']}")
    check(pat.post(f"/cases/{case_id}/stage", json={"to_stage": "financial"}))
    check(pat.post(f"/cases/{case_id}/stage", json={"to_stage": "discrepancies"}))
    detected = check(pat.post(f"/cases/{case_id}/risks/detect"))
    print(f"  findings: {detected['outcome']}")
    for finding in detected["findings"]:
        print(f"    [{finding['severity']:8}] {finding['title']}")
    print("\nSign in at http://localhost:5173 (dev) or the backend URL as pat / alex / avery with password", PASSWORD)


if __name__ == "__main__":
    main()
