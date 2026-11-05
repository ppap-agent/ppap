"""Unified PDF -> Excel pipeline."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
import uuid

from openpyxl import Workbook

from documents.contracts import (
    ConversionArtifact,
    FileRef,
)
from documents.excel import load_excel_workbook
from documents.files import download_file_to_dir_sync, parse_file_size
from documents.ocr.env import max_file_size
from documents.pdf import (
    DEFAULT_SPEC,
    PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC,
    PPAP_2_1_DVP_R_SPEC,
    PPAP_2_2_DPVR_SPEC,
    PPAP_3_SUB_SUPPLIER_LIST_SPEC,
    PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT_SPEC,
    PPAP_5_TOOLING_APPROVAL_REPORT_SPEC,
    PPAP_6_MSA_REPORT_SPEC,
    PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY_SPEC,
    PPAP_8_PROCESS_FLOW_CHART_SPEC,
    PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST_SPEC,
    PPAP_12_EQUIPMENT_LIST_SPEC,
    PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT_SPEC,
    PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT_SPEC,
    PPAP_15_ANNUAL_TEST_REQUIREMENTS_SPEC,
    PPAP_16_PSW_SPEC,
    ColumnPolicy,
    ExtractionSpec,
    GroupingMode,
    GroupingSpec,
    OutputMode,
    OutputSpec,
    PdfConversionError,
    PdfSpec,
    SheetNameMode,
    StitchSpec,
    TitleSectionRule,
)
from documents.pdf import convert_pdf_to_excel as docops_convert_pdf_to_excel

logger = logging.getLogger(__name__)

BACKEND_MANUAL_LINEGRID = "manual_linegrid"

ERROR_INVALID_INPUT = "invalid_input"
ERROR_DOWNLOAD_FAILED = "download_failed"
ERROR_MANUAL_LINEGRID_FAILED = "manual_linegrid_failed"
ERROR_MANUAL_LINEGRID_EXCEPTION = "manual_linegrid_exception"

PROFILE_DEFAULT = "default"
PROFILE_PPAP_2_1_DVP_R = "ppap_2_1_dvp_r"
PROFILE_PPAP_2_2_DPVR = "ppap_2_2_dpvr"
PROFILE_PPAP_3_SUB_SUPPLIER_LIST = "ppap_3_sub_supplier_list"
PROFILE_PPAP_1_DIMENSION_MEASUREMENT_REPORT = "ppap_1_dimension_measurement_report"
PROFILE_PPAP_6_MSA_REPORT = "ppap_6_msa_report"
PROFILE_PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT = "ppap_4_checking_fixture_approval_report"
PROFILE_PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY = "ppap_7_initial_process_capacity_study"
PROFILE_PPAP_8_PROCESS_FLOW_CHART = "ppap_8_process_flow_chart"
PROFILE_PPAP_9_PFMEA = "ppap_9_pfmea"
PROFILE_PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST = "ppap_11_process_special_characteristic_list"
PROFILE_PPAP_5_TOOLING_APPROVAL_REPORT = "ppap_5_tooling_approval_report"
PROFILE_PPAP_12_EQUIPMENT_LIST = "ppap_12_equipment_list"
PROFILE_PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT = "ppap_13_plant_capacity_evaluation_report"
PROFILE_PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT = "ppap_14_xir_assembly_issue_report"
PROFILE_PPAP_15_ANNUAL_TEST_REQUIREMENTS = "ppap_15_annual_test_requirements"
PROFILE_PPAP_16_PSW = "ppap_16_psw"
_DOC_SEQ_PATTERN = re.compile(r"^(?P<doc_seq>\d+)-")

# PFMEA doc9 uses titled sections and stable sheet names.
PPAP_9_PFMEA_SECTIONED_SPEC = PdfSpec(
    grouping=GroupingSpec(
        mode=GroupingMode.TITLE_SECTIONS,
        section_rules=(
            TitleSectionRule(label="第五版PFMEA表格", any_of=("aiag & vda pfmea", "pfmea id编号")),
            TitleSectionRule(label="变更履历", any_of=("pfmea变更履历", "变更履历 版本号")),
            TitleSectionRule(label="PFMEA严重度评分准则", all_of=("严重度", "公司或产品系列示例", "空白 由使用人员填写")),
            TitleSectionRule(label="PFMEA發生频度評分準則", all_of=("对失效起因发生的预测", "预防控制", "频度")),
            TitleSectionRule(label="PFMEA侦测度评分准则", all_of=("探测能力", "探测方法成熟度", "探测机会")),
            TitleSectionRule(label="PFMEA AP準則", all_of=("措施優先度 ap", "備註", "影響 s")),
            TitleSectionRule(label="PFMEA AP判定数据库", all_of=("序号 s o d", "措施優先度 ap")),
        ),
    ),
    output=OutputSpec(
        mode=OutputMode.SHEET_PER_GROUP,
        sheet_name_mode=SheetNameMode.GROUP_LABEL,
    ),
    stitch=StitchSpec(
        column_policy=ColumnPolicy.PAD_TO_MAX,
        drop_repeated_header=False,
    ),
    extraction=ExtractionSpec(
        line_tolerance=1.15,
        merge_tolerance=1.35,
        text_tolerance=1.15,
    ),
)


def _to_file_ref(file_ref: FileRef | str) -> FileRef:
    if isinstance(file_ref, FileRef):
        return file_ref
    return FileRef(location=str(file_ref))


def _is_pdf(file_ref: FileRef) -> bool:
    path = file_ref.location.split("?")[0].split("#")[0]
    return path.lower().endswith(".pdf")


def _cleanup(paths: list[str]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            logger.debug("failed to cleanup temp file: %s", path)


def _failure(
    *,
    strategy_used: str,
    diagnostics: dict[str, object],
    temp_files: list[str],
    cleanup: bool = True,
) -> ConversionArtifact:
    if cleanup:
        _cleanup(temp_files)
        temp_files = []
    return ConversionArtifact(
        success=False,
        strategy_used=strategy_used,
        diagnostics=diagnostics,
        temp_files=temp_files,
    )


def _attempt_manual_linegrid(
    source: str,
    output_path: str,
    *,
    spec: PdfSpec,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    try:
        result = docops_convert_pdf_to_excel(
            source,
            output_path,
            spec=spec,
        )
        observed = dict(getattr(result, "diagnostics", None) or {})
        # Keep the existing audit-facing page identity outside the original
        # converter. This metadata never changes extracted cells or XLSX data.
        extraction = getattr(result, "extraction", None)
        if extraction is not None:
            observed["original_pages"] = [
                {
                    "page": page.page_index + 1,
                    "success": page.success,
                    "region": None if page.table_region is None else [None if value is None else round(float(value), 2) for value in page.table_region],
                    "error": page.error_message,
                }
                for group in extraction.groups
                for page in group.page_results
            ]
        result_output_path = getattr(result, "output_path", None)
        result_success = getattr(result, "success", None)
        if result_output_path == output_path and os.path.exists(output_path):
            return None, observed
        if result_success is True and os.path.exists(output_path):
            return None, observed

        result_error = getattr(result, "error_message", None)
        return {
            "error_code": ERROR_MANUAL_LINEGRID_FAILED,
            "error": str(result_error or "manual linegrid conversion failed"),
            "pdf_v2_diagnostics": observed,
        }, observed
    except PdfConversionError as exc:
        return {
            "error_code": ERROR_MANUAL_LINEGRID_FAILED,
            "error": exc.message,
            "pdf_v2_error_code": exc.code,
            "pdf_v2_diagnostics": exc.diagnostics,
        }, dict(exc.diagnostics or {})
    except Exception as exc:
        return {
            "error_code": ERROR_MANUAL_LINEGRID_EXCEPTION,
            "error": str(exc),
        }, None


def _build_attempt(backend: str, *, success: bool, error_code: str = "", elapsed_ms: int = 0) -> dict[str, object]:
    return {
        "backend": backend,
        "success": success,
        "error_code": error_code,
        "elapsed_ms": elapsed_ms,
    }


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


_SPEC_BY_NAME = {
    "DVP_R": (PROFILE_PPAP_2_1_DVP_R, PPAP_2_1_DVP_R_SPEC),
    "DPVR": (PROFILE_PPAP_2_2_DPVR, PPAP_2_2_DPVR_SPEC),
    "SUB_SUPPLIER_LIST": (PROFILE_PPAP_3_SUB_SUPPLIER_LIST, PPAP_3_SUB_SUPPLIER_LIST_SPEC),
    "DIMENSION_MEASUREMENT_REPORT": (PROFILE_PPAP_1_DIMENSION_MEASUREMENT_REPORT, PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC),
    "MSA_REPORT": (PROFILE_PPAP_6_MSA_REPORT, PPAP_6_MSA_REPORT_SPEC),
    "CHECKING_FIXTURE_APPROVAL_REPORT": (PROFILE_PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT, PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT_SPEC),
    "TOOLING_APPROVAL_REPORT": (PROFILE_PPAP_5_TOOLING_APPROVAL_REPORT, PPAP_5_TOOLING_APPROVAL_REPORT_SPEC),
    "INITIAL_PROCESS_CAPACITY_STUDY": (PROFILE_PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY, PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY_SPEC),
    "PROCESS_FLOW_CHART": (PROFILE_PPAP_8_PROCESS_FLOW_CHART, PPAP_8_PROCESS_FLOW_CHART_SPEC),
    "PFMEA": (PROFILE_PPAP_9_PFMEA, PPAP_9_PFMEA_SECTIONED_SPEC),
    "EQUIPMENT_LIST": (PROFILE_PPAP_12_EQUIPMENT_LIST, PPAP_12_EQUIPMENT_LIST_SPEC),
    "PLANT_CAPACITY_EVALUATION_REPORT": (PROFILE_PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT, PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT_SPEC),
    "XIR_ASSEMBLY_ISSUE_REPORT": (PROFILE_PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT, PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT_SPEC),
    "ANNUAL_TEST_REQUIREMENTS": (PROFILE_PPAP_15_ANNUAL_TEST_REQUIREMENTS, PPAP_15_ANNUAL_TEST_REQUIREMENTS_SPEC),
    "PSW": (PROFILE_PPAP_16_PSW, PPAP_16_PSW_SPEC),
    "PROCESS_SPECIAL_CHARACTERISTIC_LIST": (PROFILE_PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST, PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST_SPEC),
}


def _normalize_doc_type(doc_type: object | None) -> str | None:
    if doc_type is None:
        return None
    name = getattr(doc_type, "name", None)
    if isinstance(name, str):
        return None if not name or name == "UNKNOWN" else name
    if not isinstance(doc_type, str) or not doc_type:
        return None
    if doc_type in _SPEC_BY_NAME:
        return doc_type
    return None


def _resolve_manual_linegrid_spec(doc_type: str | None) -> tuple[str, PdfSpec]:
    if doc_type is None:
        return PROFILE_DEFAULT, DEFAULT_SPEC
    return _SPEC_BY_NAME.get(doc_type, (PROFILE_DEFAULT, DEFAULT_SPEC))


def _extract_doc_seq(location: str) -> int | None:
    basename = os.path.basename(str(location).split("?")[0].split("#")[0])
    match = _DOC_SEQ_PATTERN.match(basename)
    if not match:
        return None
    try:
        return int(match.group("doc_seq"))
    except ValueError:
        return None


def _resolve_manual_linegrid_profile(location: str) -> tuple[str, PdfSpec, int | None]:
    doc_seq = _extract_doc_seq(location)

    if doc_seq == 2:
        return PROFILE_PPAP_2_1_DVP_R, PPAP_2_1_DVP_R_SPEC, doc_seq
    if doc_seq == 3:
        return PROFILE_PPAP_3_SUB_SUPPLIER_LIST, PPAP_3_SUB_SUPPLIER_LIST_SPEC, doc_seq
    if doc_seq == 1:
        return PROFILE_PPAP_1_DIMENSION_MEASUREMENT_REPORT, PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC, doc_seq

    if doc_seq == 4:
        return PROFILE_PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT, PPAP_4_CHECKING_FIXTURE_APPROVAL_REPORT_SPEC, doc_seq

    if doc_seq == 6:
        return PROFILE_PPAP_6_MSA_REPORT, PPAP_6_MSA_REPORT_SPEC, doc_seq

    if doc_seq == 5:
        return PROFILE_PPAP_5_TOOLING_APPROVAL_REPORT, PPAP_5_TOOLING_APPROVAL_REPORT_SPEC, doc_seq

    if doc_seq == 7:
        return PROFILE_PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY, PPAP_7_INITIAL_PROCESS_CAPACITY_STUDY_SPEC, doc_seq

    if doc_seq == 8:
        return PROFILE_PPAP_8_PROCESS_FLOW_CHART, PPAP_8_PROCESS_FLOW_CHART_SPEC, doc_seq

    if doc_seq == 9:
        return PROFILE_PPAP_9_PFMEA, PPAP_9_PFMEA_SECTIONED_SPEC, doc_seq
    if doc_seq == 11:
        return (
            PROFILE_PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST,
            PPAP_11_PROCESS_SPECIAL_CHARACTERISTIC_LIST_SPEC,
            doc_seq,
        )
    if doc_seq == 12:
        return PROFILE_PPAP_12_EQUIPMENT_LIST, PPAP_12_EQUIPMENT_LIST_SPEC, doc_seq
    if doc_seq == 13:
        return PROFILE_PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT, PPAP_13_PLANT_CAPACITY_EVALUATION_REPORT_SPEC, doc_seq
    if doc_seq == 14:
        return PROFILE_PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT, PPAP_14_XIR_ASSEMBLY_ISSUE_REPORT_SPEC, doc_seq
    if doc_seq == 15:
        return PROFILE_PPAP_15_ANNUAL_TEST_REQUIREMENTS, PPAP_15_ANNUAL_TEST_REQUIREMENTS_SPEC, doc_seq
    if doc_seq == 16:
        return PROFILE_PPAP_16_PSW, PPAP_16_PSW_SPEC, doc_seq

    return PROFILE_DEFAULT, DEFAULT_SPEC, doc_seq


def convert_pdf_to_excel_auto(
    file_ref: FileRef | str,
    *,
    enable_cache: bool = True,
    doc_type: object | None = None,
    spec: PdfSpec | None = None,
) -> ConversionArtifact:
    """Prepare native tables only; graph facts use the explicit page reader."""
    conversion_started_at = time.perf_counter()
    ref = _to_file_ref(file_ref)
    resolved_doc_type = _normalize_doc_type(doc_type)
    if spec is not None:
        profile_name, manual_linegrid_spec, profile_doc_seq = "task", spec, None
    elif resolved_doc_type is not None:
        profile_name, manual_linegrid_spec = _resolve_manual_linegrid_spec(resolved_doc_type)
        profile_doc_seq = None
    else:
        profile_name, manual_linegrid_spec, profile_doc_seq = _resolve_manual_linegrid_profile(ref.location)
    diagnostics: dict[str, object] = {
        "location": ref.location,
        "conversion_backend_order": [BACKEND_MANUAL_LINEGRID],
        "conversion_attempts": [],
        "fallback_ocr_triggered": False,
        "manual_linegrid_spec_label": profile_name,
        "task_spec": spec is not None,
        "resolved_doc_type": resolved_doc_type or "",
        "conversion_profile_name": profile_name,
        "conversion_profile_doc_seq": profile_doc_seq,
    }
    temp_files: list[str] = []

    if not _is_pdf(ref):
        diagnostics["total_elapsed_ms"] = _elapsed_ms(conversion_started_at)
        diagnostics["final_strategy_used"] = "invalid_input"
        return _failure(
            strategy_used="invalid_input",
            diagnostics={
                **diagnostics,
                "error_code": ERROR_INVALID_INPUT,
                "error": "input is not a PDF",
            },
            temp_files=[],
            cleanup=False,
        )

    pdf_path = ref.location

    if ref.is_remote:
        download_started_at = time.perf_counter()
        max_size = parse_file_size(max_file_size())
        dl_dir = os.path.join(tempfile.gettempdir(), "documents-conversion-dl")
        os.makedirs(dl_dir, exist_ok=True)
        ext = os.path.splitext(ref.location.split("?")[0].split("#")[0])[1] or ".pdf"
        unique_name = f"{uuid.uuid4().hex}{ext}"
        try:
            downloaded_pdf = download_file_to_dir_sync(
                ref.location,
                dl_dir,
                max_size,
                custom_filename=unique_name,
            )
        except Exception as exc:
            diagnostics["download_elapsed_ms"] = _elapsed_ms(download_started_at)
            diagnostics["total_elapsed_ms"] = _elapsed_ms(conversion_started_at)
            diagnostics["final_strategy_used"] = BACKEND_MANUAL_LINEGRID
            return _failure(
                strategy_used=BACKEND_MANUAL_LINEGRID,
                diagnostics={
                    **diagnostics,
                    "error_code": ERROR_DOWNLOAD_FAILED,
                    "error": str(exc),
                },
                temp_files=temp_files,
            )
        pdf_path = downloaded_pdf
        temp_files.append(downloaded_pdf)
        diagnostics["download_elapsed_ms"] = _elapsed_ms(download_started_at)

    fd, temp_xlsx = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    temp_files.append(temp_xlsx)
    began = time.perf_counter()
    error, native = _attempt_manual_linegrid(pdf_path, temp_xlsx, spec=manual_linegrid_spec)
    if native:
        diagnostics["pdf_v2_diagnostics"] = native
    elapsed = _elapsed_ms(began)
    diagnostics["manual_linegrid_elapsed_ms"] = elapsed
    diagnostics["conversion_backend"] = BACKEND_MANUAL_LINEGRID
    diagnostics["final_strategy_used"] = BACKEND_MANUAL_LINEGRID
    diagnostics["total_elapsed_ms"] = _elapsed_ms(conversion_started_at)
    if error is None:
        diagnostics["conversion_attempts"] = [_build_attempt(BACKEND_MANUAL_LINEGRID, success=True, elapsed_ms=elapsed)]
        return ConversionArtifact(success=True, xlsx_path=temp_xlsx, strategy_used=BACKEND_MANUAL_LINEGRID, diagnostics=diagnostics, temp_files=temp_files)
    code = str(error.get("error_code", ERROR_MANUAL_LINEGRID_FAILED))
    if error.get("pdf_v2_error_code") in {"no_groups", "no_successful_groups", "strict_mode_failed"}:
        code = "native_table_unconfirmed"
    diagnostics.update(error)
    diagnostics["error_code"] = code
    diagnostics["conversion_attempts"] = [_build_attempt(BACKEND_MANUAL_LINEGRID, success=False, error_code=code, elapsed_ms=elapsed)]
    return _failure(strategy_used=BACKEND_MANUAL_LINEGRID, diagnostics=diagnostics, temp_files=temp_files)


def load_workbook_auto(
    file_ref: FileRef | str,
    *,
    enable_cache: bool = True,
    cleanup: bool = True,
) -> Workbook:
    """Load workbook from Excel file or converted PDF."""
    ref = _to_file_ref(file_ref)
    if not _is_pdf(ref):
        return load_excel_workbook(ref.location)

    artifact = convert_pdf_to_excel_auto(
        ref,
        enable_cache=enable_cache,
    )
    if not artifact.success or not artifact.xlsx_path:
        raise RuntimeError(f"PDF->Excel conversion failed: {artifact.diagnostics}")

    try:
        return load_excel_workbook(artifact.xlsx_path)
    finally:
        if cleanup:
            _cleanup(artifact.temp_files)
