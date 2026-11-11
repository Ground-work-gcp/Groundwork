# Rural land records (India) — problem 1.2

> **Land records are too unclear to sell, inherit or borrow against.**
> Groundwork prepares the case. Ownership decisions stay with the revenue authorities and the
> courts, and every valuation carries a stated range and confidence.

This module lives in `backend/app/domain/land/` (pure engines plus a service layer), is served
under `/api/v1/cases/{id}/land/…`, `/api/v1/land/…` and two narrow public endpoints, and appears
in the app as the **Land records** case tab and the **Field** page.

## How each part of the brief is answered

| The brief says | What Groundwork does | Where |
|---|---|---|
| Ownership, inheritance, mutation and boundary records are hard to understand | Every record (patta/chitta, adangal, FMB, EC, sale deed, death and legal heir certificates) is read from the uploaded document with page provenance and laid side by side in a **concordance matrix** | `parse.py`, `concordance.py` |
| …for families whose land is their main asset | A plain-language **explanation in Tamil, Hindi or English** (other languages via Gemini, labelled machine-translated), printable, built only from computed values | `explain.py` |
| A record that still shows a parent who died years ago | Detected automatically (record holder ↔ family tree ↔ death certificate, matched across scripts) as a **critical finding**, with how many years the record has lagged | `concordance.py`, `names.py` |
| Land passed down without formal partition | A **succession engine** walks every death in date order with exact fractions (Hindu Succession Act s.6/8/10/15/16 incl. 2005 amendment and s.15(2); Indian Succession Act s.33–44); Muslim law is explicitly *not computed* | `succession.py` |
| Rural property rarely has a credible valuation | A **conservative Bayesian valuation**: comparables adjusted for time, size, class and road; deeds at guideline value treated as floors; asymmetric outlier handling; shrinkage toward the guideline value; pooled dispersion so thin markets stay wide | `valuation.py` |
| Valuations must avoid overestimates that harm borrowers | Always **P10–P50–P90 with a confidence label**; the **lending value is the 20th percentile less a title haircut**; a claimed value above P90 is flagged | `valuation.py` |
| …so it cannot be used as collateral | **Lender readiness** per lender (bank crop loan/KCC, bank term loan, NBFC LAP, co-operative) with a **critical-path fix plan** (offices, documents to carry, indicative fees, days) and the **RBI collateral-free limit** surfaced first | `readiness.py`, `regions.py` |
| The VAO, taluk office, document writer and lawyer each hold one piece | Every disagreement names **which office corrects it**; the **mutation packet** assembles the pieces into one draft application with a document checklist | `concordance.py`, `documents.py` |
| Banks prefer to lend against crops or gold | A **land credit passport** — hashed, anchored in the audit chain, issued only after two-person approval — that a branch verifies with a code and SHA-256, without seeing names | `documents.py`, `api/v1/public.py` |
| Co-heirs, including those who left the village | **Single-use consent links** (SHA-256 of the token stored, 30-day expiry): the co-heir sees their draft share and how it was worked out, and consents, objects or asks for information from a phone. Replies are audited as `heir` | `api/v1/land.py`, `api/v1/public.py` |
| Revenue and survey offices; document writers and advocates; banks, NBFCs and cooperatives | Region profiles name each actor per state (VAO / Village Accountant / Talathi / Lekhpal; Tahsildar; survey office; Sub-Registrar) and each lender's checklist | `regions.py` |
| Few comparable transactions | Comparables are **shared across the organisation's cases**, sale deeds uploaded to any case become comparables automatically, and the estimate says how many *effective* comparables it rests on | `service.py` |
| Record systems vary by region | Profiles for Tamil Nadu, Karnataka, Andhra Pradesh, Maharashtra, Uttar Pradesh and a generic one: record names, units (cents, guntas, hectare-are, bigha), local land-class terms (nanjai/punjai, poramboke, gairan), fees | `regions.py`, `units.py` |
| Names, extents or boundaries disagree | Names matched across **Tamil, Devanagari, Kannada, Telugu, Malayalam, Bengali, Gujarati, Gurmukhi and Odia** scripts with one Unicode offset table plus a phonetic key; extents compared within a regional tolerance and against the **shoelace area** of measured corners; boundaries checked for **reciprocity** with neighbours' records | `names.py`, `units.py`, `concordance.py` |
| Disputes that run for years; moneylenders | The **cost ledger** records rupees and days, receipted or not, flags payments above the official fee, and prices informal loans against the formal rate | `money.py` |
| Agentic AI | The **land case agent** plans over an allowlisted toolset, runs the deterministic engines, records each step in the audit chain as `actor_kind = ai`, turns what only people can do into **hand-offs**, and *proposes* (never executes) evidence requests for the critical path | `agent.py` |

