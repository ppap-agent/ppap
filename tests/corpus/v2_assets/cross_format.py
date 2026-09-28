"""V2 PDF↔Excel cross-format semantic contract.

This is not a second expected-status oracle. The human CSV remains correctness
authority. This module only asks whether the PDF path and Excel path for the
same business document diverged in a way that is not already recorded.
Suggestion text is not part of the comparison.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from tests.corpus.v2_assets.baseline_exceptions import ObservedRuleOutcome
from tests.corpus.v2_assets.v2_assets_shared import FileCase, normalize_file_name

EXECUTION_RESULT = "result"
EXECUTION_FAILURE = "execution_failure"
MISSING_RULE = "missing_rule"


def pair_id_for_file_name(file_name: str) -> str:
    return normalize_file_name(Path(file_name).stem)


@dataclass(frozen=True)
class FormatRuleOutcome:
    status: str
    execution_class: str
    failure_class: str | None = None

    @property
    def semantic(self) -> tuple[str, str, str | None]:
        return (self.status, self.execution_class, self.failure_class)


@dataclass(frozen=True)
class CrossFormatPair:
    pair_id: str
    excel: FileCase
    pdf: FileCase


@dataclass(frozen=True)
class KnownPairDivergence:
    pair_id: str
    rule_index: int
    pdf_status: str
    excel_status: str
    pdf_execution_class: str
    excel_execution_class: str
    pdf_failure_class: str | None = None
    excel_failure_class: str | None = None

    @property
    def location(self) -> tuple[str, int]:
        return (self.pair_id, self.rule_index)


@dataclass(frozen=True)
class PairRuleVerdict:
    pair_id: str
    rule_index: int
    kind: str
    pdf: FormatRuleOutcome
    excel: FormatRuleOutcome


def to_format_outcome(observed: ObservedRuleOutcome | None) -> FormatRuleOutcome:
    if observed is None:
        return FormatRuleOutcome(status="MISSING", execution_class=MISSING_RULE)
    failure = observed.failure_class
    if failure == "missing_agent_rule":
        return FormatRuleOutcome(status="MISSING", execution_class=MISSING_RULE, failure_class=failure)
    status = (observed.actual_status or "ERR").upper()
    if status == "ERR" or (failure or "").startswith("raised:"):
        stable = None if not failure or failure.startswith("raised:") else failure
        return FormatRuleOutcome(status="ERR", execution_class=EXECUTION_FAILURE, failure_class=stable)
    return FormatRuleOutcome(status=status, execution_class=EXECUTION_RESULT)


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


def _matches_known(known: KnownPairDivergence, pdf: FormatRuleOutcome, excel: FormatRuleOutcome) -> bool:
    return (
        known.pdf_status == pdf.status
        and known.excel_status == excel.status
        and known.pdf_execution_class == pdf.execution_class
        and known.excel_execution_class == excel.execution_class
        and known.pdf_failure_class == pdf.failure_class
        and known.excel_failure_class == excel.failure_class
    )


def classify_pair_rule(
    pair_id: str,
    rule_index: int,
    pdf: FormatRuleOutcome,
    excel: FormatRuleOutcome,
    known: Iterable[KnownPairDivergence],
) -> PairRuleVerdict:
    record = _known_map(known).get((pair_id, rule_index))
    if pdf.semantic == excel.semantic:
        kind = "stale" if record is not None else "consistent"
    elif record is None:
        kind = "new"
    elif _matches_known(record, pdf, excel):
        kind = "allowed"
    else:
        kind = "changed"
    return PairRuleVerdict(pair_id=pair_id, rule_index=rule_index, kind=kind, pdf=pdf, excel=excel)


def classify_pair_rules(
    pair_id: str,
    pdf_outcomes: Mapping[int, FormatRuleOutcome],
    excel_outcomes: Mapping[int, FormatRuleOutcome],
    known: Iterable[KnownPairDivergence],
) -> list[PairRuleVerdict]:
    rules = sorted(set(pdf_outcomes) | set(excel_outcomes))
    missing = FormatRuleOutcome(status="MISSING", execution_class=MISSING_RULE)
    return [
        classify_pair_rule(
            pair_id,
            rule,
            pdf_outcomes.get(rule, missing),
            excel_outcomes.get(rule, missing),
            known,
        )
        for rule in rules
    ]


def D(
    pair_id: str,
    rule_index: int,
    pdf_status: str,
    excel_status: str,
    pdf_execution_class: str = EXECUTION_RESULT,
    excel_execution_class: str = EXECUTION_RESULT,
    pdf_failure_class: str | None = None,
    excel_failure_class: str | None = None,
) -> KnownPairDivergence:
    return KnownPairDivergence(
        pair_id=pair_id,
        rule_index=rule_index,
        pdf_status=pdf_status,
        excel_status=excel_status,
        pdf_execution_class=pdf_execution_class,
        excel_execution_class=excel_execution_class,
        pdf_failure_class=pdf_failure_class,
        excel_failure_class=excel_failure_class,
    )
