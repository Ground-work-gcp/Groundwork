"""Rural land records end to end, on a synthetic Tamil Nadu case.

The plot (S.No. 123/4A, Keezhaiyur) is still in the name of Ramasamy, who died in 1987. His widow
died in 2015 and a son in 2019; a son and a granddaughter live away from the village. The patta,
adangal, FMB sketch, EC, death certificate, a neighbour's patta and a nearby sale deed are uploaded
as documents; everything below is derived from them through the API.
"""

from __future__ import annotations

from pathlib import Path

from tests.helpers import add_member, new_case, sign_up, upload

LAND = Path(__file__).resolve().parents[1] / "fixtures" / "land_documents"
FILES = sorted(p.name for p in LAND.glob("*.txt"))


def _upload_land(client, case_id: str) -> None:
    for name in FILES:
        upload(client, case_id, name, (LAND / name).read_bytes())


def _tree(client, case_id: str) -> dict[str, str]:
    ids: dict[str, str] = {}

    def add(key: str, **body) -> None:
        r = client.post(f"/api/v1/cases/{case_id}/land/members", json=body)
        assert r.status_code == 201, r.text
        ids[key] = r.json()["member"]["id"]

    add("r", name="Ramasamy", sex="male", is_alive=False, date_of_death="1987-03-02", is_recorded_holder=True)
    add("l", name="Lakshmi Ammal", sex="female", is_alive=False, date_of_death="10-06-2015", spouse_ids=[ids["r"]])
    add("m", name="Muthusamy", sex="male", is_alive=False, date_of_death="2019-01-20", parent_ids=[ids["r"], ids["l"]])
    add("v", name="Valli", sex="female", spouse_ids=[ids["m"]])
    add("k", name="Kannan", sex="male", parent_ids=[ids["r"], ids["l"]], lives_in_village=False, location="Chennai")
    add("s", name="Selvi", sex="female", parent_ids=[ids["r"], ids["l"]])
    add("a", name="Arun", sex="male", parent_ids=[ids["m"], ids["v"]])
    add("p", name="Priya", sex="female", parent_ids=[ids["m"], ids["v"]], lives_in_village=False, location="Bengaluru")
    return ids


def _setup(app):
    admin, _ = sign_up(app)
    case_id = new_case(admin, "Ramasamy heirs — S.No. 123/4A Keezhaiyur")["case"]["id"]
    r = admin.put(f"/api/v1/cases/{case_id}/land/parcel", json={
        "region_code": "TN", "district": "Thanjavur", "taluk": "Orathanadu", "village": "Keezhaiyur", "survey_number": "S.F. No. 123/4-A",
        "guideline_inr_per_sqm": 150, "personal_law": "hindu",
    })
    assert r.status_code == 200, r.text
    assert r.json()["parcel"]["survey_key"] == "123/4A"
    _upload_land(admin, case_id)
    derived = admin.post(f"/api/v1/cases/{case_id}/land/records/derive").json()
    assert derived["created"] == len(FILES) and derived["comparables"] == 1
    return admin, case_id


def test_records_are_derived_compared_and_become_findings(app):
    admin, case_id = _setup(app)
    land = admin.get(f"/api/v1/cases/{case_id}/land").json()
    parcel = land["parcel"]
    assert parcel["extent_text"] == "1 acre 20 cents" and abs(parcel["extent_sqm"] - 4856.23) < 1 and parcel["land_class"] == "wet"
    types = sorted(r["source_type"] for r in land["records"])
    assert types == ["cultivation", "death_certificate", "encumbrance", "ror", "ror", "sale_deed", "sketch"]
    _tree(admin, case_id)

    conc = admin.post(f"/api/v1/cases/{case_id}/land/analyses/concordance").json()["analysis"]["result"]
    kinds = {(i["type"], i["severity"]) for i in conc["issues"]}
    assert ("deceased_holder", "critical") in kinds
    assert ("extents", "high") in kinds  # FMB 1.05 acres and its measured corners vs 1.20 acres on the patta
    assert ("survey_numbers", "medium") in kinds  # EC cites 123/4, not 123/4A
    assert ("encumbrance", "high") in kinds  # Kannan's 2021 agreement of sale is still open
    titles = " | ".join(i["title"] for i in conc["issues"])
    assert "East boundary differs" in titles and "S.No. 124 does not acknowledge" in titles
    assert conc["ec_years"] >= 25 and conc["deceased_holders"] == ["Ramasamy s/o Kandasamy"]
    deceased = next(i for i in conc["issues"] if i["type"] == "deceased_holder")
    assert "Taluk office" in deceased["office"] and "38 years after the death" in deceased["detail"]

    findings = admin.get(f"/api/v1/cases/{case_id}/risks").json()["findings"]
    land_types = {f["finding_type"] for f in findings if f["finding_type"].startswith("land_")}
    assert {"land_holder_deceased", "land_extent_mismatch", "land_boundary_conflict", "land_encumbrance_open", "land_survey_mismatch"} <= land_types
    # Re-running detection from the Findings tab keeps the land findings (they are part of detection, not a side channel).
    admin.post(f"/api/v1/cases/{case_id}/risks/detect")
    again = {f["finding_type"] for f in admin.get(f"/api/v1/cases/{case_id}/risks").json()["findings"] if f["status"] == "open"}
    assert land_types <= again
    suggested = admin.post(f"/api/v1/cases/{case_id}/actions/suggested").json()["created"]
    assert any("legal heir certificate" in a["params"]["title"] for a in suggested)


