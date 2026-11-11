"""Family pathways: the real choices a family has once it knows who the heirs are.

Knowing the shares is not the end. Five heirs, two of them in cities, must now choose how the
land becomes usable again — and each route has different costs, different signatures, different
timelines and different borrowing power. Families usually make this choice on a document
writer's say-so. This lays the routes side by side, computed from the case:

* **A · Joint mutation** — record all heirs, borrow jointly. Cheapest; every co-owner signs the loan.
* **B · Release to one heir** — the others execute registered release deeds in favour of the heir
  who farms the land; one person can then borrow. Shows exactly what value each releasing heir
  gives up, so a fair settlement can be agreed *before* anyone signs.
* **C · Partition and subdivision** — a registered partition deed, a survey subdivision and a
  separate record for each heir; each borrows alone, on a smaller plot.
* **D · Collateral-free bridge** — a crop loan within the collateral-free limit now, on the
  cultivation record, while A/B/C runs. Not a route to clear title — a way not to borrow from the
  moneylender while it happens.

Each route is a small dependency graph of steps (offices, fees, days), scheduled by the critical
path; stamp duty and registration fees are computed on the guideline value of what is
transferred; borrowing capacity is the conservative lending value after the route's fixes. The
ranking is a transparent weighted score — the family decides.
"""

from __future__ import annotations

from typing import Any

from .names import similarity
from .regions import Region, duty, process, services
from .valuation import inr

MIN_LENDABLE_SQM = 2023.0  # half an acre: below this many branches will not lend against agricultural land
FRAGMENT_DISCOUNT = 0.10
WEIGHTS = {"days": 0.30, "capacity": 0.30, "cost": 0.15, "coordination": 0.25}


def _schedule(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, list[str]]:
    finish: dict[str, int] = {}
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        start = max((finish[d] for d in s["needs"] if d in finish), default=0)
        s["start_day"], s["finish_day"] = start, start + s["days"]
        finish[s["id"]] = s["finish_day"]
    if not steps:
        return steps, 0, []
    cur = max(steps, key=lambda s: s["finish_day"])["id"]
    path: list[str] = []
    while cur:
        path.insert(0, cur)
        deps = [d for d in by_id[cur]["needs"] if d in finish]
        cur = max(deps, key=lambda d: finish[d]) if deps else ""
    for s in steps:
        s["critical"] = s["id"] in path
    return steps, max(finish.values()), path


def _svc(region: Region, sid: str, needs: list[str], **over: Any) -> dict[str, Any]:
    s = services(region)[sid]
    return {"id": sid, "name": s.name, "office": s.office, "days": s.days, "fee_inr": s.fee_inr, "needs": needs, **over}


def beneficiary(shares: list[dict[str, Any]], cultivators: list[str], preferred: str | None) -> dict[str, Any] | None:
    living = [s for s in shares if s["alive"]]
    if preferred:
        hit = next((s for s in living if s["member_id"] == preferred), None)
        if hit:
            return hit | {"why": "chosen by the family"}
    for c in cultivators:
        for s in living:
            if similarity(c, s["name"])[0] >= 0.86:
                return s | {"why": "cultivates the land (adangal / crop record)"}
    village = [s for s in living if s["lives_in_village"]]
    pick = max(village or living, key=lambda s: s["value"], default=None)
    return pick | {"why": "largest share among heirs in the village"} if pick else None


