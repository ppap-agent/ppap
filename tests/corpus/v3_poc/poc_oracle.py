from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.parse import unquote
from uuid import NAMESPACE_DNS, uuid5

import pandas as pd
from ppap_core.contracts import AuditInput, GroupContextInput
from ppap_core.types import PpapDocumentType

RULE_GROUPS = 12
RULE_BLOCK_SIZE = 5
FIRST_RULE_COL = 3
VALID_POC_SEQ_ORDER = ("1", "8", "9", "10", "11")
VALID_POC_SEQS = set(VALID_POC_SEQ_ORDER)

POC_SEQ_TO_DOC_TYPE = {
    "1": PpapDocumentType.DIMENSION_MEASUREMENT_REPORT,
    "8": PpapDocumentType.PROCESS_FLOW_CHART,
    "9": PpapDocumentType.PFMEA,
    "10": PpapDocumentType.CONTROL_PLAN,
    "11": PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST,
}

REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS_ROOT = Path(os.getenv("PPAP_ASSETS_ROOT", REPO_ROOT / "assets"))
DEFAULT_POC_DATASET_ROOT = Path(os.getenv("PPAP_POC_DATASET_ROOT", ASSETS_ROOT / "poc测试" / "POC测试样本"))
DEFAULT_POC_EXPECTED_XLSX = Path(os.getenv("PPAP_POC_EXPECTED_XLSX", ASSETS_ROOT / "poc测试" / "poc-测试数据统计表.xlsx"))


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


@dataclass(frozen=True)
class BundleContext:
    bundle_id: str
    cases: List[PocCase]


@dataclass(frozen=True)
class SimpleAuditFile:
    file_url: str
    audit_id: str
    customer: str | None = None
    model: str | None = None
    part: str | None = None
    file_metadata: Dict | None = None
    content: str = ""


def _normalize_file_name(name: str) -> str:
    if not name:
        return ""
    cleaned = unquote(str(name).strip())
    return unicodedata.normalize("NFC", cleaned)