def test_succession_valuation_readiness_and_explanation(app):
    admin, case_id = _setup(app)
    ids = _tree(admin, case_id)
    succ = admin.post(f"/api/v1/cases/{case_id}/land/analyses/succession").json()["analysis"]["result"]
    shares = {s["name"]: s["share"] for s in succ["shares"]}
    assert succ["status"] == "computed" and succ["total"] == "1"
    assert shares == {"Kannan": "1/3", "Selvi": "1/3", "Arun": "1/9", "Priya": "1/9", "Valli": "1/9"}
    assert sorted(succ["away_from_village"]) == ["Kannan", "Priya"] and len(succ["steps"]) == 3

    for body in (
        {"village": "Keezhaiyur", "taluk": "Orathanadu", "sale_date": "2024-06-01", "extent_text": "50 cents", "consideration_inr": 420000, "land_class": "wet"},
        {"village": "Keezhaiyur", "taluk": "Orathanadu", "sale_date": "2025-02-15", "extent_text": "2 acres", "consideration_inr": 1650000, "land_class": "wet", "distance_km": 1.5},
    ):
        assert admin.post("/api/v1/land/comparables", json=body).status_code == 201
    val = admin.post(f"/api/v1/cases/{case_id}/land/analyses/valuation", json={"claimed_value_inr": 4_000_000}).json()["analysis"]["result"]
    assert val["status"] == "estimated" and val["p10_inr"] < val["p50_inr"] < val["p90_inr"]
    assert val["lending_value_inr"] < val["p20_inr"] < val["p50_inr"]  # the title haircut applies to the 20th percentile
    assert {h["kind"] for h in val["haircut_reasons"]} >= {"deceased_holder", "consents"}
    assert any("above the 90th percentile" in w for w in val["warnings"])
    assert len(val["comparables"]) == 3  # two entered + the sale deed derived from the documents

    term = admin.post(f"/api/v1/cases/{case_id}/land/analyses/readiness", json={"lender": "bank_term_loan", "loan_amount_inr": 500000}).json()["analysis"]["result"]
    assert term["verdict"] == "not_ready"
    req = {r["id"]: r["status"] for r in term["requirements"]}
    assert req["no_deceased_holder"] == "blocked" and req["legal_heir_certificate"] == "missing" and req["co_heir_consent"] == "blocked"
    plan = [p["service"] for p in term["plan"]]
    assert plan.index("legal_heir_certificate") < plan.index("co_heir_consent") < plan.index("mutation")
    assert "mutation" in term["critical_path"] and term["days_to_ready"] >= 81 and term["official_fees_inr"] > 0

    crop = admin.post(f"/api/v1/cases/{case_id}/land/analyses/readiness", json={"lender": "bank_crop_loan", "loan_amount_inr": 150000}).json()["analysis"]["result"]
    assert crop["collateral_free"] is True and "collateral-free" in crop["notes"][0]

    ta = admin.get(f"/api/v1/cases/{case_id}/land/explain?lang=ta").json()
    assert ta["language"] == "ta" and "பட்டா மாற்றம்" not in ta["lines"][0] and "பெயரில் பதிவாகியுள்ளது" in ta["lines"][0]
    assert any("₹" in line for line in ta["lines"]) and "Kannan: 1/3" in ta["shares"]
    hi = admin.get(f"/api/v1/cases/{case_id}/land/explain?lang=hi").json()
    assert "दर्ज है" in hi["lines"][0]

    packet = admin.get(f"/api/v1/cases/{case_id}/land/mutation-packet").text
    assert "Patta transfer" in packet and "| Kannan | 1/3 | no |" in packet and "**to obtain**" in packet
    letter = admin.get(f"/api/v1/land/members/{ids['k']}/consent-letter").text
    assert "I, Kannan" in letter and "1/3" in letter


