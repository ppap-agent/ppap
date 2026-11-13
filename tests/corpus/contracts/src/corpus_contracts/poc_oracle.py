from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List
from uuid import NAMESPACE_DNS, uuid5

from audit.contracts import AuditInput, GroupContextInput
from audit.kinds import PpapDocumentType, cross_file_doc_type_name

from corpus_contracts.oracle import load_oracle

POC_SEQ_TO_DOC_TYPE = {
    "1": PpapDocumentType.DIMENSION_MEASUREMENT_REPORT,
    "8": PpapDocumentType.PROCESS_FLOW_CHART,
    "9": PpapDocumentType.PFMEA,
    "10": PpapDocumentType.CONTROL_PLAN,
    "11": PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST,
}


@dataclass(frozen=True)
class OracleRuleExpectation:
    rule_index: int
    status: str
    rule_desc: str
    manual_reason: str


@dataclass(frozen=True)
class PocCase:
    file_name: str
    file_seq: str
    file_path: Path
    bundle_id: str
    expected: Dict[int, OracleRuleExpectation]

    def __post_init__(self) -> None:
        if not self.expected:
            raise AssertionError(f"V3 oracle incomplete: file={self.file_name} seq={self.file_seq} has 0 PASS/NG expectations")


def build_bundle_map(cases: Iterable[PocCase]) -> Dict[str, List[PocCase]]:
    bundle_map: Dict[str, List[PocCase]] = {}
    for case in cases:
        bucket = bundle_map.setdefault(case.bundle_id, [])
        bucket.append(case)
    for bucket in bundle_map.values():
        bucket.sort(key=lambda case: (case.file_seq, case.file_name))
    return bundle_map


def build_group_context_for_bundle(bundle_cases: List[PocCase]) -> GroupContextInput:
    if not bundle_cases:
        raise ValueError("bundle_cases must not be empty")

    bundle_id = bundle_cases[0].bundle_id
    task_group_id = uuid5(NAMESPACE_DNS, bundle_id)

    by_doc_type: Dict[str, List[AuditInput]] = {}
    by_audit_id: Dict[str, AuditInput] = {}

    for case in bundle_cases:
        audit_file = AuditInput(
            file_url=str(case.file_path),
            audit_id=case.file_name,
            project_id="default",
        )
        by_audit_id[audit_file.audit_id] = audit_file
        type_name = cross_file_doc_type_name(filename=case.file_name, content=audit_file.content or "")
        if type_name is None:
            continue
        by_doc_type.setdefault(type_name, []).append(audit_file)

    return GroupContextInput(
        task_group_id=str(task_group_id),
        by_doc_type=by_doc_type,
        by_audit_id=by_audit_id,
        rules_by_id={},
    )


def load_poc_cases(bundle: str | None = None, seq_set: set[str] | None = None) -> List[PocCase]:
    by_type = {typ.get_type_name(): seq for seq, typ in POC_SEQ_TO_DOC_TYPE.items()}
    cases = []
    for group in load_oracle("nio"):
        if bundle and group.case_id != bundle:
            continue
        for file in group.files:
            if file.role != "target":
                continue
            type_name = file.suite().document_type
            seq = by_type[type_name]
            if seq_set and seq not in seq_set:
                continue
            cases.append(
                PocCase(
                    file.filename,
                    seq,
                    file.path,
                    group.case_id,
                    {number: OracleRuleExpectation(number, e.status, e.rule_desc, e.manual_reason) for number, e in ((int(index.rsplit(":", 1)[-1]), e) for index, e in file.expected.items())},
                )
            )
    return sorted(cases, key=lambda case: (case.bundle_id, case.file_seq, case.file_name))
