from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote

from ppap_excel_audit_engine.checkers.agents.annual_test_requirements_agent import (
    AnnualTestRequirementsAgent,
)
from ppap_excel_audit_engine.checkers.agents.checking_fixture_approval_agent import (
    CheckingFixtureApprovalAgent,
)
from ppap_excel_audit_engine.checkers.agents.checking_fixture_approval_excel_agent import (
    CheckingFixtureApprovalExcelAgent,
)
from ppap_excel_audit_engine.checkers.agents.control_plan_agent import ControlPlanAgent
from ppap_excel_audit_engine.checkers.agents.dimension_measurement_excel_agent import (
    DimensionMeasurementExcelAgent,
)
from ppap_excel_audit_engine.checkers.agents.dimension_measurement_pdf_agent import (
    DimensionMeasurementPDFAgent,
)
from ppap_excel_audit_engine.checkers.agents.dpvr_excel_agent import DpvrExcelAgent
from ppap_excel_audit_engine.checkers.agents.dvp_r_excel_agent import DvpRExcelAgent
from ppap_excel_audit_engine.checkers.agents.equipment_list_agent import EquipmentListAgent
from ppap_excel_audit_engine.checkers.agents.initial_process_capacity_agent import (
    InitialProcessCapacityAgent,
)
from ppap_excel_audit_engine.checkers.agents.msa_report_agent import MsaReportAgent
from ppap_excel_audit_engine.checkers.agents.pdf_excel_delegator_agent import (
    AnnualTestRequirementsPdfExcelDelegatorAgent,
    ControlPlanPdfExcelDelegatorAgent,
    DpvrPdfExcelDelegatorAgent,
    DvpRPdfExcelDelegatorAgent,
    EquipmentListPdfExcelDelegatorAgent,
    InitialProcessCapacityPdfExcelDelegatorAgent,
    MsaReportPdfExcelDelegatorAgent,
    PfmeaPdfExcelDelegatorAgent,
    PlantCapacityEvaluationPdfExcelDelegatorAgent,
    ProcessFlowChartPdfExcelDelegatorAgent,
    ProcessSpecialCharacteristicPdfExcelDelegatorAgent,
    SubSupplierListPdfExcelDelegatorAgent,
    ToolingApprovalPdfExcelDelegatorAgent,
    XirAssemblyIssuePdfExcelDelegatorAgent,
)
from ppap_excel_audit_engine.checkers.agents.pfmea_agent import PfmeaAgent
from ppap_excel_audit_engine.checkers.agents.plant_capacity_evaluation_agent import (
    PlantCapacityEvaluationAgent,
)
from ppap_excel_audit_engine.checkers.agents.process_flow_chart_excel_agent import (
    ProcessFlowChartExcelAgent,
)
from ppap_excel_audit_engine.checkers.agents.process_special_characteristic_agent import (
    ProcessSpecialCharacteristicAgent,
)
from ppap_excel_audit_engine.checkers.agents.psw_excel_agent import PswExcelAgent
from ppap_excel_audit_engine.checkers.agents.psw_pdf_agent import PswPDFAgent
from ppap_excel_audit_engine.checkers.agents.sub_supplier_list_excel_agent import (
    SubSupplierListExcelAgent,
)
from ppap_excel_audit_engine.checkers.agents.tooling_approval_agent import ToolingApprovalAgent
from ppap_excel_audit_engine.checkers.agents.xir_assembly_issue_agent import (
    XirAssemblyIssueAgent,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = REPO_ROOT

RULE_GROUPS = 12
EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm"}
PDF_EXTENSIONS = {".pdf"}
DocKey = int | str

EXCEL_AGENT_MAP: dict[int | str, type] = {
    1: DimensionMeasurementExcelAgent,
    "2-1": DvpRExcelAgent,
    "2-2": DpvrExcelAgent,
    2: DvpRExcelAgent,
    3: SubSupplierListExcelAgent,
    4: CheckingFixtureApprovalExcelAgent,
    5: ToolingApprovalAgent,
    6: MsaReportAgent,
    7: InitialProcessCapacityAgent,
    8: ProcessFlowChartExcelAgent,
    9: PfmeaAgent,
    10: ControlPlanAgent,
    11: ProcessSpecialCharacteristicAgent,
    12: EquipmentListAgent,
    13: PlantCapacityEvaluationAgent,
    14: XirAssemblyIssueAgent,
    15: AnnualTestRequirementsAgent,
    16: PswExcelAgent,
}

PDF_AGENT_MAP: dict[int | str, type] = {
    1: DimensionMeasurementPDFAgent,
    "2-1": DvpRPdfExcelDelegatorAgent,
    "2-2": DpvrPdfExcelDelegatorAgent,
    2: DvpRPdfExcelDelegatorAgent,
    3: SubSupplierListPdfExcelDelegatorAgent,
    4: CheckingFixtureApprovalAgent,
    5: ToolingApprovalPdfExcelDelegatorAgent,
    6: MsaReportPdfExcelDelegatorAgent,
    7: InitialProcessCapacityPdfExcelDelegatorAgent,
    8: ProcessFlowChartPdfExcelDelegatorAgent,
    9: PfmeaPdfExcelDelegatorAgent,
    10: ControlPlanPdfExcelDelegatorAgent,
    11: ProcessSpecialCharacteristicPdfExcelDelegatorAgent,
    12: EquipmentListPdfExcelDelegatorAgent,
    13: PlantCapacityEvaluationPdfExcelDelegatorAgent,
    14: XirAssemblyIssuePdfExcelDelegatorAgent,
    15: AnnualTestRequirementsPdfExcelDelegatorAgent,
    16: PswPDFAgent,
}


@dataclass(frozen=True)
class DummyRule:
    rules: List[Dict[str, Any]]
    customers: List[str]
    models: List[str]
    parts: List[str]
    type: str | None = None
    rule_id: str = "local_rule"


@dataclass(frozen=True)
class SimpleAuditFile:
    file_url: str
    audit_id: str
    customer: str | None = None
    model: str | None = None
    part: str | None = None
    file_metadata: Dict[str, Any] | None = None
    content: str = ""


@dataclass(frozen=True)
class RuleExpectation:
    status: str
    rule_desc: str
    manual_reason: str


@dataclass(frozen=True)
class FileCase:
    file_name: str
    file_path: Path
    expected: Dict[int, RuleExpectation]
    agent_cls: type
    file_type: str


@dataclass(frozen=True)
class OracleCaseSpec:
    file_name: str
    file_format: str
    file_type: str
    agent_cls: type
    expected: Dict[int, RuleExpectation]


def normalize_file_name(name: str) -> str:
    if not name:
        return ""
    cleaned = unquote(name.strip())
    return unicodedata.normalize("NFC", cleaned)


_LONG_FEEDBACK_HEADER_HINTS = {
    "文件类型",
    "规则编号",
    "规则",
    "人工结果",
    "文件",
    "文件格式",
}


def is_long_feedback_csv(csv_path: Path) -> bool:
    path = csv_path.expanduser().resolve()
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None) or []
    header_set = {str(cell).strip() for cell in header if str(cell).strip()}
    return _LONG_FEEDBACK_HEADER_HINTS.issubset(header_set)