def test_remote_coheir_consent_link_is_single_use(app, anon):
    admin, case_id = _setup(app)
    ids = _tree(admin, case_id)
    link = admin.post(f"/api/v1/land/members/{ids['p']}/consent-link").json()
    token = link["token"]
    assert link["link"].endswith(f"#consent/{token}")
    view = anon.get(f"/api/v1/public/consent/{token}")
    assert view.status_code == 200, view.text
    assert view.json()["name"] == "Priya" and view.json()["your_share"] == "1/9"
    assert anon.post(f"/api/v1/public/consent/{token}", json={"decision": "maybe"}).status_code == 422
    assert anon.post(f"/api/v1/public/consent/{token}", json={"decision": "consented", "note": "Agreed by phone"}).json()["status"] == "consented"
    assert anon.get(f"/api/v1/public/consent/{token}").status_code == 404  # used
    member = next(m for m in admin.get(f"/api/v1/cases/{case_id}/land").json()["members"] if m["id"] == ids["p"])
    assert member["consent_status"] == "consented" and member["consent_note"] == "Agreed by phone"
    events = [e["event"] for e in admin.get(f"/api/v1/cases/{case_id}/timeline").json()["events"]]
    assert "land.consent_reply" in events and "land.consent_link_issued" in events
    assert anon.get("/api/v1/public/consent/not-a-real-token").status_code == 404


def test_agent_prepares_the_case_and_passport_needs_a_second_person(app, anon):
    admin, case_id = _setup(app)
    _tree(admin, case_id)
    run = admin.post(f"/api/v1/cases/{case_id}/land/agent", json={"lender": "bank_term_loan", "loan_amount_inr": 500000}).json()["run"]
    tools = [s["tool"] for s in run["steps"]]
    assert tools[:3] == ["concordance", "detect_findings", "succession"] and "readiness" in tools and "propose_actions" in tools
    assert run["mode"] == "deterministic" and "Ramasamy" in run["narrative"]
    whats = " | ".join(h["what"] for h in run["handoffs"])
    assert "Get Kannan's consent" in whats and "Legal heir certificate" in whats
    assert run["proposed_actions"] and len(run["result_hash"]) == 64
    actions = admin.get(f"/api/v1/cases/{case_id}/workflow").json()["actions"]
    assert any(a["proposed_by_kind"] == "system" and a["status"] == "proposed" for a in actions)
    # running again does not duplicate the same proposals
    again = admin.post(f"/api/v1/cases/{case_id}/land/agent", json={"lender": "bank_term_loan", "loan_amount_inr": 500000}).json()["run"]
    assert again["proposed_actions"] == []
    ai_steps = [e for e in admin.get(f"/api/v1/cases/{case_id}/timeline").json()["events"] if e["event"] == "land.agent.step"]
    assert ai_steps and all(e["actor_kind"] == "ai" for e in ai_steps)

    proposed = admin.post(f"/api/v1/cases/{case_id}/land/passport", json={"lender": "bank_term_loan", "loan_amount_inr": 500000})
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()["action"]
    assert admin.post(f"/api/v1/approvals/{action['id']}/approve", json={"confirmation": f"approve action {action['seq']}"}).status_code in (403, 409)
    attorney, _ = add_member(admin, app, "attorney", "Asha Advocate")
    ok = attorney.post(f"/api/v1/approvals/{action['id']}/approve", json={"confirmation": f"approve action {action['seq']}"})
    assert ok.status_code == 200, ok.text
    passports = admin.get(f"/api/v1/cases/{case_id}/land/passports").json()["passports"]
    assert len(passports) == 1 and passports[0]["status"] == "active"
    code, sha = passports[0]["code"], passports[0]["sha256"]
    verified = anon.get(f"/api/v1/public/passports/{code}?sha={sha}").json()
    assert verified["valid"] is True and verified["plot"]["survey_number"] == "123/4A" and "co_owners" in verified
    assert "Kannan" not in str(verified)  # no names on the public endpoint
    assert anon.get(f"/api/v1/public/passports/{code}?sha={'0' * 64}").json()["valid"] is False
    doc = admin.get(f"/api/v1/land/passports/{passports[0]['id']}/document").text
    assert code in doc and sha in doc and "P10" in doc
    assert admin.get("/api/v1/audit/verify").json()["ok"] is True