def _normalize_status(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.upper()
    if normalized in {"PASS", "NG"}:
        return normalized
    raise ValueError(f"Invalid manual status: '{value}' (only PASS/NG/empty are allowed)")


def parse_seq_filter(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None

    seq_set = {segment.strip() for segment in cleaned.split(",") if segment.strip()}
    invalid = sorted(seq_set - VALID_POC_SEQS, key=int)
    if invalid:
        valid = ",".join(VALID_POC_SEQ_ORDER)
        raise ValueError(f"Invalid --seq values: {invalid}. Valid seq values: {valid}")
    return seq_set


def _load_expected_from_xlsx(
    xlsx_path: Path,
    df: pd.DataFrame | None = None,
) -> Dict[str, Dict[int, OracleRuleExpectation]]:
    if not xlsx_path.exists():
        raise AssertionError(f"Required V3 expected XLSX is missing: {xlsx_path}")

    if df is None:
        df = pd.read_excel(xlsx_path, header=None, sheet_name=0).fillna("")
    else:
        df = df.fillna("")
    if df.shape[0] < 4:
        raise AssertionError(f"Expected xlsx has no data rows: {xlsx_path}")

    expected: Dict[str, Dict[int, OracleRuleExpectation]] = {}
    data_rows = df.iloc[3:]

    min_columns = FIRST_RULE_COL + RULE_GROUPS * RULE_BLOCK_SIZE
    for row in data_rows.itertuples(index=False):
        cells = list(row)
        if len(cells) < min_columns:
            cells += [""] * (min_columns - len(cells))

        file_name = _normalize_file_name(str(cells[0]))
        if not file_name:
            continue

        file_seq = str(cells[1]).strip()
        if file_seq and file_seq not in VALID_POC_SEQS:
            raise AssertionError(f"Unsupported file seq '{file_seq}' for {file_name}. Allowed seq: {sorted(VALID_POC_SEQS)}")

        rule_map: Dict[int, OracleRuleExpectation] = {}
        for group_idx in range(RULE_GROUPS):
            base_col = FIRST_RULE_COL + group_idx * RULE_BLOCK_SIZE
            rule_index = group_idx + 1
            rule_desc = str(cells[base_col]).strip()
            manual_status = _normalize_status(str(cells[base_col + 1]))
            manual_reason = str(cells[base_col + 2]).strip()
            if not manual_status:
                continue
            rule_map[rule_index] = OracleRuleExpectation(
                rule_index=rule_index,
                status=manual_status,
                rule_desc=rule_desc,
                manual_reason=manual_reason,
            )

        if file_name in expected:
            existing = {rule_idx: (exp.status, exp.rule_desc) for rule_idx, exp in expected[file_name].items()}
            incoming = {rule_idx: (exp.status, exp.rule_desc) for rule_idx, exp in rule_map.items()}
            if existing != incoming:
                raise AssertionError(f"Conflicting duplicate rows in xlsx: {file_name}")
            continue

        expected[file_name] = rule_map

    if not expected:
        raise AssertionError(f"No valid V3 cases in expected xlsx: {xlsx_path}")
    return expected


def _index_dataset_files(root: Path) -> Dict[str, Path]:
    if not root.exists():
        raise AssertionError(f"Required V3 dataset is missing: {root}")

    mapping: Dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        normalized_name = _normalize_file_name(path.name)
        for candidate in {path.name, normalized_name}:
            if not candidate:
                continue
            if candidate in mapping and mapping[candidate] != path:
                raise AssertionError(f"Duplicate file name in dataset after normalization: {candidate} -> {mapping[candidate]} vs {path}")
            mapping[candidate] = path
    return mapping


def _build_poc_cases(
    expected_map: Dict[str, Dict[int, OracleRuleExpectation]],
    dataset_index: Dict[str, Path],
    file_seq_map: Dict[str, str],
) -> List[PocCase]:
    missing = sorted(name for name in expected_map if name not in dataset_index)
    if missing:
        raise AssertionError(f"Missing files in dataset: {missing}")

    missing_seq = sorted(name for name in expected_map if name not in file_seq_map)
    if missing_seq:
        raise AssertionError(f"Missing seq info in xlsx for files: {missing_seq}")

    cases: List[PocCase] = []
    for file_name, rule_map in expected_map.items():
        file_path = dataset_index[file_name]
        file_seq = file_seq_map[file_name]

        cases.append(
            PocCase(
                file_name=file_name,
                file_seq=file_seq,
                file_path=file_path,
                bundle_id=file_path.parent.name,
                expected=rule_map,
            )
        )

    cases.sort(key=lambda case: (case.bundle_id, case.file_seq, case.file_name))
    return cases


def _filter_cases(
    cases: Iterable[PocCase],
    bundle: str | None,
    seq_set: set[str] | None,
) -> List[PocCase]:
    filtered = list(cases)
    if bundle:
        filtered = [case for case in filtered if case.bundle_id == bundle]
    if seq_set:
        filtered = [case for case in filtered if case.file_seq in seq_set]
    return filtered


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
        doc_type = POC_SEQ_TO_DOC_TYPE.get(case.file_seq)
        if doc_type is None:
            continue
        by_doc_type.setdefault(doc_type.get_type_name(), []).append(audit_file)

    return GroupContextInput(
        task_group_id=str(task_group_id),
        by_doc_type=by_doc_type,
        by_audit_id=by_audit_id,
        rules_by_id={},
    )


def load_poc_cases(
    bundle: str | None = None,
    seq_set: set[str] | None = None,
    dataset_root: Path = DEFAULT_POC_DATASET_ROOT,
    expected_xlsx: Path = DEFAULT_POC_EXPECTED_XLSX,
) -> List[PocCase]:
    if not dataset_root.exists():
        raise AssertionError(f"Required V3 dataset is missing: {dataset_root}")
    if not expected_xlsx.exists():
        raise AssertionError(f"Required V3 expected XLSX is missing: {expected_xlsx}")

    raw_df = pd.read_excel(expected_xlsx, header=None, sheet_name=0).fillna("")
    expected_map = _load_expected_from_xlsx(expected_xlsx, df=raw_df)
    dataset_index = _index_dataset_files(dataset_root)

    # Build file -> seq mapping from xlsx rows.
    file_seq_map: Dict[str, str] = {}
    for row in raw_df.iloc[3:].itertuples(index=False):
        cells = list(row)
        if len(cells) < 2:
            continue
        file_name = _normalize_file_name(str(cells[0]))
        if not file_name:
            continue
        seq = str(cells[1]).strip()
        if not seq:
            raise AssertionError(f"Missing file seq in xlsx for file: {file_name}")
        if seq not in VALID_POC_SEQS:
            raise AssertionError(f"Unsupported file seq '{seq}' for {file_name}. Allowed seq: {sorted(VALID_POC_SEQS)}")
        if file_name in file_seq_map and file_seq_map[file_name] != seq:
            raise AssertionError(f"Conflicting seq for file {file_name}: {file_seq_map[file_name]} vs {seq}")
        file_seq_map[file_name] = seq

    cases = _build_poc_cases(
        expected_map=expected_map,
        dataset_index=dataset_index,
        file_seq_map=file_seq_map,
    )
    return _filter_cases(cases, bundle=bundle, seq_set=seq_set)
