"""V2 PDF↔Excel cross-format semantic contract.

This is not a second expected-status oracle. The human CSV remains correctness
authority. This module only asks whether the PDF path and Excel path for the
same business document diverged in a way that is not already recorded.
Comparison uses the canonical ExecutionOutcome. Suggestion text is not part
of the comparison.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ppap_corpus_contracts.execution_outcome import MISSING_OUTCOME, ExecutionOutcome, O
from ppap_corpus_contracts.v2_assets_shared import FileCase, normalize_file_name


def pair_id_for_file_name(file_name: str) -> str:
    return normalize_file_name(Path(file_name).stem)


@dataclass(frozen=True)
class CrossFormatPair:
    pair_id: str
    excel: FileCase
    pdf: FileCase


@dataclass(frozen=True)
class KnownPairDivergence:
    pair_id: str
    rule_index: int
    pdf: ExecutionOutcome
    excel: ExecutionOutcome

    @property
    def location(self) -> tuple[str, int]:
        return (self.pair_id, self.rule_index)


@dataclass(frozen=True)
class PairRuleVerdict:
    pair_id: str
    rule_index: int
    kind: str
    pdf: ExecutionOutcome
    excel: ExecutionOutcome


def discover_v2_cross_format_pairs(
    excel_cases: Sequence[FileCase],
    pdf_cases: Sequence[FileCase],
) -> list[CrossFormatPair]:
    excel_by_id: dict[str, list[FileCase]] = defaultdict(list)
    pdf_by_id: dict[str, list[FileCase]] = defaultdict(list)
    for case in excel_cases:
        excel_by_id[pair_id_for_file_name(case.file_name)].append(case)
    for case in pdf_cases:
        pdf_by_id[pair_id_for_file_name(case.file_name)].append(case)

    ambiguous: list[str] = []
    for pair_id in sorted(set(excel_by_id) | set(pdf_by_id)):
        excel_hits = excel_by_id.get(pair_id, [])
        pdf_hits = pdf_by_id.get(pair_id, [])
        if len(excel_hits) > 1 or len(pdf_hits) > 1:
            ambiguous.append(
                f"- pair={pair_id} excel={[case.file_name for case in excel_hits]} "
                f"pdf={[case.file_name for case in pdf_hits]}"
            )
    if ambiguous:
        raise AssertionError("V2 cross-format pairs ambiguous:\n" + "\n".join(ambiguous))

    pairs = [
        CrossFormatPair(pair_id=pair_id, excel=excel_by_id[pair_id][0], pdf=pdf_by_id[pair_id][0])
        for pair_id in sorted(set(excel_by_id) & set(pdf_by_id))
    ]
    if not pairs:
        raise AssertionError("No V2 PDF/Excel pairs discovered from oracle assets")
    return pairs


def _known_map(
    known: Iterable[KnownPairDivergence],
) -> dict[tuple[str, int], KnownPairDivergence]:
    return {item.location: item for item in known}


def classify_pair_rule(
    pair_id: str,
    rule_index: int,
    pdf: ExecutionOutcome,
    excel: ExecutionOutcome,
    known: Iterable[KnownPairDivergence],
) -> PairRuleVerdict:
    record = _known_map(known).get((pair_id, rule_index))
    if pdf.semantic == excel.semantic:
        kind = "stale" if record is not None else "consistent"
    elif record is None:
        kind = "new"
    elif record.pdf.semantic == pdf.semantic and record.excel.semantic == excel.semantic:
        kind = "allowed"
    else:
        kind = "changed"
    return PairRuleVerdict(pair_id=pair_id, rule_index=rule_index, kind=kind, pdf=pdf, excel=excel)


def classify_pair_rules(
    pair_id: str,
    pdf_outcomes: Mapping[int, ExecutionOutcome],
    excel_outcomes: Mapping[int, ExecutionOutcome],
    known: Iterable[KnownPairDivergence],
) -> list[PairRuleVerdict]:
    rules = sorted(set(pdf_outcomes) | set(excel_outcomes))
    return [
        classify_pair_rule(
            pair_id,
            rule,
            pdf_outcomes.get(rule, MISSING_OUTCOME),
            excel_outcomes.get(rule, MISSING_OUTCOME),
            known,
        )
        for rule in rules
    ]


def D(
    pair_id: str,
    rule_index: int,
    pdf: ExecutionOutcome | str,
    excel: ExecutionOutcome | str,
) -> KnownPairDivergence:
    return KnownPairDivergence(
        pair_id=pair_id,
        rule_index=rule_index,
        pdf=pdf if isinstance(pdf, ExecutionOutcome) else O(pdf),
        excel=excel if isinstance(excel, ExecutionOutcome) else O(excel),
    )
