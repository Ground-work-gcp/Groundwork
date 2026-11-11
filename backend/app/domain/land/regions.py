"""Region profiles: record systems vary by state, so nothing below is hard-coded elsewhere.

Each profile names the records a family holds (what Tamil Nadu calls *patta / chitta*,
Karnataka an *RTC / pahani*, Maharashtra a *7/12 extract*), the official who keeps them,
the offices that correct each kind of error, the area units in use, the services a family
pays for (with indicative official fees and typical days), and what each kind of lender
asks for before accepting land as collateral.

Fees and days are **indicative defaults** for planning; every value is meant to be checked
and overridden per district (field observations feed that back — see ``field.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SQM = {
    "sqm": 1.0,
    "sqft": 0.09290304,
    "sqyd": 0.83612736,
    "cent": 40.468564224,
    "acre": 4046.8564224,
    "gunta": 101.17141056,
    "are": 100.0,
    "hectare": 10_000.0,
    "ground": 222.967296,  # 2,400 sq ft (Chennai)
    "kanal": 505.857,
    "marla": 25.2929,
}


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    office: str
    fee_inr: int
    days: int
    needs: tuple[str, ...] = ()
    documents: tuple[str, ...] = ()


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    language: str
    ror: str  # the record of rights
    cultivation: str
    sketch: str
    encumbrance: str
    village_official: str
    revenue_office: str
    survey_office: str
    registration_office: str
    mutation: str
    legal_heir: str
    portal: str
    units: tuple[str, ...]
    bigha_sqm: float | None = None
    annual_growth: float = 0.06
    extent_tolerance: float = 0.03
    local_terms: dict[str, str] = field(default_factory=dict)


REGIONS: dict[str, Region] = {
    r.code: r
    for r in [
        Region(
            "TN", "Tamil Nadu", "ta",
            ror="Patta / Chitta", cultivation="Adangal (A-register extract)", sketch="FMB (Field Measurement Book) sketch",
            encumbrance="Encumbrance Certificate (EC)", village_official="Village Administrative Officer (VAO)",
            revenue_office="Taluk office (Tahsildar)", survey_office="Taluk survey office (Firka surveyor)",
            registration_office="Sub-Registrar's office", mutation="Patta transfer (patta maaruthal)",
            legal_heir="Legal heir certificate (Tahsildar)", portal="Tamil Nilam / TNeGA e-Sevai",
            units=("cent", "acre", "hectare", "are"),
            local_terms={"nanjai": "wet", "punjai": "dry", "manavari": "dry", "natham": "residential", "poramboke": "government"},
        ),
        Region(
            "KA", "Karnataka", "kn",
            ror="RTC (Pahani)", cultivation="RTC crop column (Pahani)", sketch="Survey sketch (Hissa / Podi tippani)",
            encumbrance="Encumbrance Certificate (Form 15/16)", village_official="Village Accountant",
            revenue_office="Taluk office (Tahsildar)", survey_office="Taluk survey office (ADLR)",
            registration_office="Sub-Registrar's office", mutation="Mutation (M.R. entry, khata change)",
            legal_heir="Family tree / survivorship certificate (Tahsildar)", portal="Bhoomi",
            units=("acre", "gunta", "hectare"),
            local_terms={"tari": "wet", "khushki": "dry", "bagayat": "garden", "gomala": "government", "kharab": "government"},
        ),
        Region(
            "AP", "Andhra Pradesh", "te",
            ror="ROR-1B", cultivation="Adangal / Pahani", sketch="FMB sketch", encumbrance="Encumbrance Certificate (EC)",
            village_official="Village Revenue Officer (VRO)", revenue_office="Mandal office (Tahsildar)",
            survey_office="Mandal surveyor", registration_office="Sub-Registrar's office", mutation="Mutation (pattadar passbook update)",
            legal_heir="Family member certificate (Tahsildar)", portal="Meebhoomi", units=("acre", "cent", "hectare"),
            local_terms={"magani": "wet", "metta": "dry", "poramboke": "government"},
        ),
        Region(
            "MH", "Maharashtra", "mr",
            ror="7/12 extract (Satbara)", cultivation="7/12 crop column (Gaon Namuna 12)", sketch="Land record map (Bhu-naksha)",
            encumbrance="Search report / Index-II", village_official="Talathi", revenue_office="Tehsil office (Tahsildar)",
            survey_office="Taluka Inspector of Land Records (TILR)", registration_office="Sub-Registrar's office",
            mutation="Ferfar (mutation entry, register 6)", legal_heir="Varas (heirship) entry / legal heir certificate",
            portal="Mahabhumi", units=("hectare", "are", "acre", "gunta"),
            local_terms={"bagayat": "garden", "jirayat": "dry", "gairan": "government"},
        ),
        Region(
            "UP", "Uttar Pradesh", "hi",
            ror="Khatauni", cultivation="Khasra", sketch="Shajra (village map)", encumbrance="Encumbrance search (Sub-Registrar)",
            village_official="Lekhpal", revenue_office="Tehsil (Tahsildar)", survey_office="Revenue inspector (Kanungo)",
            registration_office="Sub-Registrar's office", mutation="Dakhil-kharij (varasat entry for inheritance)",
            legal_heir="Varasat entry / succession certificate", portal="UP Bhulekh",
            units=("bigha", "hectare", "acre"), bigha_sqm=2529.29,
            local_terms={"banjar": "government", "gram samaj": "government", "abadi": "residential"},
        ),
        Region(
            "IN", "Other state (generic)", "en",
            ror="Record of Rights (RoR)", cultivation="Cultivation / crop record", sketch="Survey sketch / village map",
            encumbrance="Encumbrance Certificate (EC)", village_official="Village revenue official",
            revenue_office="Tehsil / taluk office", survey_office="Survey office", registration_office="Sub-Registrar's office",
            mutation="Mutation", legal_heir="Legal heir certificate", portal="State land records portal",
            units=("acre", "hectare", "cent", "gunta"),
        ),
    ]
}

_SERVICE_ROWS: list[tuple[str, str, str, int, int, tuple[str, ...], tuple[str, ...]]] = [
    # id, name, office (profile field or literal), indicative fee ₹, typical days, needs, documents
    ("death_certificate", "Death certificate", "local body (panchayat / municipality)", 50, 10, (), ("hospital or village record of death",)),
    ("legal_heir_certificate", "Legal heir certificate", "revenue_office", 60, 30, ("death_certificate",), ("death certificate", "family tree affidavit", "identity proof of heirs")),
    ("co_heir_consent", "Co-heirs' consent or no-objection", "family (notarised)", 200, 21, ("legal_heir_certificate",), ("consent letter from each co-heir",)),
    ("mutation", "Mutation in the record of rights", "revenue_office", 60, 30, ("legal_heir_certificate", "co_heir_consent"), ("application", "legal heir certificate", "consents", "current RoR copy")),
    ("encumbrance_certificate", "Encumbrance certificate", "registration_office", 200, 5, (), ("survey number and village",)),
    ("sketch_copy", "Survey sketch copy", "survey_office", 50, 3, (), ("survey number",)),
    ("survey_measurement", "Re-survey / measurement of the plot", "survey_office", 400, 45, ("sketch_copy",), ("application", "sketch copy", "notice to neighbours")),
    ("cultivation_record", "Cultivation record copy", "village_official", 20, 2, (), ()),
    ("tax_receipt", "Land revenue (kist) payment receipt", "village_official", 100, 1, (), ()),
    ("valuation_report", "Valuation by an empanelled valuer", "valuer empanelled with the lender", 3000, 7, ("mutation",), ("RoR", "sketch", "EC")),
    ("legal_scrutiny", "Legal scrutiny report", "advocate on the lender's panel", 2500, 7, ("encumbrance_certificate", "mutation"), ("title documents", "EC", "RoR")),
    ("title_documents", "Certified copies of parent documents", "registration_office", 300, 7, (), ("document numbers from the EC",)),
]


def services(region: Region) -> dict[str, Service]:
    out: dict[str, Service] = {}
    for sid, name, office, fee, days, needs, docs in _SERVICE_ROWS:
        resolved = getattr(region, office, office) if office.isidentifier() else office
        out[sid] = Service(sid, name, resolved, fee, days, needs, docs)
    return out


# What a lender asks for, by requirement id (see readiness.py for how each is checked).
REQUIREMENTS: dict[str, dict[str, str]] = {
    "ror_in_applicant_name": {"label": "Record of rights in the applicant's name", "why": "The lender can only take a charge from the recorded holder.", "fix": "mutation"},
    "no_deceased_holder": {"label": "No deceased person on the record", "why": "A dead holder cannot sign the mortgage; the heirs must be recorded first.", "fix": "mutation"},
    "legal_heir_certificate": {"label": "Legal heir certificate", "why": "Proves who inherited when the holder died.", "fix": "legal_heir_certificate"},
    "co_heir_consent": {"label": "All co-heirs consent", "why": "Undivided land is pledged only with every co-owner's consent.", "fix": "co_heir_consent"},
    "encumbrance_13y": {"label": "Encumbrance certificate, 13 years, no open charge", "why": "Shows no other loan or sale is registered against the land.", "fix": "encumbrance_certificate"},
    "encumbrance_30y": {"label": "Encumbrance certificate, 30 years", "why": "Long-period title search for larger or term loans.", "fix": "encumbrance_certificate"},
    "title_chain": {"label": "Parent documents for the title chain", "why": "Lets the lender's advocate trace how the family came to hold the land.", "fix": "title_documents"},
    "sketch_matches": {"label": "Survey sketch agrees with the recorded extent", "why": "The lender values only what the sketch can locate.", "fix": "survey_measurement"},
    "boundaries_undisputed": {"label": "Boundaries undisputed", "why": "A disputed boundary is a disputed security.", "fix": "survey_measurement"},
    "cultivation_record": {"label": "Current cultivation record", "why": "Crop loans are sized on what is grown.", "fix": "cultivation_record"},
    "land_tax_paid": {"label": "Land revenue paid up to date", "why": "Arrears are a first charge ahead of the bank.", "fix": "tax_receipt"},
    "not_government_land": {"label": "Not government (poramboke / gairan) land", "why": "Government land cannot be mortgaged at all.", "fix": "survey_measurement"},
    "valuation": {"label": "Valuation with a stated range", "why": "Sets the loan amount; Groundwork uses the conservative end.", "fix": "valuation_report"},
    "legal_scrutiny": {"label": "Legal scrutiny report", "why": "The lender's advocate certifies the title is marketable.", "fix": "legal_scrutiny"},
}

LENDERS: dict[str, dict[str, Any]] = {
    "bank_crop_loan": {
        "label": "Bank crop loan / Kisan Credit Card",
        "requirements": ["ror_in_applicant_name", "no_deceased_holder", "cultivation_record", "land_tax_paid", "not_government_land"],
        # Above this, banks take the land as collateral (RBI collateral-free limit for agricultural loans; verify the current circular).
        "collateral_free_limit_inr": 200_000,
        "collateral_requirements": ["encumbrance_13y", "co_heir_consent", "valuation", "sketch_matches"],
        "ltv": 0.6,
    },
    "bank_term_loan": {
        "label": "Bank term loan / mortgage of agricultural land",
        "requirements": [
            "ror_in_applicant_name", "no_deceased_holder", "legal_heir_certificate", "co_heir_consent", "encumbrance_30y", "title_chain",
            "sketch_matches", "boundaries_undisputed", "land_tax_paid", "not_government_land", "valuation", "legal_scrutiny",
        ],
        "collateral_free_limit_inr": 0,
        "collateral_requirements": [],
        "ltv": 0.6,
    },
    "nbfc_lap": {
        "label": "NBFC loan against property",
        "requirements": [
            "ror_in_applicant_name", "no_deceased_holder", "co_heir_consent", "encumbrance_13y", "title_chain", "sketch_matches",
            "not_government_land", "valuation", "legal_scrutiny",
        ],
        "collateral_free_limit_inr": 0,
        "collateral_requirements": [],
        "ltv": 0.5,
    },
    "cooperative": {
        "label": "Primary agricultural co-operative (PACS) / co-operative bank",
        "requirements": ["ror_in_applicant_name", "no_deceased_holder", "cultivation_record", "land_tax_paid", "encumbrance_13y", "not_government_land"],
        "collateral_free_limit_inr": 0,
        "collateral_requirements": [],
        "ltv": 0.6,
    },
}

# Indicative formal rates for the "cost of unbankable land" comparison (annual, simple).
FORMAL_RATES = {"bank_crop_loan": 0.07, "bank_term_loan": 0.10, "nbfc_lap": 0.16, "cooperative": 0.07}

GLOSSARY: dict[str, str] = {
    "ror": "The government record of who holds the land, how big it is and what kind of land it is.",
    "cultivation": "The village record of who cultivates the land and what was grown, season by season.",
    "sketch": "The survey drawing of the plot: its shape, its measurements and the neighbouring survey numbers.",
    "encumbrance": "A list of every registered sale, mortgage, gift or lease on the survey number for a period of years.",
    "mutation": "Changing the name on the record of rights after a sale, gift, partition or death.",
    "legal_heir": "The revenue office's certificate naming the people a deceased person left behind.",
    "survey_number": "The plot's number in the village survey; a subdivision (such as 4A) marks a part of it.",
    "guideline_value": "The government's minimum value per unit area, used for stamp duty. It is not the market price.",
}


def region(code: str | None) -> Region:
    return REGIONS.get((code or "").upper(), REGIONS["IN"])


def catalog() -> dict[str, Any]:
    out = []
    for r in REGIONS.values():
        out.append({
            "code": r.code, "name": r.name, "language": r.language, "units": list(r.units),
            "records": {
                "ror": r.ror, "cultivation": r.cultivation, "sketch": r.sketch, "encumbrance": r.encumbrance,
                "mutation": r.mutation, "legal_heir": r.legal_heir,
            },
            "offices": {
                "village_official": r.village_official, "revenue_office": r.revenue_office, "survey_office": r.survey_office,
                "registration_office": r.registration_office, "portal": r.portal,
            },
            "services": [s.__dict__ | {"needs": list(s.needs), "documents": list(s.documents)} for s in services(r).values()],
            "local_terms": r.local_terms,
        })
    return {
        "regions": out,
        "lenders": {k: {"label": v["label"], "requirements": v["requirements"], "ltv": v["ltv"], "collateral_free_limit_inr": v["collateral_free_limit_inr"]} for k, v in LENDERS.items()},
        "requirements": REQUIREMENTS,
        "glossary": GLOSSARY,
        "note": "Fees, days and limits are indicative planning defaults. Check them with the local office and lender.",
    }


# ── proceedings: how each office processes an application, and where to go when it stalls ──
#
# Timelines are indicative service timelines (citizen charters / right-to-services notifications
# differ by state and change). They drive reminders and escalation drafts, never a legal claim.

PROCEEDING_KINDS: dict[str, dict[str, Any]] = {
    "legal_heir_certificate": {"label": "Legal heir certificate", "office": "revenue_office", "service": "legal_heir_certificate",
                               "flow": ["draft", "filed", "field_verification", "hearing", "ordered"]},
    "mutation": {"label": "Mutation (name transfer)", "office": "revenue_office", "service": "mutation",
                 "flow": ["draft", "filed", "field_verification", "notice_period", "hearing", "ordered"]},
    "encumbrance_certificate": {"label": "Encumbrance certificate", "office": "registration_office", "service": "encumbrance_certificate",
                                "flow": ["draft", "filed", "ordered"]},
    "survey_measurement": {"label": "Survey / measurement", "office": "survey_office", "service": "survey_measurement",
                           "flow": ["draft", "filed", "notice_period", "field_verification", "ordered"]},
    "name_correction": {"label": "Name correction in the record", "office": "revenue_office", "service": "mutation",
                        "flow": ["draft", "filed", "field_verification", "hearing", "ordered"]},
    "appeal": {"label": "Appeal / revision", "office": "appellate_office", "service": "",
               "flow": ["draft", "filed", "hearing", "ordered"]},
    "grievance": {"label": "Grievance petition", "office": "grievance_portal", "service": "",
                  "flow": ["draft", "filed", "ordered"]},
    "dispute_mediation": {"label": "Pre-litigation mediation (free legal aid)", "office": "District Legal Services Authority (DLSA)", "service": "",
                          "flow": ["draft", "filed", "hearing", "ordered"]},
}
TERMINAL = {"ordered", "rejected", "withdrawn"}
STATUS_LABELS = {
    "draft": "Draft — not yet filed", "filed": "Filed, acknowledgement received", "field_verification": "Field verification by the village / revenue officer",
    "notice_period": "Public notice — objection window open", "hearing": "Hearing", "ordered": "Order passed", "rejected": "Rejected", "withdrawn": "Withdrawn",
}

# Per-region process facts. sla: indicative days to dispose; objection_days: public-notice window for mutation.
PROCESS: dict[str, dict[str, Any]] = {
    "TN": {"sla": {"legal_heir_certificate": 30, "mutation": 30, "encumbrance_certificate": 7, "survey_measurement": 45, "name_correction": 30, "appeal": 60, "grievance": 30, "dispute_mediation": 90},
           "objection_days": 15, "appellate_office": "Revenue Divisional Officer (RDO)", "collector": "District Collector",
           "grievance_portal": "CM Helpline (1100) / CM Special Cell", "rts_law": "", "online": "TNeGA e-Sevai / Tamil Nilam",
           "stamp": {"release": (0.01, 40_000, 0), "partition": (0.01, 25_000, 0), "registration": (0.01, 4_000, 0)}},
    "KA": {"sla": {"legal_heir_certificate": 21, "mutation": 30, "encumbrance_certificate": 7, "survey_measurement": 60, "name_correction": 30, "appeal": 60, "grievance": 30, "dispute_mediation": 90},
           "objection_days": 30, "appellate_office": "Assistant Commissioner (sub-division)", "collector": "Deputy Commissioner",
           "grievance_portal": "IPGRS / Janaspandana", "rts_law": "Karnataka Sakala Services Act, 2011", "online": "Bhoomi / Nadakacheri",
           "stamp": {"release": (0.0, 0, 1_000), "partition": (0.0, 0, 1_000), "registration": (0.01, 0, 0)}},
    "AP": {"sla": {"legal_heir_certificate": 30, "mutation": 30, "encumbrance_certificate": 7, "survey_measurement": 45, "name_correction": 30, "appeal": 60, "grievance": 30, "dispute_mediation": 90},
           "objection_days": 15, "appellate_office": "Revenue Divisional Officer (RDO)", "collector": "District Collector",
           "grievance_portal": "Spandana (Meekosam)", "rts_law": "", "online": "Meebhoomi / village secretariat",
           "stamp": {"release": (0.01, 0, 0), "partition": (0.01, 0, 0), "registration": (0.005, 0, 0)}},
    "MH": {"sla": {"legal_heir_certificate": 21, "mutation": 30, "encumbrance_certificate": 7, "survey_measurement": 60, "name_correction": 30, "appeal": 60, "grievance": 30, "dispute_mediation": 90},
           "objection_days": 15, "appellate_office": "Sub-Divisional Officer (SDO)", "collector": "District Collector",
           "grievance_portal": "Aaple Sarkar", "rts_law": "Maharashtra Right to Public Services Act, 2015", "online": "Mahabhumi / e-Ferfar",
           "stamp": {"release": (0.0, 0, 200), "partition": (0.01, 0, 0), "registration": (0.01, 30_000, 0)}},
    "UP": {"sla": {"legal_heir_certificate": 15, "mutation": 45, "encumbrance_certificate": 7, "survey_measurement": 60, "name_correction": 45, "appeal": 90, "grievance": 30, "dispute_mediation": 90},
           "objection_days": 30, "appellate_office": "Sub-Divisional Magistrate (SDM)", "collector": "District Magistrate",
           "grievance_portal": "IGRS / Jansunwai", "rts_law": "Uttar Pradesh Janhit Guarantee Adhiniyam, 2011", "online": "UP Bhulekh / e-District",
           "stamp": {"release": (0.0, 0, 5_000), "partition": (0.01, 0, 0), "registration": (0.01, 0, 0)}},
    "IN": {"sla": {"legal_heir_certificate": 30, "mutation": 30, "encumbrance_certificate": 7, "survey_measurement": 60, "name_correction": 30, "appeal": 60, "grievance": 30, "dispute_mediation": 90},
           "objection_days": 15, "appellate_office": "Sub-divisional revenue officer", "collector": "District Collector",
           "grievance_portal": "CPGRAMS / state grievance portal", "rts_law": "", "online": "state land records portal",
           "stamp": {"release": (0.01, 0, 0), "partition": (0.01, 0, 0), "registration": (0.01, 0, 0)}},
}


def process(code: str | None) -> dict[str, Any]:
    return PROCESS.get((code or "").upper(), PROCESS["IN"])


def duty(code: str | None, instrument: str, value_inr: float) -> int:
    """Indicative stamp duty or registration fee on ``value_inr``: rate × value, capped, with a floor/flat amount."""
    rate, cap, flat = process(code)["stamp"][instrument]
    amount = value_inr * rate
    if cap:
        amount = min(amount, cap)
    return int(round(max(amount, flat)))


def office_for(reg: Region, kind: str) -> str:
    key = PROCEEDING_KINDS[kind]["office"]
    proc = process(reg.code)
    if key in proc:
        return proc[key]
    return getattr(reg, key, key)
