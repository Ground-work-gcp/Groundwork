"""Who holds the land now, in what shares — a *draft* for the revenue authority, never a decision.

The common case in the field: the record still shows a grandfather who died in 1987; his widow
died in 2015; one son died in 2019 leaving his own family; a daughter married and moved to
Chennai. Nobody can say who must sign. This module walks every death **in date order**, passes
each person's share to their heirs under the personal law the family says applies, and keeps
going until every share sits with a living person (or it cannot go further and says why).

Implemented, with exact fractions:

* **Hindu Succession Act, 1956** (as amended in 2005)
  - s.8 and s.10, Class I heirs of a male (widow(s) together one share; each son, daughter and the
    mother one share; each predeceased child's branch one share, divided under rule 4, traced two
    generations); Class II first entries (father; then brothers and sisters).
  - s.15 and s.16 for a female: (a) children, children of predeceased children, and husband;
    (b) heirs of the husband; (c) mother and father; (d) heirs of the father. Section 15(2) sends
    property she inherited from her parents (or from her husband) back to that side when she
    leaves no children.
  - s.6 for ancestral (coparcenary) land: a notional partition among the coparceners at the
    holder's death (daughters are coparceners when he died on or after 9 Sep 2005), the holder's
    share devolving under s.8; before 2005 with no Class I female heir, by survivorship.
* **Indian Succession Act, 1925** (Christians): s.33 spouse one-third with lineal descendants
  (two-thirds, per stirpes), one-half with kindred only; s.42–44 father, else mother and siblings.
* **Muslim personal law** is *not computed*: shares depend on the school and on the full set of
  sharers and residuaries. The result says so and lists who must be identified.

Every step is written out in plain words so a family, a VAO or an advocate can check it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

AMENDMENT_2005 = "2005-09-09"
DISCLAIMER = (
    "Draft computation prepared from the family tree as entered. It is not a legal determination: "
    "succession is decided by the revenue authority (on a legal heir certificate and mutation) or by a civil court."
)
LAW_NAMES = {
    "hindu": "the Hindu Succession Act, 1956",
    "christian": "the Indian Succession Act, 1925",
    "muslim": "Muslim personal law",
    "other": "the applicable personal law",
}


@dataclass
class Person:
    id: str
    name: str
    sex: str = "unknown"
    alive: bool = True
    death: str | None = None
    parents: list[str] = field(default_factory=list)
    spouses: list[str] = field(default_factory=list)
    lives_in_village: bool = True
    consent_status: str = "unknown"


class Tree:
    def __init__(self, people: list[Person]):
        self.p = {x.id: x for x in people}
        self.kids: dict[str, list[str]] = defaultdict(list)
        self.sp: dict[str, set[str]] = defaultdict(set)
        for x in people:
            for parent in x.parents:
                if parent in self.p:
                    self.kids[parent].append(x.id)
            for s in x.spouses:
                if s in self.p:
                    self.sp[x.id].add(s)
                    self.sp[s].add(x.id)
        self.warnings: list[str] = []
        self._warned: set[str] = set()

    def name(self, pid: str) -> str:
        return self.p[pid].name

    def children(self, pid: str) -> list[str]:
        return sorted(self.kids.get(pid, []), key=lambda k: self.p[k].name)

    def spouses(self, pid: str) -> list[str]:
        return sorted(self.sp.get(pid, set()), key=lambda k: self.p[k].name)

    def parent(self, pid: str, sex: str) -> str | None:
        for q in self.p[pid].parents:
            if q in self.p and self.p[q].sex == sex:
                return q
        return None

    def siblings(self, pid: str) -> list[str]:
        out: set[str] = set()
        for q in self.p[pid].parents:
            out.update(self.kids.get(q, []))
        out.discard(pid)
        return sorted(out, key=lambda k: self.p[k].name)

    def alive_at(self, pid: str, date: str | None) -> bool:
        x = self.p[pid]
        if x.alive:
            return True
        if x.death is None:
            if pid not in self._warned:
                self._warned.add(pid)
                self.warnings.append(f"{x.name} is marked deceased without a date of death; treated as having survived the earlier deaths.")
            return True
        if date is None:
            return False
        return x.death > date


Dist = list[tuple[str, Fraction]]


def _equal(groups: list[Dist]) -> Dist:
    """Each group is one equal portion; within a group, the group's own fractions apply."""
    groups = [g for g in groups if g]
    if not groups:
        return []
    share = Fraction(1, len(groups))
    out: dict[str, Fraction] = defaultdict(Fraction)
    for g in groups:
        for pid, f in g:
            out[pid] += share * f
    return list(out.items())


def _together(ids: list[str]) -> Dist:
    return [(i, Fraction(1, len(ids))) for i in ids] if ids else []


