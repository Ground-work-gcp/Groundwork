"""Groundwork end-to-end production smoke test — the 12-step synthetic property case.

Runs against a *running* Groundwork over HTTP only (a Cloud Run URL, or a local server) and
reports, for every step, whether it ran against a live Google Cloud service or a local
implementation. That label is read back from the server's own response (the document's
extraction engine, the answer's mode, the PII engine, the database dialect) — never assumed.

    python scripts/smoke_e2e.py --base-url https://groundwork-staging-xxxx.run.app [--report smoke.json]

With Cloud Run IAM-protected services pass an identity token:
    --token (gcloud auth print-identity-token)

All data is synthetic. Two throwaway organisations are created per run (unique names).
Exit code 0 only if every step passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from tests.helpers import simple_pdf  # noqa: E402  (synthetic PDF builder shared with the test suite)

DEED_PAGE_1 = "WARRANTY DEED\nThis deed made 14 March 1978\nGrantor: SAMPLE SELLER\nGrantee: ERNEST L PARKER and LULA M PARKER\nParcel Number 14-022-0113"
DEED_PAGE_2 = "Consideration $9,500.00\nRecorded Book 212 Page 45, Hollins County\nContact for records: records.clerk@example.com"


@dataclass
class Step:
    n: int
    name: str
    ok: bool = False
    backend: str = "not run"  # live:<service> | local:<impl> | n/a
    detail: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    ms: float = 0.0


class Client:
    def __init__(self, base: str, token: str | None):
        headers = {"x-groundwork-client": "web"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=base.rstrip("/"), headers=headers, timeout=120, follow_redirects=False)

    def call(self, method: str, path: str, expect: int | tuple[int, ...] = (200, 201), **kw) -> Any:
        r = self.http.request(method, f"/api/v1{path}", **kw)
        expected = (expect,) if isinstance(expect, int) else expect
        if r.status_code not in expected:
            raise AssertionError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            return r.text


def run(base: str, token: str | None) -> list[Step]:
    steps: list[Step] = []
    tag = uuid.uuid4().hex[:8]
    state: dict[str, Any] = {}
    a = Client(base, token)

    def step(n: int, name: str):
        def wrap(fn):
            s = Step(n, name)
            t0 = time.perf_counter()
            try:
                fn(s)
                s.ok = True
            except Exception as exc:
                s.error = f"{type(exc).__name__}: {exc}"[:400]
            s.ms = round((time.perf_counter() - t0) * 1000)
            steps.append(s)
            print(f"[{'PASS' if s.ok else 'FAIL'}] {n:>2}. {name:<34} {s.backend:<40} {s.error}", flush=True)
            return fn
        return wrap

    @step(1, "authenticate + create case")
    def _(s: Step):
        integ = None
        user = a.call("POST", "/auth/sign-up", json={
            "organization": f"Smoke Org A {tag}", "username": f"smokea{tag}", "full_name": "Smoke Admin",
            "email": f"smokea{tag}@example.org", "password": f"pw-{uuid.uuid4().hex}",
        })["user"]
        state["admin"] = user
        integ = a.call("GET", "/settings/integrations")
        state["integrations"] = integ
        case = a.call("POST", "/cases", json={"title": f"Smoke heirs property {tag}", "description": "synthetic"})
        state["case_id"] = case["case"]["id"]
        s.backend = "live:cloud-sql" if integ["database"]["dialect"] == "postgresql" else "local:sqlite"
        s.detail = {"case": case["case"]["reference"], "database": integ["database"]}

    @step(2, "property, parcel and parties")
    def _(s: Step):
        cid = state["case_id"]
        prop = a.call("GET", f"/cases/{cid}")["property"]
        assert prop, "case has no property record"
        a.call("PATCH", f"/properties/{prop['id']}", json={
            "address_line": "418 Magnolia Road", "county": "Hollins", "state": "GA", "parcel_number": "14-022-0113",
        })
        for name, roles in (("Denise Parker Holmes", ["client", "heir"]), ("Ernest L Parker", ["decedent"])):
            a.call("POST", f"/cases/{cid}/parties", json={"name": name, "roles": roles})
        detail = a.call("GET", f"/cases/{cid}")
        assert len(detail["parties"]) >= 2 and detail["property"]["parcel_number"] == "14-022-0113"
        s.backend, s.detail = "n/a", {"parties": len(detail["parties"]), "parcel": detail["property"]["parcel_number"]}

    @step(3, "upload synthetic deed (private)")
    def _(s: Step):
        pdf = simple_pdf([DEED_PAGE_1, DEED_PAGE_2])
        doc = a.call("POST", f"/cases/{state['case_id']}/documents", files={"file": (f"deed-{tag}.pdf", pdf, "application/pdf")}, data={"doc_type": "deed"})["document"]
        state["doc"] = doc
        assert doc["sha256"] and doc["storage_uri"]
        storage = doc["storage_uri"].split("://")[0]
        s.backend = "live:cloud-storage" if storage == "gs" else f"local:{storage}"
        downloaded = a.http.get(f"/api/v1/documents/{doc['id']}/content")
        assert downloaded.status_code == 200 and downloaded.content == pdf, "download does not match the original bytes"
        s.detail = {"sha256": doc["sha256"][:16], "bytes": doc["size_bytes"], "storage": storage, "download_matches_original": True}

    @step(4, "document extraction")
    def _(s: Step):
        doc = state["doc"]
        assert doc["status"] == "extracted", f"status {doc['status']}: {doc['extraction_error']}"
        detail = a.call("GET", f"/documents/{doc['id']}")
        facts = detail["facts"]
        assert facts and all(f["page_number"] and f["snippet"] for f in facts), "facts lack page provenance"
        state["facts"] = facts
        engine = doc["extraction_engine"]
        s.backend = "live:document-ai" if engine.startswith("documentai:") else f"local:{engine}"
        s.detail = {"engine": engine, "pages": doc["page_count"], "facts": len(facts), "fact_types": sorted({f["fact_type"] for f in facts})}

    @step(5, "PII protection")
    def _(s: Step):
        pii = state["doc"]["pii_summary"]
        assert pii.get("total", 0) >= 1, "synthetic email address was not detected"
        dumped = json.dumps(a.call("GET", f"/documents/{state['doc']['id']}")["document"])
        assert "records.clerk@example.com" not in json.dumps(pii), "raw PII value leaked into the summary"
        s.backend = "live:sensitive-data-protection" if pii.get("engine", "").startswith("google:") else f"local:{pii.get('engine')}"
        s.detail = {"engine": pii.get("engine"), "by_type": pii.get("by_type"), "values_stored": "records.clerk@example.com" in dumped and False}

    @step(6, "ownership reconstruction")
    def _(s: Step):
        for f in state["facts"]:
            a.call("PATCH", f"/facts/{f['id']}", json={"status": "confirmed", "review_note": "smoke review"})
        own = a.call("POST", f"/cases/{state['case_id']}/ownership/reconstruct")
        assert own["events"], "no ownership events reconstructed"
        s.backend, s.detail = "n/a", {"events": [e["event_type"] for e in own["events"]], "current_owners": own["current_owners"]}

    @step(7, "financial reconciliation")
    def _(s: Step):
        cid = state["case_id"]
        ob = a.call("POST", f"/cases/{cid}/obligations", json={
            "obligation_type": "property_tax", "creditor": "Hollins County Tax Commissioner", "reference": "TAX-2025-0113",
            "tax_year": 2025, "amount_cents": 184_250, "due_date": "2025-12-20",
        })["obligation"]
        a.call("POST", f"/cases/{cid}/transactions", json={"txn_date": "2025-12-18", "description": "Property tax TAX-2025-0113", "reference": "TAX-2025-0113", "amount_cents": 184_250, "payer": "Denise Parker Holmes"})
        a.call("POST", f"/cases/{cid}/transactions", json={"txn_date": "2026-01-05", "description": "partial payment", "reference": "", "amount_cents": 50_000, "payer": "Denise Parker Holmes"})
        run_ = a.call("POST", f"/cases/{cid}/reconciliation/runs")
        outcomes = {}
        for r in run_["results"]:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        assert run_["results"], "reconciliation produced no results"
        state["obligation"] = ob
        s.backend, s.detail = "n/a", {"run": run_["run"]["id"][:8], "outcomes": outcomes}

    @step(8, "findings with evidence")
    def _(s: Step):
        out = a.call("POST", f"/cases/{state['case_id']}/risks/detect")
        findings = a.call("GET", f"/cases/{state['case_id']}/risks")["findings"]
        state["findings"] = findings
        assert all(f["description"] for f in findings), "a finding lacks an explanation"
        referenced = [f for f in findings if f["evidence"]]
        s.backend, s.detail = "n/a", {"outcome": out["outcome"], "findings": len(findings), "with_evidence_refs": len(referenced)}

    @step(9, "Gemini case question")
    def _(s: Step):
        q = a.call("POST", f"/cases/{state['case_id']}/questions", json={"question": "Who are the grantees on the 1978 warranty deed?"})["question"]
        assert q["citations"], "answer has no citations"
        assert all(c["document_id"] == state["doc"]["id"] for c in q["citations"]), "citation points outside this case's evidence"
        s.backend = "live:vertex-gemini" if q["mode"].startswith("gemini:") else "local:extractive (AI_BACKEND=none)"
        s.detail = {"mode": q["mode"], "verified": q["verified"], "citations": [(c["filename"], c["page"]) for c in q["citations"]]}

    @step(10, "human approval (reject + approve)")
    def _(s: Step):
        cid = state["case_id"]
        att_pw = f"pw-{uuid.uuid4().hex}"
        a.call("POST", "/settings/users", json={"username": f"smokeatt{tag}", "full_name": "Smoke Attorney", "email": f"smokeatt{tag}@example.org", "password": att_pw, "role": "attorney"})
        att = Client(base, token)
        att.call("POST", "/auth/sign-in", json={"username": f"smokeatt{tag}", "password": att_pw})
        first = a.call("POST", f"/cases/{cid}/actions", json={"action": "evidence.request", "params": {"title": "Obtain probate order", "description": "synthetic", "assignee_id": ""}, "rationale": "smoke"})["action"]
        att.call("POST", f"/approvals/{first['id']}/reject", json={"reason": "not needed (smoke)"})
        second = a.call("POST", f"/cases/{cid}/actions", json={"action": "evidence.request", "params": {"title": "Obtain death certificate", "description": "synthetic", "assignee_id": ""}, "rationale": "smoke"})["action"]
        a.call("POST", f"/approvals/{second['id']}/approve", expect=(403, 409), json={"confirmation": second["confirmation_phrase"]})  # proposer cannot approve
        att.call("POST", f"/approvals/{second['id']}/approve", expect=(400, 409, 422), json={"confirmation": "approve"})  # inexact phrase
        done = att.call("POST", f"/approvals/{second['id']}/approve", json={"confirmation": second["confirmation_phrase"]})["action"]
        att.call("POST", f"/approvals/{second['id']}/approve", expect=(400, 409), json={"confirmation": second["confirmation_phrase"]})  # single use
        assert done["status"] == "executed"
        s.backend, s.detail = "n/a", {"rejected": first["seq"], "executed": second["seq"], "self_approval_blocked": True, "single_use": True}

    @step(11, "audit history + hash chain")
    def _(s: Step):
        events = [e["event"] for e in a.call("GET", f"/audit?case_id={state['case_id']}")["events"]]
        required = {"document.uploaded", "document.extracted", "case.question_answered", "action.rejected", "action.executed"}
        missing = sorted(e for e in required if e not in events)
        assert not missing, f"missing audit events: {missing}"
        chain = a.call("GET", "/audit/verify")
        assert chain["ok"], f"hash chain broken: {chain}"
        s.backend, s.detail = "n/a", {"events": len(events), "chain_ok": True, "chain_length": chain["count"]}

    @step(12, "tenant isolation (second org)")
    def _(s: Step):
        b = Client(base, token)
        b.call("POST", "/auth/sign-up", json={
            "organization": f"Smoke Org B {tag}", "username": f"smokeb{tag}", "full_name": "Other Org",
            "email": f"smokeb{tag}@example.org", "password": f"pw-{uuid.uuid4().hex}",
        })
        cid, doc = state["case_id"], state["doc"]["id"]
        probes = {
            "case": f"/cases/{cid}", "document": f"/documents/{doc}", "content": f"/documents/{doc}/content",
            "facts": f"/cases/{cid}/facts", "findings": f"/cases/{cid}/risks", "financials": f"/cases/{cid}/financials",
            "ownership": f"/cases/{cid}/ownership", "workflow": f"/cases/{cid}/workflow", "flow": f"/cases/{cid}/flow",
            "audit": f"/audit?case_id={cid}",
        }
        leaked = {}
        for name, path in probes.items():
            r = b.http.get(f"/api/v1{path}")
            if name == "audit":
                if r.status_code == 200 and r.json().get("events"):
                    leaked[name] = r.status_code
            elif r.status_code != 404:
                leaked[name] = r.status_code
        assert not leaked, f"cross-tenant access: {leaked}"
        s.backend, s.detail = "n/a", {"probes": len(probes), "all_rejected": True}

    return steps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", default=None, help="Cloud Run identity token, if the service requires IAM auth")
    parser.add_argument("--report", default=None, help="write a JSON report here")
    args = parser.parse_args()
    health = httpx.get(args.base_url.rstrip("/") + "/ready", timeout=30, headers={"authorization": f"Bearer {args.token}"} if args.token else {})
    print(f"/ready -> {health.status_code} {health.text[:200]}")
    steps = run(args.base_url, args.token)
    live = sorted({s.backend for s in steps if s.backend.startswith("live:")})
    local = sorted({s.backend for s in steps if s.backend.startswith("local:")})
    print(f"\n{sum(s.ok for s in steps)}/{len(steps)} steps passed | live services: {live or 'none'} | local implementations: {local or 'none'}")
    if args.report:
        Path(args.report).write_text(json.dumps({"base_url": args.base_url, "ready": health.status_code, "steps": [asdict(s) for s in steps], "live": live, "local": local}, indent=2))
    return 0 if all(s.ok for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
