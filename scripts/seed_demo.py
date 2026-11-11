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
LAND = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "land_documents"
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
    seed_land_case(pat)
    print("\nSign in at http://localhost:5173 (dev) or the backend URL as pat / alex / avery with password", PASSWORD)


def seed_land_case(pat: httpx.Client) -> None:
    """A synthetic rural Tamil Nadu case: land still in a grandfather's name, five heirs, two of them away."""
    case = check(pat.post("/cases", json={
        "title": "Ramasamy heirs — S.No. 123/4A, Keezhaiyur",
        "description": "The patta still shows Ramasamy (died 1987). Kannan wants a crop loan; a co-heir signed an agreement of sale in 2021.",
        "parties": [{"name": "Kannan", "roles": ["client", "heir"]}],
    }))["case"]
    cid = case["id"]
    print(f"case {case['reference']}  {cid}  (rural land records)")
    check(pat.put(f"/cases/{cid}/land/parcel", json={
        "region_code": "TN", "district": "Thanjavur", "taluk": "Orathanadu", "village": "Keezhaiyur", "survey_number": "123/4A",
        "guideline_inr_per_sqm": 150, "personal_law": "hindu",
    }))
    for path in sorted(LAND.glob("0*.txt")):
        check(pat.post(f"/cases/{cid}/documents", files={"file": (path.name, path.read_bytes(), "text/plain")}))
    derived = check(pat.post(f"/cases/{cid}/land/records/derive"))
    print(f"  land records: {derived['created']} read from documents, {derived['comparables']} comparable sale(s)")
    ids: dict[str, str] = {}
    tree = [
        ("r", {"name": "Ramasamy", "sex": "male", "is_alive": False, "date_of_death": "1987-03-02", "is_recorded_holder": True}),
        ("l", {"name": "Lakshmi Ammal", "sex": "female", "is_alive": False, "date_of_death": "2015-06-10", "spouse_ids": ["r"]}),
        ("m", {"name": "Muthusamy", "sex": "male", "is_alive": False, "date_of_death": "2019-01-20", "parent_ids": ["r", "l"]}),
        ("v", {"name": "Valli", "sex": "female", "spouse_ids": ["m"]}),
        ("k", {"name": "Kannan", "sex": "male", "parent_ids": ["r", "l"], "lives_in_village": False, "location": "Chennai"}),
        ("s", {"name": "Selvi", "sex": "female", "parent_ids": ["r", "l"], "consent_status": "consented"}),
        ("a", {"name": "Arun", "sex": "male", "parent_ids": ["m", "v"]}),
        ("p", {"name": "Priya", "sex": "female", "parent_ids": ["m", "v"], "lives_in_village": False, "location": "Bengaluru"}),
    ]
    for key, body in tree:
        body = {**body, "parent_ids": [ids[x] for x in body.get("parent_ids", [])], "spouse_ids": [ids[x] for x in body.get("spouse_ids", [])]}
        ids[key] = check(pat.post(f"/cases/{cid}/land/members", json=body))["member"]["id"]
    for sale in (
        {"village": "Keezhaiyur", "taluk": "Orathanadu", "sale_date": "2024-06-01", "extent_text": "50 cents", "consideration_inr": 420000, "land_class": "wet"},
        {"village": "Keezhaiyur", "taluk": "Orathanadu", "sale_date": "2025-02-15", "extent_text": "2 acres", "consideration_inr": 1650000, "land_class": "wet", "distance_km": 1.5},
    ):
        check(pat.post("/land/comparables", json=sale))
    for cost in (
        {"entry_date": "2025-01-10", "category": "document_writer", "payee_kind": "document_writer", "amount_inr": 1500, "days_spent": 1, "notes": "to read the patta"},
        {"entry_date": "2025-02-01", "category": "unreceipted_payment", "payee_kind": "middleman", "service": "legal_heir_certificate", "amount_inr": 3000, "days_spent": 3},
        {"entry_date": "2024-06-01", "category": "informal_loan", "payee_kind": "lender", "principal_inr": 100000, "monthly_rate_pct": 3, "months": 12, "notes": "village moneylender"},
    ):
        check(pat.post(f"/cases/{cid}/land/costs", json=cost))
    check(pat.post("/land/field-observations", json={
        "village": "Keezhaiyur", "taluk": "Orathanadu", "district": "Thanjavur", "observed_on": "2025-10-04", "households_surveyed": 40,
        "households_deceased_holder": 23, "correction_barriers": ["co-heirs living in cities", "cost of the legal heir certificate", "fear of family dispute"],
        "branch_name": "Orathanadu branch", "lender_kind": "bank_crop_loan", "branch_requirements": ["patta in borrower's name", "adangal for the season", "no-due certificate from the co-op"],
        "valuation_practices": ["guideline value", "broker word of mouth"], "disagreement_counts": {"names": 14, "extents": 9, "boundaries": 6},
        "dispute_settlers": ["village elders (panchayat)", "VAO", "civil court"], "dispute_days_typical": 400, "family_spend_inr_typical": 18000,
        "family_days_typical": 25, "record_readers": ["document writer", "middleman at the taluk office"], "record_reading_fee_inr": 500,
    }))
    agent = check(pat.post(f"/cases/{cid}/land/agent", json={"lender": "bank_crop_loan", "loan_amount_inr": 300000}))["run"]
    print(f"  agent: {len(agent['steps'])} steps, {len(agent['handoffs'])} hand-offs, proposed actions {agent['proposed_actions']}")
    print(f"  {agent['narrative'][:300]}…")


if __name__ == "__main__":
    main()
