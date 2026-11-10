"""Regression: the Python engine reproduces Magpie's original TypeScript matcher exactly.

``tests/fixtures/recon_golden/expected.json`` was produced by running the unmodified
``lib/recon/{generate,ingest,match,cash}.ts`` under Bun (``scripts/golden_recon.ts``). Every
MatchResult — rule, tier, outcome, confidence, linked ids, inputs, evidence text, failure
class and amount — must be identical, in the same order.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.domain.reconciliation.engine import SOURCE_NAMES, MatchInput, ingest_sources, run_match
from app.domain.reconciliation.engine.cash import build_cash_series
from app.domain.reconciliation.engine.money import to_decimal, to_indian_decimal

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "recon_golden"


@lru_cache(maxsize=1)
def _expected() -> dict:
    return json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _run():
    sources = {name: (FIXTURE / name).read_text(encoding="utf-8") for name in SOURCE_NAMES}
    batch = ingest_sources(sources)
    run = run_match(
        MatchInput(
            payments=batch.payments,
            settlements=batch.settlements,
            recon=batch.recon,
            bank=batch.bank,
            ledger=batch.ledger,
            chargebacks=batch.chargebacks,
        )
    )
    return batch, run


def test_money_formatting_matches_typescript():
    for paise, indian, plain in _expected()["money"]:
        assert to_indian_decimal(paise) == indian
        assert to_decimal(paise) == plain


def test_ingestion_counts_rejections_and_file_stats_match():
    batch, _ = _run()
    expected = _expected()["ingest"]
    counts = {name: len(getattr(batch, name)) for name in expected["counts"]}
    assert counts == expected["counts"]
    assert [r.to_dict() for r in batch.rejections] == expected["rejections"]
    assert [f.to_dict() for f in batch.files] == expected["files"]


def test_every_match_result_is_identical_and_in_order():
    _, run = _run()
    expected = _expected()["results"]
    assert len(run.results) == len(expected)
    for index, (got, want) in enumerate(zip(run.results, expected, strict=True)):
        assert got == want, f"result #{index} differs ({want['rule']})"


def test_run_statistics_match():
    _, run = _run()
    stats = {k: v for k, v in run.stats.items() if k != "elapsedMs"}
    assert stats == _expected()["stats"]


def test_cash_position_matches():
    batch, run = _run()
    queue = sorted(
        (r for r in run.results if r["outcome"] != "AUTO_MATCHED"),
        key=lambda r: -abs(r["amount"]),
    )
    cash = build_cash_series(batch.payments, batch.settlements, batch.bank, run.results, queue)
    assert cash == _expected()["cash"]


def test_precision_against_the_answer_key_has_no_false_matches():
    """Independent of the TS output: score auto-matches against Magpie's truth.json."""
    _, run = _run()
    truth = json.loads((FIXTURE / "truth.json").read_text(encoding="utf-8"))
    # A duplicated statement row is indistinguishable from its original, so the matcher keeps
    # the lower id by convention (match.ts: "a scorer has to treat the pair as unordered").
    twin: dict[str, str] = {}
    for link in truth["links"]:
        if link["class"] == "DUPLICATE_CREDIT":
            copy = link["right"][0]
            original = link["note"].split("Identical to ")[1].split(";")[0]
            twin[copy], twin[original] = original, copy

    def key(lane: str, left: list[str], right: list[str]) -> tuple:
        return lane, tuple(sorted(left)), tuple(sorted(min(i, twin.get(i, i)) for i in right))

    true_links = {key(link["lane"], link["left"], link["right"]) for link in truth["links"] if link["expect"] == "MATCH"}
    auto = [r for r in run.results if r["outcome"] == "AUTO_MATCHED"]
    false_matches = [r for r in auto if key(r["lane"], r["left"], r["right"]) not in true_links]
    assert auto, "the matcher should auto-match most of the batch"
    assert false_matches == [], f"{len(false_matches)} auto-matches are not in the answer key"