def compare(
    region: Region,
    *,
    succ: dict[str, Any],
    val: dict[str, Any],
    ready: dict[str, Any],
    extent_sqm: float | None,
    guideline_inr_per_sqm: float | None,
    cultivators: list[str],
    lender_limit_inr: int,
    ltv: float,
    preferred_beneficiary: str | None = None,
) -> dict[str, Any]:
    living = [s for s in succ.get("shares", []) if s["alive"]]
    if not living:
        return {"status": "insufficient", "reason": "Work out the draft shares first (Family & shares)."}
    if val.get("status") != "estimated" or not extent_sqm:
        return {"status": "insufficient", "reason": "Estimate the land's value first (Value)."}
    plan_ids = {p["service"] for p in ready.get("plan", [])}
    away = [s for s in living if not s["lives_in_village"]]
    p20, p50 = val["p20_inr"], val["p50_inr"]
    residual = sum(h["cut"] for h in val.get("haircut_reasons", []) if h["kind"] in ("land_class",))
    lend_after = int(round(p20 * (1 - residual), -2))
    guide_total = (guideline_inr_per_sqm or (p50 / extent_sqm)) * extent_sqm
    proc = process(region.code)

    def title_steps() -> list[dict[str, Any]]:
        out = []
        if "death_certificate" in plan_ids:
            out.append(_svc(region, "death_certificate", []))
        if "legal_heir_certificate" in plan_ids:
            out.append(_svc(region, "legal_heir_certificate", [s["id"] for s in out if s["id"] == "death_certificate"]))
        return out

    def lender_steps(after: str, per_heir: bool = False) -> list[dict[str, Any]]:
        steps = [_svc(region, "encumbrance_certificate", [])]
        if "survey_measurement" in plan_ids and not per_heir:
            steps.append(_svc(region, "survey_measurement", []))
        steps.append(_svc(region, "valuation_report", [after]))
        steps.append(_svc(region, "legal_scrutiny", [after, "encumbrance_certificate"]))
        steps.append({"id": "loan_sanction", "name": "Loan application and sanction", "office": ready.get("lender_label", "lender"), "days": 21, "fee_inr": 0,
                      "needs": ["valuation_report", "legal_scrutiny"] + (["survey_measurement"] if any(s["id"] == "survey_measurement" for s in steps) else [])})
        return steps

    routes: list[dict[str, Any]] = []
    heirs_needed = [s["id"] for s in title_steps()]

    # A · joint mutation, joint borrowing
    a = title_steps()
    a.append(_svc(region, "co_heir_consent", heirs_needed[-1:], name="Signed consent letters from every co-heir"))
    a.append(_svc(region, "mutation", ["co_heir_consent"], name="Mutation into all heirs' names"))
    a += lender_steps("mutation")
    a, days_a, path_a = _schedule(a)
    routes.append({
        "id": "joint", "label": "A · Joint mutation, joint borrowing",
        "summary": "Record every heir on the land; borrow together, every co-owner signing.",
        "steps": a, "days": days_a, "critical_path": path_a,
        "cost_inr": sum(s["fee_inr"] for s in a), "duty_inr": 0,
        "signatures": [s["name"] for s in living], "away_signatures": [s["name"] for s in away],
        "borrower": "all co-owners jointly", "capacity_inr": int(round(lend_after * ltv, -2)), "per_heir": [],
        "value_transfers": [],
        "risks": [
            "Every co-owner becomes a borrower or guarantor; one refusal, now or later, blocks the loan.",
            f"{len(away)} co-owner(s) away from the village must come to the branch or give a registered power of attorney." if away else "All co-owners live locally.",
        ],
    })

    # B · release in favour of one heir
    ben = beneficiary(living, cultivators, preferred_beneficiary)
    releasing = [s for s in living if ben and s["member_id"] != ben["member_id"]]
    if ben and releasing:
        transfers, duty_total = [], 0
        for s in releasing:
            gv = s["value"] * guide_total
            stamp = duty(region.code, "release", gv)
            reg_fee = duty(region.code, "registration", gv)
            duty_total += stamp + reg_fee
            transfers.append({"from": s["name"], "share": s["share"], "market_value_inr": int(round(s["value"] * p50, -2)), "guideline_value_inr": int(round(gv, -2)), "stamp_inr": stamp, "registration_inr": reg_fee})
        b = title_steps()
        b.append({"id": "release_deeds", "name": f"Registered release deeds by {len(releasing)} heir(s) in favour of {ben['name']}",
                  "office": proc.get("registration_office", region.registration_office), "days": 14, "fee_inr": duty_total, "needs": heirs_needed[-1:]})
        b.append(_svc(region, "mutation", ["release_deeds"], name=f"Mutation into {ben['name']}'s name"))
        b += lender_steps("mutation")
        b, days_b, path_b = _schedule(b)
        routes.append({
            "id": "release", "label": f"B · Release to {ben['name']}",
            "summary": f"The other heirs release their shares to {ben['name']} ({ben['why']}), who then holds and borrows alone.",
            "steps": b, "days": days_b, "critical_path": path_b,
            "cost_inr": sum(s["fee_inr"] for s in b), "duty_inr": duty_total,
            "signatures": [s["name"] for s in releasing] + [ben["name"]], "away_signatures": [s["name"] for s in releasing if not s["lives_in_village"]],
            "borrower": ben["name"], "capacity_inr": int(round(lend_after * ltv, -2)), "per_heir": [{"name": ben["name"], "capacity_inr": int(round(lend_after * ltv, -2))}],
            "value_transfers": transfers,
            "fair_settlement_inr": sum(t["market_value_inr"] for t in transfers),
            "risks": [
                f"The releasing heirs give up about {inr(sum(t['market_value_inr'] for t in transfers))} of land at today's median value; agree and record any settlement before signing.",
                "A release is permanent. Each releasing heir should have independent advice (free legal aid is available through the DLSA).",
            ],
        })

    # C · partition and subdivision
    if len(living) > 1:
        partition_duty = sum(duty(region.code, "partition", s["value"] * guide_total) for s in living[1:]) + duty(region.code, "registration", guide_total)
        c = title_steps()
        c.append({"id": "partition_deed", "name": "Registered partition deed among all heirs", "office": region.registration_office, "days": 21,
                  "fee_inr": partition_duty, "needs": heirs_needed[-1:]})
        sv = services(region)["survey_measurement"]
        c.append({"id": "subdivision", "name": f"Survey subdivision into {len(living)} plots", "office": sv.office, "days": max(sv.days, 60),
                  "fee_inr": sv.fee_inr * len(living), "needs": ["partition_deed"]})
        c.append(_svc(region, "mutation", ["subdivision"], name=f"Separate record of rights for each of {len(living)} heirs"))
        c += lender_steps("mutation", per_heir=True)
        c, days_c, path_c = _schedule(c)
        per = []
        for s in living:
            area = s["value"] * extent_sqm
            small = area < MIN_LENDABLE_SQM
            cap = int(round(lend_after * s["value"] * (1 - FRAGMENT_DISCOUNT if small else 1) * ltv, -2))
            per.append({"name": s["name"], "share": s["share"], "area_sqm": round(area), "capacity_inr": cap, "below_min_plot": small})
        routes.append({
            "id": "partition", "label": "C · Partition and subdivide",
            "summary": "Divide the land by a registered partition deed and a survey subdivision; each heir gets a separate record and borrows alone.",
            "steps": c, "days": days_c, "critical_path": path_c,
            "cost_inr": sum(s["fee_inr"] for s in c), "duty_inr": partition_duty,
            "signatures": [s["name"] for s in living], "away_signatures": [s["name"] for s in away],
            "borrower": "each heir on their own plot", "capacity_inr": sum(p["capacity_inr"] for p in per), "per_heir": per, "value_transfers": [],
            "risks": [
                f"{sum(1 for p in per if p['below_min_plot'])} plot(s) would be under half an acre — many branches will not lend on them, and small plots sell for less." if any(p["below_min_plot"] for p in per) else "Every resulting plot is at least half an acre.",
                "The longest route: the survey subdivision needs notice to neighbours and a field visit.",
            ],
        })

    # D · collateral-free bridge (runs alongside any of the above)
    if lender_limit_inr:
        d_steps = [
            _svc(region, "cultivation_record", []),
            _svc(region, "tax_receipt", []),
            {"id": "family_noc", "name": "Family no-objection for the cultivating heir to borrow", "office": "family (on plain paper)", "days": 3, "fee_inr": 0, "needs": []},
            {"id": "kcc", "name": "Kisan Credit Card / crop loan application", "office": "bank or co-operative branch", "days": 14, "fee_inr": 0,
             "needs": ["cultivation_record", "tax_receipt", "family_noc"]},
        ]
        d_steps, days_d, path_d = _schedule(d_steps)
        who = ben["name"] if ben else living[0]["name"]
        routes.append({
            "id": "bridge", "label": "D · Collateral-free crop loan now (bridge)",
            "summary": f"{who} applies for a crop loan within the collateral-free limit on the cultivation record, while the title is fixed. Replaces moneylender credit; does not fix the title.",
            "steps": d_steps, "days": days_d, "critical_path": path_d, "cost_inr": sum(s["fee_inr"] for s in d_steps), "duty_inr": 0,
            "signatures": [who], "away_signatures": [], "borrower": who, "capacity_inr": lender_limit_inr,
            "per_heir": [{"name": who, "capacity_inr": lender_limit_inr}], "value_transfers": [], "bridge": True,
            "risks": ["Sized by the crop's scale of finance; many branches still ask for the land record or a family NOC.", "Does not clear the title — combine with A, B or C."],
        })

    # transparent ranking (the bridge is advice, not a competitor)
    main = [r for r in routes if not r.get("bridge")]
    if main:
        def norm(vals: list[float], higher_better: bool) -> list[float]:
            lo, hi = min(vals), max(vals)
            return [1.0] * len(vals) if hi == lo else [((v - lo) / (hi - lo)) if higher_better else ((hi - v) / (hi - lo)) for v in vals]

        coord = [len(r["signatures"]) + 2 * len(r["away_signatures"]) for r in main]
        parts = {
            "days": norm([r["days"] for r in main], False), "capacity": norm([r["capacity_inr"] for r in main], True),
            "cost": norm([r["cost_inr"] for r in main], False), "coordination": norm([float(c) for c in coord], False),
        }
        for i, r in enumerate(main):
            r["score"] = round(sum(WEIGHTS[k] * parts[k][i] for k in WEIGHTS) * 100)
            r["score_parts"] = {k: round(parts[k][i], 2) for k in WEIGHTS}
        best = max(main, key=lambda r: r["score"])
        for r in main:
            r["recommended"] = r is best
    first_rupee = min((r["days"] for r in routes), default=None)
    return {
        "status": "compared", "routes": routes, "weights": WEIGHTS, "beneficiary": ben,
        "lending_value_after_fix_inr": lend_after, "first_rupee_days": first_rupee,
        "assumptions": [
            "Stamp duty and registration fees are indicative, computed on the guideline value of what is transferred; check the current rates with the Sub-Registrar.",
            "Borrowing capacity uses the conservative lending value (20th percentile) after the route's fixes, times the lender's loan-to-value.",
            "Days are typical office timelines scheduled by the critical path; real files can take longer — track them under Proceedings.",
        ],
        "disclaimer": "These are options to discuss, not advice to choose one. The family decides; registered deeds should be explained to every signatory.",
    }
