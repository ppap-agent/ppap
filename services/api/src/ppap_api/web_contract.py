"""FastAPI wire schemas consumed by the Web client.

These types describe HTTP JSON only. UI-only state belongs in apps/web.
"""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


AnalysisMode = Literal["standard", "deep"]
FindingStatus = Literal["PASS", "NG", "ERR", "UNABLE", "NA"]
MaterialRole = Literal["target", "reference"]
CoverageState = Literal["mapped", "unsupported", "executed"]
ProgressPhase = Literal["reading", "planning", "checking", "validating"]


class ErrorResponse(TypedDict):
    error: str


class PublicConfig(TypedDict):
    api_url: str
    profile: str
    login: str
    deep_offered: NotRequired[bool]
    feishu_app_id: NotRequired[str]


class PrincipalPublic(TypedDict):
    display_name: str
    user_id: NotRequired[str]
    tenant_id: NotRequired[str]
    tenant_key: NotRequired[str]


class FileVersion(TypedDict):
    file_id: str
    id: str
    original_filename: str
    status: str
    content_type: NotRequired[str]
    byte_size: NotRequired[int]
    sha256: NotRequired[str | None]


class FileCreateRequest(TypedDict):
    original_filename: str
    byte_size: int
    content_type: str


class FileStatus(TypedDict):
    status: str


class Metadata(TypedDict):
    customer: str | None
    model: str | None
    part: str | None


class Analysis(TypedDict):
    metadata: Metadata
    document_type: str | None
    matched_rule_id: str | None
    matching_status: str
    notices: list[str]


class Rule(TypedDict):
    id: str
    title: str
    kind: str
    created_at: str
    type: NotRequired[str]
    predecessor_id: NotRequired[str | None]
    customers: NotRequired[list[str]]
    models: NotRequired[list[str]]
    parts: NotRequired[list[str]]


class RuleItem(TypedDict):
    id: str
    category: str
    priority: str | None
    rule_to_check: str
    type_of_rule: str


class RuleBody(TypedDict):
    kind: str
    type: str
    rule_profile: str
    customers: list[str]
    models: list[str]
    parts: list[str]
    rules: list[RuleItem]


class RuleDetail(TypedDict):
    id: str
    title: str
    kind: str
    created_at: str
    body: RuleBody
    source_file_id: str
    source_file_version_id: str
    type: NotRequired[str]
    predecessor_id: NotRequired[str | None]
    customers: NotRequired[list[str]]
    models: NotRequired[list[str]]
    parts: NotRequired[list[str]]


class RuleCandidate(TypedDict):
    title: str
    body: RuleBody
    source_file_version_id: str


class RulesList(TypedDict):
    rules: list[Rule]


class RuleTypes(TypedDict):
    types: list[str]


class RuleParseRequest(TypedDict):
    request_id: str
    source_file_id: str
    source_file_version_id: str
    document_type: NotRequired[str]


class RulePublishRequest(TypedDict):
    title: str
    body: RuleBody
    source_file_version_id: str
    predecessor_id: NotRequired[str | None]


class AnalysisJobResult(TypedDict, total=False):
    metadata: Metadata
    document_type: str | None
    matched_rule_id: str | None
    matching_status: str
    notices: list[str]
    error: str


class AnalysisJob(TypedDict):
    id: str
    status: str
    result: NotRequired[AnalysisJobResult]


class LatestAnalysis(TypedDict):
    analysis: AnalysisJob | None


class FileAnalysisRequest(TypedDict):
    request_id: str
    source_file_id: str
    source_file_version_id: str


class RuleParseJobResult(TypedDict, total=False):
    title: str
    body: RuleBody
    source_file_version_id: str
    error: str


class RuleParseJob(TypedDict):
    id: str
    status: str
    result: NotRequired[RuleParseJobResult]
    source_file_id: NotRequired[str]
    source_file_version_id: NotRequired[str]
    document_type: NotRequired[str]


class MaterialWire(TypedDict):
    file_id: str
    file_version_id: str
    original_filename: str
    role: MaterialRole
    rule_version_id: str | None
    rule: NotRequired[Rule]


class WritebackTarget(TypedDict):
    available: bool
    provider: NotRequired[str]
    object_kind: NotRequired[str]


class Draft(TypedDict):
    id: str
    status: str
    items: list[MaterialWire]
    updated_at: NotRequired[str]
    predecessor_id: NotRequired[str | None]
    analysis_mode: NotRequired[AnalysisMode]
    writeback: NotRequired[WritebackTarget]


class DraftCreateRequest(TypedDict, total=False):
    predecessor_id: str | None


class DraftInputItem(TypedDict):
    file_version_id: str
    role: MaterialRole
    rule_version_id: str | None


class DraftSaveRequest(TypedDict, total=False):
    items: list[DraftInputItem]
    analysis_mode: AnalysisMode
    writeback_target: dict[str, Any] | None


class DraftAcceptRequest(TypedDict):
    idempotency_key: str
    predecessor_id: NotRequired[str | None]


class DraftList(TypedDict):
    drafts: list[Draft]
    next_offset: int | None


class XlsxCellLocator(TypedDict):
    kind: Literal["xlsx_cell"]
    sheet: str
    cell: str