# ── Hindu Succession Act ─────────────────────────────────────────────────────


def _branch(t: Tree, child: str, date: str, depth: int) -> Dist:
    """Rule 4 of s.10: the heirs in the branch of a predeceased son or daughter."""
    groups: list[Dist] = []
    if t.p[child].sex == "male":
        widows = [s for s in t.spouses(child) if t.alive_at(s, date)]
        groups.append(_together(widows))
    for g in t.children(child):
        if t.alive_at(g, date):
            groups.append([(g, Fraction(1))])
        elif depth < 2:
            groups.append(_branch(t, g, date, depth + 1))
    return _equal(groups)


def hindu_male(t: Tree, pid: str, date: str) -> tuple[Dist, str]:
    groups: list[Dist] = [_together([s for s in t.spouses(pid) if t.alive_at(s, date)])]
    for c in t.children(pid):
        groups.append([(c, Fraction(1))] if t.alive_at(c, date) else _branch(t, c, date, 1))
    mother = t.parent(pid, "female")
    if mother and t.alive_at(mother, date):
        groups.append([(mother, Fraction(1))])
    dist = _equal(groups)
    if dist:
        return dist, "Hindu Succession Act s.8 and s.10 — Class I heirs share equally; a predeceased child's branch takes one share"
    father = t.parent(pid, "male")
    if father and t.alive_at(father, date):
        return [(father, Fraction(1))], "Hindu Succession Act s.8(b) — no Class I heir; Class II entry I (father)"
    sibs = [s for s in t.siblings(pid) if t.alive_at(s, date)]
    if sibs:
        return _together(sibs), "Hindu Succession Act s.8(b) — no Class I heir or father; Class II entry II (brothers and sisters)"
    return [], "no Class I heir, father or sibling in the tree — later Class II entries and agnates are not computed"


def _children_branches(t: Tree, pid: str, date: str) -> list[Dist]:
    groups: list[Dist] = []
    for c in t.children(pid):
        if t.alive_at(c, date):
            groups.append([(c, Fraction(1))])
        else:
            grand = [g for g in t.children(c) if t.alive_at(g, date)]
            groups.append(_together(grand))
    return [g for g in groups if g]


def hindu_female(t: Tree, pid: str, date: str, source: str) -> tuple[Dist, str]:
    kids = _children_branches(t, pid, date)
    husband = next((s for s in t.spouses(pid) if t.p[s].sex == "male"), None)
    if kids:
        groups = list(kids)
        if husband and t.alive_at(husband, date):
            groups.append([(husband, Fraction(1))])
        return _equal(groups), "Hindu Succession Act s.15(1)(a) — sons, daughters (children of a predeceased child by branch) and husband share equally"
    if source == "father_or_mother":
        father = t.parent(pid, "male")
        if father:
            dist, _ = _heirs_of(t, father, date, "male")
            if dist:
                return dist, "Hindu Succession Act s.15(2)(a) — she left no children; land inherited from her parents goes to her father's heirs"
    if source == "husband" and husband:
        dist, _ = _heirs_of(t, husband, date, "male")
        if dist:
            return dist, "Hindu Succession Act s.15(2)(b) — she left no children; land inherited from her husband goes to his heirs"
    if husband and t.alive_at(husband, date):
        return [(husband, Fraction(1))], "Hindu Succession Act s.15(1)(a) — no children; the husband takes"
    if husband:
        dist, _ = _heirs_of(t, husband, date, "male")
        if dist:
            return dist, "Hindu Succession Act s.15(1)(b) — no children or husband; the heirs of the husband"
    parents = [q for q in (t.parent(pid, "female"), t.parent(pid, "male")) if q and t.alive_at(q, date)]
    if parents:
        return _together(parents), "Hindu Succession Act s.15(1)(c) — mother and father"
    father = t.parent(pid, "male")
    if father:
        dist, _ = _heirs_of(t, father, date, "male")
        if dist:
            return dist, "Hindu Succession Act s.15(1)(d) — the heirs of the father"
    return [], "no heir under s.15(1)(a)–(d) in the tree"


def _heirs_of(t: Tree, pid: str, date: str, sex: str) -> tuple[Dist, str]:
    """Heirs of a person as if they had died on ``date`` (used by s.15(1)(b)/(d) and s.15(2))."""
    return hindu_male(t, pid, date) if sex == "male" else hindu_female(t, pid, date, "other")


# ── Indian Succession Act (Christians) ───────────────────────────────────────


def _descendants(t: Tree, pid: str, date: str, depth: int = 0) -> Dist:
    groups: list[Dist] = []
    for c in t.children(pid):
        if t.alive_at(c, date):
            groups.append([(c, Fraction(1))])
        elif depth < 2:
            groups.append(_descendants(t, c, date, depth + 1))
    return _equal(groups)