def _infer_manifest_path(dataset_root: Path) -> Path | None:
    candidates = sorted(path for path in dataset_root.glob("manifest*.json") if path.is_file())
    if not candidates:
        return None
    preferred_names = ("manifest_4_8.json", "manifest.json")
    for name in preferred_names:
        for path in candidates:
            if path.name == name:
                return path
    return candidates[0]


def load_expected_results(csv_path: Path, *, include_unlabeled: bool = False) -> Dict[str, Dict[int, RuleExpectation]]:
    expected: Dict[str, Dict[int, RuleExpectation]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        next(reader, None)
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            file_name = normalize_file_name(row[0])
            if not file_name:
                continue
            padded = row + [""] * max(0, 3 + RULE_GROUPS * 5 - len(row))
            rule_map: Dict[int, RuleExpectation] = {}
            for group_idx in range(RULE_GROUPS):
                base_col = 3 + group_idx * 5
                rule_desc = padded[base_col].strip()
                status = padded[base_col + 1].strip().upper()
                manual_reason = padded[base_col + 2].strip()
                if status or (include_unlabeled and rule_desc):
                    rule_map[group_idx + 1] = RuleExpectation(
                        status=status,
                        rule_desc=rule_desc,
                        manual_reason=manual_reason,
                    )
            if file_name in expected:
                existing = {k: (v.status, v.rule_desc) for k, v in expected[file_name].items()}
                new = {k: (v.status, v.rule_desc) for k, v in rule_map.items()}
                if existing != new:
                    raise AssertionError(f"Conflicting duplicate rows in CSV: {file_name}")
                continue
            expected[file_name] = rule_map
    return expected


def index_dataset_files(root: Path) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        normalized_name = normalize_file_name(path.name)
        for candidate in {path.name, normalized_name}:
            if not candidate:
                continue
            if candidate in mapping and mapping[candidate] != path:
                raise AssertionError(f"Duplicate file name in dataset after normalization: {candidate}")
            mapping[candidate] = path
    return mapping


def parse_doc_key(file_name: str) -> DocKey | None:
    sub_match = re.match(r"^(\d+)-(\d+)", file_name)
    if sub_match:
        major = int(sub_match.group(1))
        minor = int(sub_match.group(2))
        return f"{major}-{minor}"

    major_match = re.match(r"^(\d+)", file_name)
    if not major_match:
        return None
    return int(major_match.group(1))


def major_doc_key(doc_key: DocKey | None) -> int | None:
    if isinstance(doc_key, int):
        return doc_key
    if isinstance(doc_key, str):
        head = doc_key.split("-", 1)[0]
        if head.isdigit():
            return int(head)
    return None


def resolve_agent_cls(
    doc_key: DocKey | None,
    *,
    file_type: str,
    allow_major_fallback: bool = False,
) -> type | None:
    if doc_key is None:
        return None

    if file_type == "excel":
        agent_map = EXCEL_AGENT_MAP
    elif file_type in {"pdf", "scanned"}:
        agent_map = PDF_AGENT_MAP
    else:
        return None

    agent_cls = agent_map.get(doc_key)
    if agent_cls is not None or not allow_major_fallback:
        return agent_cls
    fallback_key = major_doc_key(doc_key)
    if fallback_key is None:
        return None
    return agent_map.get(fallback_key)


def make_dummy_rule(rule_count: int) -> DummyRule:
    rules: List[Dict[str, Any]] = []
    for idx in range(rule_count):
        rules.append(
            {
                "id": f"local_rule_{idx + 1}",
                "category": "local_excel_check",
                "priority": "P1",
                "rule_to_check": "local fixture",
                "type_of_rule": "table",
            }
        )
    return DummyRule(rules=rules, customers=[], models=[], parts=[])


def build_cases(
    *,
    dataset_root: Path,
    expected_csv: Path,
    ftype_filter: str | None = None,
    include_unlabeled: bool = False,
) -> List[FileCase]:
    dataset_root = dataset_root.expanduser().resolve()
    expected_csv = expected_csv.expanduser().resolve()
    if is_long_feedback_csv(expected_csv):
        return build_cases_from_long_feedback(
            dataset_root=dataset_root,
            expected_csv=expected_csv,
            ftype_filter=ftype_filter,
            include_unlabeled=include_unlabeled,
            manifest_path=_infer_manifest_path(dataset_root),
        )

    expected_map = load_expected_results(expected_csv, include_unlabeled=include_unlabeled)
    dataset_files = index_dataset_files(dataset_root)

    missing = sorted(name for name in expected_map if name not in dataset_files)
    if missing:
        raise AssertionError(f"Missing files in dataset: {missing}")

    cases: List[FileCase] = []
    for file_name, rule_map in expected_map.items():
        if not rule_map:
            continue
        file_path = dataset_files[file_name]
        doc_key = parse_doc_key(file_name)
        if doc_key is None:
            continue
        suffix = file_path.suffix.lower()
        is_excel = suffix in EXCEL_EXTENSIONS
        is_pdf = suffix in PDF_EXTENSIONS

        if ftype_filter == "excel" and not is_excel:
            continue
        if ftype_filter in {"pdf", "scanned"} and not is_pdf:
            continue

        if is_excel:
            file_type = "excel"
        elif is_pdf:
            file_type = "pdf" if ftype_filter != "scanned" else "scanned"
        else:
            continue
        agent_cls = resolve_agent_cls(doc_key, file_type=file_type)
        if not agent_cls:
            continue
        cases.append(
            FileCase(
                file_name=file_name,
                file_path=file_path,
                expected=rule_map,
                agent_cls=agent_cls,
                file_type=file_type,
            )
        )

    if not cases:
        raise AssertionError("No test cases found in dataset for selected options.")
    return cases


_LONG_FEEDBACK_REQUIRED_HEADERS = {
    "文件类型",
    "规则编号",
    "规则",
    "人工结果",
    "人工依据",
    "文件",
    "文件格式",
}


def _normalize_feedback_file_format(file_format: str) -> str:
    return "".join(str(file_format or "").strip().lower().split())


def _match_feedback_file_type(*, file_format: str, ftype_filter: str | None) -> bool:
    if not ftype_filter:
        return True

    normalized = _normalize_feedback_file_format(file_format)
    if ftype_filter == "excel":
        return "excel" in normalized
    if ftype_filter == "pdf":
        return "pdf" in normalized and "扫描" not in normalized
    if ftype_filter == "scanned":
        return "扫描" in normalized and "pdf" in normalized
    return False


def _parse_feedback_doc_key(*, file_type: str, file_name: str) -> DocKey | None:
    normalized = str(file_type or "").strip()
    if re.fullmatch(r"\d+-\d+", normalized):
        return normalized
    if normalized.isdigit():
        return int(normalized)
    return parse_doc_key(file_name)


def _load_manifest_path_index(manifest_path: Path, dataset_root: Path) -> dict[tuple[str, str], tuple[Path, ...]]:
    if not manifest_path.exists():
        return {}

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = raw.get("rows", []) if isinstance(raw, dict) else []
    collected: dict[tuple[str, str], list[Path]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        file_name = normalize_file_name(str(row.get("file_name", "") or ""))
        file_format = _normalize_feedback_file_format(str(row.get("file_format", "") or ""))
        local_path_raw = str(row.get("local_path", "") or "").strip()
        if not file_name or not local_path_raw:
            continue
        local_path = Path(local_path_raw).expanduser().resolve()
        if not local_path.is_relative_to(dataset_root):
            continue
        bucket = collected.setdefault((file_name, file_format), [])
        if local_path not in bucket:
            bucket.append(local_path)
    return {key: tuple(paths) for key, paths in collected.items()}


def reconcile_v2_oracle_assets(
    selected: list[OracleCaseSpec],
    *,
    dataset_files: Dict[str, Path],
    manifest_paths: dict[tuple[str, str], tuple[Path, ...]],
) -> list[tuple[OracleCaseSpec, Path]]:
    if not selected:
        raise AssertionError("No long-feedback test cases found for selected options.")

    missing: list[OracleCaseSpec] = []
    ambiguous: list[tuple[OracleCaseSpec, list[Path]]] = []
    resolved: list[tuple[OracleCaseSpec, Path]] = []

    for spec in selected:
        candidates: list[Path] = []
        for path in manifest_paths.get((spec.file_name, spec.file_format), ()):
            if path.exists() and path not in candidates:
                candidates.append(path)
        if not candidates:
            fallback = dataset_files.get(spec.file_name)
            if fallback is not None and fallback.exists() and fallback not in candidates:
                candidates.append(fallback)

        if len(candidates) == 1:
            resolved.append((spec, candidates[0]))
        elif len(candidates) > 1:
            ambiguous.append((spec, candidates))
        else:
            missing.append(spec)

    parts: list[str] = []
    if missing:
        lines = [f"- file={spec.file_name} file_type={spec.file_type}" for spec in missing]
        parts.append("V2 oracle assets missing:\n" + "\n".join(lines))
    if ambiguous:
        lines = [f"- file={spec.file_name} file_type={spec.file_type} -> {paths}" for spec, paths in ambiguous]
        parts.append("V2 oracle assets ambiguous:\n" + "\n".join(lines))
    if parts:
        raise AssertionError("\n".join(parts))
    return resolved


def _select_long_feedback_oracle_cases(
    *,
    expected_csv: Path,
    ftype_filter: str | None,
    include_unlabeled: bool,
) -> list[OracleCaseSpec]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    with expected_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header_set = set(reader.fieldnames or [])
        missing_headers = sorted(_LONG_FEEDBACK_REQUIRED_HEADERS - header_set)
        if missing_headers:
            raise AssertionError(f"Missing required headers in long feedback CSV: {missing_headers}")

        for row in reader:
            file_name = normalize_file_name(str(row.get("文件", "") or ""))
            if not file_name:
                continue

            file_format = str(row.get("文件格式", "") or "").strip()
            if not _match_feedback_file_type(file_format=file_format, ftype_filter=ftype_filter):
                continue

            doc_key = _parse_feedback_doc_key(
                file_type=str(row.get("文件类型", "") or ""),
                file_name=file_name,
            )
            if doc_key is None:
                continue

            if ftype_filter in {"excel", "pdf", "scanned"}:
                agent_file_type = ftype_filter
            else:
                suffix = Path(file_name).suffix.lower()
                if suffix in EXCEL_EXTENSIONS:
                    agent_file_type = "excel"
                elif suffix in PDF_EXTENSIONS:
                    agent_file_type = "pdf"
                else:
                    continue
            agent_cls = resolve_agent_cls(doc_key, file_type=agent_file_type)
            if agent_cls is None:
                continue

            rule_index_raw = str(row.get("规则编号", "") or "").strip()
            if not rule_index_raw.isdigit():
                continue
            rule_index = int(rule_index_raw)
            status = str(row.get("人工结果", "") or "").strip().upper()
            rule_desc = str(row.get("规则", "") or "").strip()
            manual_reason = str(row.get("人工依据", "") or "").strip()
            if not status and not (include_unlabeled and rule_desc):
                continue

            group_key = (file_name, _normalize_feedback_file_format(file_format))
            state = grouped.setdefault(
                group_key,
                {
                    "file_name": file_name,
                    "file_format": group_key[1],
                    "file_type": agent_file_type,
                    "agent_cls": agent_cls,
                    "expected": {},
                },
            )

            existing = state["expected"].get(rule_index)
            if existing is not None and (existing.status != status or existing.rule_desc != rule_desc or existing.manual_reason != manual_reason):
                raise AssertionError(f"Conflicting duplicate row in long feedback CSV: file={file_name!r}, rule={rule_index}")

            state["expected"][rule_index] = RuleExpectation(
                status=status,
                rule_desc=rule_desc,
                manual_reason=manual_reason,
            )

    return [
        OracleCaseSpec(
            file_name=state["file_name"],
            file_format=state["file_format"],
            file_type=state["file_type"],
            agent_cls=state["agent_cls"],
            expected=state["expected"],
        )
        for _, state in sorted(grouped.items(), key=lambda item: item[0])
        if state["expected"]
    ]


def build_cases_from_long_feedback(
    *,
    dataset_root: Path,
    expected_csv: Path,
    ftype_filter: str | None = None,
    include_unlabeled: bool = False,
    manifest_path: Path | None = None,
) -> List[FileCase]:
    dataset_root = dataset_root.expanduser().resolve()
    expected_csv = expected_csv.expanduser().resolve()
    if not dataset_root.exists():
        raise AssertionError(f"Missing dataset root: {dataset_root}")
    if not expected_csv.exists():
        raise AssertionError(f"Missing expected CSV: {expected_csv}")

    selected = _select_long_feedback_oracle_cases(
        expected_csv=expected_csv,
        ftype_filter=ftype_filter,
        include_unlabeled=include_unlabeled,
    )
    resolved = reconcile_v2_oracle_assets(
        selected,
        dataset_files=index_dataset_files(dataset_root),
        manifest_paths=(_load_manifest_path_index(manifest_path.expanduser().resolve(), dataset_root) if manifest_path else {}),
    )
    return [
        FileCase(
            file_name=spec.file_name,
            file_path=path,
            expected=spec.expected,
            agent_cls=spec.agent_cls,
            file_type=spec.file_type,
        )
        for spec, path in resolved
    ]
