"""A land value that is honest about how little the market says — always a range, never a single number.

Rural plots rarely have comparable sales, and the ones that exist are distorted: sale deeds are
often registered at the government guideline value to keep stamp duty down, so the declared
price is a floor, not the price. Overestimates harm the borrower (more debt than the land will
carry) more than underestimates harm the lender, so the method is deliberately asymmetric.

1. **Comparables, adjusted to the subject**: for time (the region's annual growth), size (larger
   plots sell for less per unit, elasticity ``SIZE_ELASTICITY``), kind of land and road access.
2. **Weighted and robust**: weights fall with age (5-year scale) and distance (3 km scale), and
   with dissimilarity; deeds declared at or below the guideline value are down-weighted as
   *censored*; outliers are down-weighted by a Huber step on the median absolute deviation.
3. **Shrinkage to the guideline value**: in log space the guideline value is a prior
   (σ = 0.40) and the comparables a likelihood whose spread comes from their own dispersion and
   their effective number (Kish), pooled with a typical rural dispersion so that two sales cannot
   look precise. With two comparables the answer stays near the guideline value
   and the range stays wide; with twenty it follows the market.
4. **A stated range and a confidence**: P10–P50–P90 from the posterior predictive, and a
   confidence label from the effective sample size and the spread.
5. **A lending value below the median**: the 20th percentile, less a *title haircut* for each
   unresolved record problem (deceased holder, extent mismatch, boundary conflict, missing
   consents). A loan ceiling applies the lender's loan-to-value to that figure, never to P50.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

SIZE_ELASTICITY = 0.12
PRIOR_SIGMA = 0.40
WEAK_PRIOR_SIGMA = 1.50
IDIOSYNCRATIC_SIGMA = 0.10
SPREAD_FLOOR = 0.15
TYPICAL_SPREAD = 0.35
PSEUDO_SALES = 3.0
Z = {"p10": -1.2816, "p20": -0.8416, "p50": 0.0, "p90": 1.2816}
CLASS_FACTOR = {"wet": 1.0, "garden": 1.1, "dry": 0.65, "residential": 2.5, "": 1.0}
HAIRCUTS = {
    "deceased_holder": (0.10, "the record is still in a deceased person's name"),
    "extents": (0.08, "the extent differs between records"),
    "boundaries": (0.10, "a boundary is unconfirmed or disputed"),
    "survey_numbers": (0.05, "the survey number or subdivision differs between records"),
    "encumbrance": (0.12, "an open charge appears on the encumbrance certificate"),
    "consents": (0.05, "not every co-heir has consented"),
    "land_class": (0.05, "the records disagree on the kind of land"),
}
MAX_HAIRCUT = 0.40


def inr(amount: int | float | None) -> str:
    """Indian digit grouping: 1703100 → ₹17,03,100."""
    if amount is None:
        return "—"
    n = int(round(amount))
    sign, n = ("-" if n < 0 else ""), abs(n)
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{sign}₹{s}"


def _years(a: str, b: date) -> float:
    try:
        y, m, d = (int(x) for x in a[:10].split("-"))
        return max(0.0, (b - date(y, m, d)).days / 365.25)
    except (ValueError, TypeError):
        return 0.0


def _wmedian(values: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(values, weights, strict=True))
    half, run = sum(weights) / 2, 0.0
    for v, w in pairs:
        run += w
        if run >= half:
            return v
    return pairs[-1][0]


def estimate(
    subject: dict[str, Any],
    comparables: list[dict[str, Any]],
    *,
    growth: float = 0.06,
    issues: dict[str, int] | None = None,
    consents_missing: int = 0,
    claimed_value_inr: int | None = None,
    ltv: float = 0.6,
    today: date | None = None,
) -> dict[str, Any]:
    """``subject``: extent_sqm, land_class, road_access, guideline_inr_per_sqm.
    ``comparables``: id, sale_date, extent_sqm, consideration_inr, land_class, distance_km, road_access, same_village."""
    today = today or date.today()
    area = float(subject.get("extent_sqm") or 0)
    if area <= 0:
        return {"status": "insufficient", "reason": "The plot's extent is not known; enter it on the parcel or a record."}
    guide = subject.get("guideline_inr_per_sqm")
    cls = subject.get("land_class") or ""
    used: list[dict[str, Any]] = []
    xs: list[float] = []
    ws: list[float] = []
    for c in comparables:
        ext, price = float(c.get("extent_sqm") or 0), float(c.get("consideration_inr") or 0)
        if ext <= 0 or price <= 0:
            continue
        per = price / ext
        age = _years(str(c.get("sale_date", "")), today)
        time_f = (1 + growth) ** age
        size_f = (ext / area) ** SIZE_ELASTICITY
        class_f = CLASS_FACTOR.get(cls, 1.0) / CLASS_FACTOR.get(c.get("land_class") or "", 1.0)
        road_f = 1.0
        if bool(c.get("road_access")) != bool(subject.get("road_access")):
            road_f = 0.85 if c.get("road_access") else 1 / 0.85
        adjusted = per * time_f * size_f * class_f * road_f
        dist = c.get("distance_km")
        dist = 0.5 if dist is None and c.get("same_village") else 3.0 if dist is None else float(dist)
        w = math.exp(-age / 5) * math.exp(-dist / 3) * (1.0 if (c.get("land_class") or "") == cls else 0.6)
        censored = bool(guide) and per <= float(guide) * 1.02
        if censored:
            w *= 0.5
        used.append({
            "id": c.get("id"), "sale_date": c.get("sale_date"), "survey_number": c.get("survey_number", ""), "village": c.get("village", ""),
            "price_per_sqm": round(per, 2), "adjusted_per_sqm": round(adjusted, 2),
            "adjustments": {"time": round(time_f, 3), "size": round(size_f, 3), "class": round(class_f, 3), "road": round(road_f, 3)},
            "weight": w, "censored_at_guideline": censored, "age_years": round(age, 1), "distance_km": dist,
        })
        xs.append(math.log(adjusted))
        ws.append(w)

    n_eff, mu_c, spread = 0.0, None, None
    if xs:
        med = _wmedian(xs, ws)
        mad = _wmedian([abs(x - med) for x in xs], ws)
        scale = 1.4826 * mad + 0.05
        for i, x in enumerate(xs):
            r = abs(x - med) / scale
            if r > 1.5:
                # Asymmetric: a sale far *above* the rest is discounted harder than one below it,
                # because an overestimate is the error that hurts the borrower.
                ws[i] *= (1.5 / r) ** 2 if x > med else 1.5 / r
                used[i]["outlier_downweighted"] = True
        total = sum(ws)
        n_eff = total**2 / sum(w * w for w in ws) if total else 0.0
        mu_c = sum(w * x for w, x in zip(ws, xs, strict=True)) / total
        var = sum(w * (x - mu_c) ** 2 for w, x in zip(ws, xs, strict=True)) / total
        # Two sales cannot tell us how dispersed the market is: pool their spread with a typical
        # rural dispersion (TYPICAL_SPREAD, worth PSEUDO_SALES observations) so a thin market stays wide.
        pooled = (n_eff * var + PSEUDO_SALES * TYPICAL_SPREAD**2) / (n_eff + PSEUDO_SALES)
        spread = max(math.sqrt(pooled), SPREAD_FLOOR)
        for i, u in enumerate(used):
            u["weight"] = round(ws[i] / total, 3)

    if guide:
        mu0, s0, prior = math.log(float(guide)), PRIOR_SIGMA, "guideline value"
    elif mu_c is not None:
        mu0, s0, prior = mu_c, WEAK_PRIOR_SIGMA, "none (no guideline value entered)"
    else:
        return {"status": "insufficient", "reason": "Neither a guideline value nor any comparable sale is available."}

    if mu_c is not None and n_eff > 0:
        se = spread / math.sqrt(n_eff)
        t0, tc = 1 / s0**2, 1 / se**2
        mu = (t0 * mu0 + tc * mu_c) / (t0 + tc)
        s_post = math.sqrt(1 / (t0 + tc))
        market_weight = tc / (t0 + tc)
    else:
        mu, s_post, market_weight = mu0, s0, 0.0
    s_pred = math.sqrt(s_post**2 + IDIOSYNCRATIC_SIGMA**2)

    def q(z: float) -> int:
        return int(round(math.exp(mu + z * s_pred) * area, -2))

    issues = issues or {}
    haircut, reasons = 0.0, []
    for kind, (cut, why) in HAIRCUTS.items():
        present = consents_missing > 0 if kind == "consents" else issues.get(kind, 0) > 0
        if present:
            haircut += cut
            reasons.append({"kind": kind, "cut": cut, "why": why})
    haircut = min(haircut, MAX_HAIRCUT)
    p20 = q(Z["p20"])
    lending = int(round(p20 * (1 - haircut), -2))
    confidence = "high" if n_eff >= 5 and s_pred <= 0.22 else "medium" if n_eff >= 2 and s_pred <= 0.38 else "low"
    out: dict[str, Any] = {
        "status": "estimated",
        "extent_sqm": area,
        "p10_inr": q(Z["p10"]), "p50_inr": q(Z["p50"]), "p90_inr": q(Z["p90"]), "p20_inr": p20,
        "per_sqm_p50": round(math.exp(mu), 2),
        "range_ratio": round(math.exp(2 * 1.2816 * s_pred), 2),
        "confidence": confidence,
        "sigma_log": round(s_pred, 3),
        "effective_comparables": round(n_eff, 2),
        "market_weight": round(market_weight, 3),
        "prior": prior,
        "guideline_total_inr": int(round(float(guide) * area, -2)) if guide else None,
        "lending_value_inr": lending,
        "title_haircut": round(haircut, 2),
        "haircut_reasons": reasons,
        "ltv": ltv,
        "loan_ceiling_inr": int(round(lending * ltv, -2)),
        "comparables": used,
        "method": (
            "Comparables adjusted for time, size, land class and road access; weighted by recency, distance and similarity; "
            "deeds declared at the guideline value treated as floors; outliers down-weighted; shrunk in log space towards the "
            "guideline value. Lending value = 20th percentile less a title haircut."
        ),
        "warnings": [],
    }
    if n_eff < 2:
        out["warnings"].append("Fewer than two effective comparable sales: the estimate leans on the guideline value and the range is wide.")
    if claimed_value_inr:
        out["claimed_value_inr"] = claimed_value_inr
        if claimed_value_inr > out["p90_inr"]:
            out["warnings"].append(
                f"The claimed value {inr(claimed_value_inr)} is above the 90th percentile ({inr(out['p90_inr'])}). Borrowing against it risks more debt than the land can carry."
            )
    return out
