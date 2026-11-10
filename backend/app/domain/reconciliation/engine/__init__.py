"""Magpie's reconciliation engine, ported to Python with result-level regression tests.

``match``/``ingest``/``cash`` reproduce the original TypeScript exactly (see
``tests/unit/test_recon_engine_golden.py``). ``candidates``, ``tolerance``, ``parse`` and
``money`` are the shared primitives the property-case lane is built from.
"""

from .ingest import SOURCE_NAMES, IngestedBatch, ingest_sources
from .match import MatchInput, MatchRun, run_match

__all__ = ["SOURCE_NAMES", "IngestedBatch", "MatchInput", "MatchRun", "ingest_sources", "run_match"]
