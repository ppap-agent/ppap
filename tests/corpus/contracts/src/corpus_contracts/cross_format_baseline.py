"""Known PDF vs Excel outcome divergences. Not a second PASS/NG oracle."""

from __future__ import annotations

from corpus_contracts.cross_format import KnownPairDivergence

KNOWN_PAIR_DIVERGENCES: frozenset[KnownPairDivergence] = frozenset()