class XlsxRangeLocator(TypedDict):
    kind: Literal["xlsx_range"]
    sheet: str
    start: str
    end: str


class PdfBlockLocator(TypedDict):
    kind: Literal["pdf_block"]
    page: int
    block: int


class DocxParagraphLocator(TypedDict):
    kind: Literal["docx_paragraph"]
    paragraph: int


class DocxCellLocator(TypedDict):
    kind: Literal["docx_cell"]
    table: int
    row: int
    column: int


SemanticLocator = XlsxCellLocator | XlsxRangeLocator | PdfBlockLocator | DocxParagraphLocator | DocxCellLocator


class SemanticSource(TypedDict):
    file_version_id: str
    filename: str
    role: MaterialRole
    locator: SemanticLocator
    excerpt: str


class SemanticScope(TypedDict):
    total: int
    checked: int
    unknown: int


class SemanticEvidence(TypedDict):
    rule_version_id: str
    clause_id: str
    rule_text: str
    scope: SemanticScope
    sources: list[SemanticSource]
    diagnostic: NotRequired[str]
    rule_source_file_version_id: NotRequired[str]


class EvidenceSource(TypedDict):
    kind: str
    locator: str


class Evidence(TypedDict):
    sources: NotRequired[list[EvidenceSource]]


class FindingLocator(TypedDict, total=False):
    file_id: str
    file_version_id: str
    filename: str
    locator: str
    rule_item_id: str
    evidence: Evidence
    semantic: SemanticEvidence


class Review(TypedDict):
    judgment: str
    rationale: str
    next_step: str
    version: int


class Finding(TypedDict):
    item_key: str
    status: FindingStatus
    item: str
    classification: str
    suggestions: str
    priority: NotRequired[str]
    locator: NotRequired[FindingLocator]
    review: NotRequired[Review]


class RuleCoverageItem(TypedDict):
    id: str
    rule_to_check: str
    coverage: CoverageState
    file_version_id: NotRequired[str]
    category: NotRequired[str]
    priority: NotRequired[str | None]
    reason: NotRequired[str]
    item_key: NotRequired[str]
    status: NotRequired[FindingStatus]


class Coverage(TypedDict):
    checked: list[str]
    unchecked: list[str]
    locator_gap: bool
    rule_items: NotRequired[list[RuleCoverageItem]]


class RoundProgress(TypedDict):
    phase: ProgressPhase
    updated_at: str
    completed_count: NotRequired[int]
    total_count: NotRequired[int]


class RoundFailure(TypedDict):
    code: str
    message: str
    retryable: bool


class Round(TypedDict):
    id: str
    execution: str
    inputs: list[MaterialWire]
    findings: list[Finding]
    predecessor_id: NotRequired[str]
    created_at: NotRequired[str]
    result_counts: NotRequired[dict[str, int]]
    reviewed_count: NotRequired[int]
    coverage: NotRequired[Coverage]
    analysis_mode: NotRequired[AnalysisMode]
    executor: NotRequired[str]
    executor_revision: NotRequired[str]
    progress: NotRequired[RoundProgress]
    failure: NotRequired[RoundFailure]
    writeback: NotRequired[WritebackTarget]


class RoundList(TypedDict):
    rounds: list[Round]
    next_offset: int | None


class ResubmitRequest(TypedDict, total=False):
    idempotency_key: str
    items: list[DraftInputItem]


class ReviewRequest(TypedDict):
    item_key: str
    judgment: str
    rationale: str
    next_step: str
    version: int


class UsageEvent(TypedDict):
    id: str
    created_at: str
    consumed_points: int
    is_free_trial: bool
    round_id: NotRequired[str | None]
    external_status: NotRequired[str]
    ai_item_id: NotRequired[str]


class UsageList(TypedDict):
    events: list[UsageEvent]
    next_offset: int | None


class QuotaEstimate(TypedDict, total=False):
    remaining_trial_ai_points: int
    estimated_points: int
    target_count: int
    accept_allowed: bool
    accept_reason: str
    quota_sufficient: bool
    enabled: bool
    user_quota: int
    ai_package_purchased: bool
    message: str
    detailed_message: str
    unavailable: bool
    usage_charge_enabled: bool


class DriveFile(TypedDict):
    token: str
    name: str
    type: str


class DriveFilesList(TypedDict):
    files: list[DriveFile]
    next_page_token: NotRequired[str]


class FeishuFileImportRequest(TypedDict):
    file_token: str
    doc_type: str


class PreviewRow(TypedDict):
    number: int
    text: str


class PreviewResponse(TypedDict, total=False):
    available: bool
    text: str
    notice: str
    sheets: list[str]
    sheet: str
    start_row: int
    end_row: int
    rows: list[PreviewRow]
    next_row: int | None


class OfficePreviewResponse(TypedDict, total=False):
    available: bool
    notice: str
    script_url: str
    config: dict[str, Any]


class ReportExport(TypedDict, total=False):
    id: str
    status: str
    document_id: str | None
    error_code: str | None


class ReportEnvelope(TypedDict):
    report: ReportExport | None


class WritebackResponse(TypedDict, total=False):
    inserted: bool
    round_id: str
    return_url: str


class HostLoginRequest(TypedDict):
    code: str
    redirect_uri: NotRequired[str]