def christian(t: Tree, pid: str, date: str) -> tuple[Dist, str]:
    spouse = [s for s in t.spouses(pid) if t.alive_at(s, date)][:1]
    desc = _descendants(t, pid, date)
    father = t.parent(pid, "male")
    kindred: Dist = []
    kin_rule = ""
    if father and t.alive_at(father, date):
        kindred, kin_rule = [(father, Fraction(1))], "s.42 father"
    else:
        mother = t.parent(pid, "female")
        group = [x for x in ([mother] if mother else []) + t.siblings(pid) if x and t.alive_at(x, date)]
        if group:
            kindred, kin_rule = _together(group), "s.43–44 mother, brothers and sisters equally"
    out: dict[str, Fraction] = defaultdict(Fraction)
    if spouse and desc:
        out[spouse[0]] += Fraction(1, 3)
        for k, f in desc:
            out[k] += Fraction(2, 3) * f
        return list(out.items()), "Indian Succession Act s.33(a) — spouse one-third, lineal descendants two-thirds per stirpes"
    if spouse and kindred:
        out[spouse[0]] += Fraction(1, 2)
        for k, f in kindred:
            out[k] += Fraction(1, 2) * f
        return list(out.items()), f"Indian Succession Act s.33(b) — spouse one-half, kindred one-half ({kin_rule})"
    if spouse:
        return [(spouse[0], Fraction(1))], "Indian Succession Act s.33(c) — spouse takes all; no kindred in the tree"
    if desc:
        return desc, "Indian Succession Act s.37–40 — lineal descendants, per stirpes"
    if kindred:
        return kindred, f"Indian Succession Act {kin_rule}"
    return [], "no spouse, descendant or near kindred in the tree"


# ── the devolution walk ──────────────────────────────────────────────────────