def test_costs_field_research_and_insights(app):
    admin, case_id = _setup(app)
    entries = [
        {"entry_date": "2025-01-10", "category": "document_writer", "payee_kind": "document_writer", "amount_inr": 1500, "days_spent": 1, "notes": "to read the patta"},
        {"entry_date": "2025-01-12", "category": "official_fee", "payee_kind": "office", "service": "encumbrance_certificate", "amount_inr": 200, "has_receipt": True, "days_spent": 1},
        {"entry_date": "2025-02-01", "category": "unreceipted_payment", "payee_kind": "middleman", "service": "legal_heir_certificate", "amount_inr": 3000, "days_spent": 3},
        {"entry_date": "2025-02-02", "category": "travel", "amount_inr": 600, "days_spent": 2},
        {"entry_date": "2024-06-01", "category": "informal_loan", "payee_kind": "lender", "principal_inr": 100000, "monthly_rate_pct": 3, "months": 12, "notes": "village moneylender"},
    ]
    for e in entries:
        r = admin.post(f"/api/v1/cases/{case_id}/land/costs", json=e)
        assert r.status_code == 201, r.text
    summary = admin.get(f"/api/v1/cases/{case_id}/land/costs").json()["summary"]
    assert summary["total_inr"] == 5300 and summary["days"] == 7 and summary["unreceipted_inr"] == 5100
    assert summary["excess_over_official"][0]["service"] == "legal_heir_certificate" and summary["excess_total_inr"] == 2940
    loan = summary["informal_loans"][0]
    assert loan["interest_paid_inr"] == 36000 and loan["formal_interest_inr"] == 7000 and loan["premium_inr"] == 29000
    assert admin.post(f"/api/v1/cases/{case_id}/land/costs", json={"entry_date": "2025-01-01", "category": "informal_loan"}).status_code == 422

    obs = {
        "village": "Keezhaiyur", "taluk": "Orathanadu", "observed_on": "2025-10-04", "households_surveyed": 40, "households_deceased_holder": 23,
        "correction_barriers": ["co-heirs living in cities", "cost of the legal heir certificate"], "branch_name": "Orathanadu branch",
        "lender_kind": "bank_term_loan", "branch_requirements": ["13-year EC", "patta in borrower's name", "no-objection from all heirs"],
        "valuation_practices": ["guideline value", "word of mouth from brokers"], "disagreement_counts": {"names": 14, "extents": 9, "boundaries": 6},
        "dispute_settlers": ["village elders (panchayat)", "VAO"], "dispute_days_typical": 400, "family_spend_inr_typical": 18000,
        "family_days_typical": 25, "record_readers": ["document writer"], "record_reading_fee_inr": 500,
    }
    assert admin.post("/api/v1/land/field-observations", json=obs).status_code == 201
    assert admin.post("/api/v1/land/field-observations", json=obs | {"households_deceased_holder": 50}).status_code == 422
    admin.post(f"/api/v1/cases/{case_id}/land/analyses/concordance")
    ins = admin.get("/api/v1/land/insights").json()
    assert ins["q1_deceased_holder"]["field_share"] == 0.575 and ins["q1_deceased_holder"]["case_share"] == 1.0
    assert ins["q4_disagreements"]["field"]["names"] == 14 and ins["q4_disagreements"]["cases"]["deceased_holder"] == 1
    assert ins["q5_disputes"]["median_days"] == 400 and ins["q6_family_cost"]["case_median_inr"] == 5300
    # what the local branch asks for is added to the readiness checklist
    ready = admin.post(f"/api/v1/cases/{case_id}/land/analyses/readiness", json={"lender": "bank_term_loan"}).json()["analysis"]["result"]
    assert "13-year EC (Orathanadu branch)" in ready["local_branch_asks"]


def test_land_records_are_tenant_isolated(app):
    admin, case_id = _setup(app)
    ids = _tree(admin, case_id)
    record_id = admin.get(f"/api/v1/cases/{case_id}/land").json()["records"][0]["id"]
    other, _ = sign_up(app)
    assert other.get(f"/api/v1/cases/{case_id}/land").status_code == 404
    assert other.delete(f"/api/v1/land/records/{record_id}").status_code == 404
    assert other.post(f"/api/v1/land/members/{ids['k']}/consent-link").status_code == 404
    assert other.get("/api/v1/land/comparables").json()["comparables"] == []
    viewer, _ = add_member(admin, app, "viewer")
    assert viewer.post(f"/api/v1/cases/{case_id}/land/records/derive").status_code == 403
    assert viewer.get(f"/api/v1/cases/{case_id}/land").status_code == 200


def test_manual_records_validation(app):
    admin, case_id = _setup(app)
    bad = admin.post(f"/api/v1/cases/{case_id}/land/records", json={"source_type": "ror", "boundaries": {"up": "x"}})
    assert bad.status_code == 422
    ok = admin.post(f"/api/v1/cases/{case_id}/land/records", json={
        "source_type": "tax_receipt", "survey_number": "123/4A", "record_date": "15-07-2026", "holders": ["Ramasamy"], "extra": {"tax_year": "2026"},
    })
    assert ok.status_code == 201 and ok.json()["record"]["record_date"] == "2026-07-15"
    bad_parcel = admin.put(f"/api/v1/cases/{case_id}/land/parcel", json={"region_code": "TN", "extent_text": "a lot of land"})
    assert bad_parcel.status_code == 422