## The workflow, as it happens in the field

A land case (`kind = rural_land`) has its own lifecycle, and every gate is computed from the case:

| Stage | Done when |
|---|---|
| Plot and family | survey number entered and at least one family member or the recorded holder |
| Records gathered | record of rights, survey sketch and encumbrance certificate on file |
| Records reconciled | compared, and every name/extent/survey/boundary difference acknowledged with (or resolved by) the office |
| Heirs and shares | draft shares computed — or no deceased holder |
| Co-heir consent | **signed** consent from every living co-owner, no objection (an online "yes" is intent, not consent an office can file) |
| Mutation recorded | the mutation proceeding has an order — or the record shows living holders |
| Lender-ready | readiness verdict *ready* for the chosen lender, or an active land credit passport |
| Verified case history | resolved through a two-person approval |

**Office proceedings** (`domain/land/proceedings.py`) model each application — legal heir certificate,
mutation, EC, survey, name correction, appeal, grievance, DLSA mediation — as the office processes it:
filed → field verification → objection window → hearing → order. Status moves only on dated events.
Each file carries the region's indicative service timeline and objection window, a health signal
(on track · at risk · overdue · stalled · done), and who it is waiting on (the office, the family,
the notice period, a hearing date). An overdue or stalled file gets a drafted representation to the
next authority — RDO/SDO/SDM/Assistant Commissioner (citing the state's right-to-services law where
one exists), then the Collector, then the state grievance portal — and the escalation is itself
tracked as a child proceeding. A filing check lists what the office will ask for before filing.
An objection proposes free pre-litigation mediation through the District Legal Services Authority.

**Family pathways** (`domain/land/pathways.py`) compare the real routes once heirs are known — joint
mutation, release deeds to the heir who farms the land, registered partition with survey subdivision,
and a collateral-free crop loan as a bridge — each as a scheduled dependency graph with fees, stamp
duty and registration fees on the guideline value transferred, the signatures required (and who must
travel or give a power of attorney), borrowing capacity per heir after the route's fixes, what each
releasing heir gives up, and a transparent weighted score. The family decides.

The **Flow** graph for a land case is its own DAG (documents → records → concordance; family →
shares → consents → proceedings → mutation → readiness), with the critical path and a Dijkstra
least-effort route that includes the collateral-free crop loan as an alternative edge. Overdue
files, objections and co-heir questions appear in **Needs you**.

## The six field questions

The **Field** page records field visits against the six questions and answers each one twice —
from what the field reports and from what the organisation's own cases show:

1. How many families hold land still recorded in a deceased person's name, and what stops them correcting it?
2. What does the local branch ask for before accepting land as collateral? *(Recorded asks flow into every nearby case's readiness checklist.)*
3. How does this village currently decide what a plot is worth?
4. Where do records disagree most often: names, extents or boundaries?
5. Who settles boundary disputes today, and how long does it take?
6. What does a family spend, in money and days, to sort out a land issue?

"Following the money": who families pay to read their own records, and how much.

## Boundaries, enforced in code

* No endpoint changes who owns anything. Succession results are labelled drafts; the mutation
  packet is a draft for the family to sign and carry.
* Issuing a passport is a workflow action (`land.issue_passport`) that a *different* attorney or
  admin must approve by typing the exact phrase.
* The agent proposes; people approve. Its steps and its runs are hashed and audited.
* Valuation never returns a single number, and the figure offered for lending sits below the median.
* Fees, days and the collateral-free limit are indicative defaults and say so.

## Try it

```bash
python scripts/seed_demo.py   # also creates "Ramasamy heirs — S.No. 123/4A, Keezhaiyur"
```

Sign in as `pat` / `demo-password-1`, open the case, then **Land records → Agent → Prepare this case**.