def _fmt(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _source_for(t: Tree, deceased: str, heir: str) -> str:
    if heir in t.p[deceased].spouses or deceased in t.p[heir].spouses:
        return "husband" if t.p[deceased].sex == "male" else "other"
    if deceased in t.p[heir].parents:
        return "father_or_mother"
    return "other"


def compute(
    people: list[Person], holder_id: str, *, law: str = "hindu", nature: str = "self_acquired"
) -> dict[str, Any]:
    t = Tree(people)
    law = law if law in LAW_NAMES else "other"
    base: dict[str, Any] = {"law": law, "law_name": LAW_NAMES[law], "nature": nature, "disclaimer": DISCLAIMER}
    if holder_id not in t.p:
        return base | {"status": "not_computed", "shares": [], "steps": [], "assumptions": [], "warnings": ["The recorded holder is not in the family tree."], "unresolved": []}
    holder = t.p[holder_id]
    assumptions = [
        "Everyone in the tree was born before the deaths that concern them, and no one listed was adopted out or disqualified.",
        "No will, gift, sale, release deed or partition is known for the land; any such document changes the result.",
        "Simultaneous deaths are treated as not surviving each other.",
    ]
    if law == "muslim" or law == "other":
        living = [x.name for x in people if x.alive]
        return base | {
            "status": "not_computed", "holder": holder.name, "shares": [], "steps": [], "assumptions": assumptions, "warnings": [],
            "unresolved": [{
                "person": holder.name,
                "reason": (
                    "Shares under Muslim personal law depend on the school followed and on every Quranic sharer and residuary. "
                    "Groundwork does not compute them; an advocate or a qazi should."
                    if law == "muslim" else "The personal law is not one Groundwork computes."
                ),
                "identify": living,
            }],
        }

    # holdings: person -> list[(fraction, source)]
    holdings: dict[str, list[tuple[Fraction, str]]] = defaultdict(list)
    steps: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    processed: set[str] = set()

    first_done = False
    if law == "hindu" and nature == "ancestral" and holder.sex == "male" and not holder.alive and holder.death:
        date = holder.death
        daughters_in = date >= AMENDMENT_2005
        cop = [holder_id]
        for c in t.children(holder_id):
            if t.alive_at(c, date) and (t.p[c].sex == "male" or (t.p[c].sex == "female" and daughters_in)):
                cop.append(c)
            elif not t.alive_at(c, date) and t.p[c].sex == "male":
                cop.extend(g for g in t.children(c) if t.alive_at(g, date) and t.p[g].sex == "male")
        n = len(cop)
    if law == "hindu" and nature == "ancestral" and holder.sex == "male" and not holder.alive and holder.death and n > 1:
        first_done = True
        for c in cop[1:]:
            holdings[c].append((Fraction(1, n), "coparcenary"))
        dist, rule = hindu_male(t, holder_id, date)
        female_class1 = any(t.p[pid].sex == "female" for pid, _ in dist)
        if daughters_in or female_class1:
            rule = f"Hindu Succession Act s.6 — notional partition among {n} coparcener(s); the holder's 1/{n} passes under s.8. " + rule
            share = Fraction(1, n)
        else:
            dist = _together(cop[1:])
            rule = "Hindu Succession Act s.6 (pre-2005, no Class I female heir) — the holder's interest passes to the surviving coparceners by survivorship"
            share = Fraction(1, n) if cop[1:] else Fraction(0)
        for pid, f in dist:
            holdings[pid].append((share * f, _source_for(t, holder_id, pid)))
        steps.append(_step(t, holder_id, date, Fraction(1), rule, [(c, Fraction(1, n)) for c in cop[1:]], dist, share))
        processed.add(holder_id)
        assumptions.append("The land is treated as ancestral coparcenary property, as entered; this is itself a question of fact for the authority.")
        if not daughters_in:
            assumptions.append("The holder died before 9 Sep 2005; the 2005 amendment may give daughters further rights in land still undivided — refer to an advocate.")
    if not first_done:
        holdings[holder_id].append((Fraction(1), "self"))

    for _ in range(200):
        dead = [pid for pid, h in holdings.items() if h and pid not in processed and not t.p[pid].alive]
        if not dead:
            break
        dead.sort(key=lambda pid: (t.p[pid].death is None, t.p[pid].death or ""))
        pid = dead[0]
        processed.add(pid)
        person = t.p[pid]
        if person.death is None:
            total = sum((f for f, _ in holdings[pid]), Fraction(0))
            unresolved.append({"person": person.name, "share": _fmt(total), "reason": "date of death unknown — enter it to pass this share on"})
            continue
        # A woman's share may carry different sources (s.15(2)); distribute each source separately.
        by_source: dict[str, Fraction] = defaultdict(Fraction)
        for f, src in holdings[pid]:
            # Only a Hindu woman's share needs its source kept apart (s.15(2)); everyone else's is one estate.
            by_source[src if law == "hindu" and person.sex == "female" else "estate"] += f
        remaining: list[tuple[Fraction, str]] = []
        for src, amount in sorted(by_source.items()):
            if law == "hindu":
                dist, rule = hindu_male(t, pid, person.death) if person.sex == "male" else hindu_female(t, pid, person.death, src)
                if person.sex == "unknown":
                    rule += " (sex not entered; computed as male — check)"
            else:
                dist, rule = christian(t, pid, person.death)
            if not dist:
                unresolved.append({"person": person.name, "share": _fmt(amount), "reason": rule})
                remaining.append((amount, src))
                continue
            for heir, f in dist:
                holdings[heir].append((amount * f, _source_for(t, pid, heir)))
            steps.append(_step(t, pid, person.death, amount, rule, [], dist, amount))
        holdings[pid] = remaining

    shares = []
    total = Fraction(0)
    for pid, h in holdings.items():
        amount = sum((f for f, _ in h), Fraction(0))
        if amount == 0:
            continue
        total += amount
        x = t.p[pid]
        shares.append({
            "member_id": pid, "name": x.name, "share": _fmt(amount), "value": float(amount), "alive": x.alive,
            "lives_in_village": x.lives_in_village, "consent_status": x.consent_status,
        })
    shares.sort(key=lambda s: (-s["value"], s["name"]))
    status = "computed" if not unresolved and total == 1 else "partial"
    return base | {
        "status": status, "holder": holder.name, "shares": shares, "total": _fmt(total), "steps": steps,
        "assumptions": assumptions, "warnings": t.warnings, "unresolved": unresolved,
        "co_owners": len([s for s in shares if s["alive"]]),
        "away_from_village": [s["name"] for s in shares if s["alive"] and not s["lives_in_village"]],
    }


def _step(t: Tree, pid: str, date: str, held: Fraction, rule: str, kept: Dist, dist: Dist, share: Fraction) -> dict[str, Any]:
    names = ", ".join(f"{t.name(h)} {_fmt(share * f)}" for h, f in sorted(dist, key=lambda x: (-x[1], t.name(x[0]))))
    text = f"On {date} {t.name(pid)} died holding {_fmt(held)}. Under {rule}: {names or 'no one'}."
    if kept:
        text += " Coparceners keep by birth: " + ", ".join(f"{t.name(c)} {_fmt(f)}" for c, f in kept) + "."
    return {
        "date": date, "person": t.name(pid), "person_id": pid, "held": _fmt(held), "rule": rule, "text": text,
        "to": [{"member_id": h, "name": t.name(h), "share": _fmt(share * f)} for h, f in dist],
    }
