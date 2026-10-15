"""FastAPI wire schemas consumed by the Web client.

These types describe HTTP JSON only. UI-only state belongs in apps/web.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from pydantic import TypeAdapter

AnalysisMode = Literal["standard", "deep"]
FindingStatus = Literal["PASS", "NG", "ERR", "UNABLE", "NA"]
MaterialRole = Literal["target", "reference"]
CoverageState = Literal["mapped", "unsupported", "executed"]
ProgressPhase = Literal["reading", "planning", "checking", "validating"]


def _inline_schema(schema_type) -> dict[str, Any]:
    raw = TypeAdapter(schema_type).json_schema()
    definitions = raw.pop("$defs", {})

    def resolve(value):
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            if name == "EngineRuleBody":
                return {"$ref": "#/components/schemas/EngineRuleBody"}
            return resolve(definitions[name])
        return {key: resolve(item) for key, item in value.items() if key != "$defs"}

    return resolve(raw)


def openapi_body(schema_type, *, required: bool = True) -> dict[str, Any]:
    """Document a JSON body without replacing the route's existing dict parsing."""
    return {
        "requestBody": {
            "required": required,
            "content": {"application/json": {"schema": _inline_schema(schema_type)}},
        }
    }


class PublicConfig(TypedDict):
    git_sha: str
    profile: str
    api_url: str
    deep_offered: bool
    feishu_app_id: str
    current_access: str
    usage_charge_enabled: bool
    login: str


class IdentityPublic(TypedDict):
    provider: str
    app_id: str
    tenant_key: str
    subject: str


class PrincipalPublic(TypedDict):
    user_id: str
    tenant_id: str
    membership: str
    display_name: str
    identity: IdentityPublic


class WritebackTarget(TypedDict):
    available: bool
    provider: NotRequired[str]
    object_kind: NotRequired[str]


class FileVersion(TypedDict):
    file_id: str
    id: str
    original_filename: str
    status: str
    content_type: str
    byte_size: int
    sha256: str | None
    origin: NotRequired[WritebackTarget]


class FileCreateRequest(TypedDict):
    original_filename: str
    byte_size: int
    content_type: str


class FileFinalizeRequest(TypedDict, total=False):
    sha256: str


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
    predecessor_id: str | None
    type: str | None
    rule_profile: str | None
    customers: list[str] | None
    models: list[str] | None
    parts: list[str] | None


class RuleItem(TypedDict):
    id: str
    category: str
    priority: str | None
    rule_to_check: str
    type_of_rule: str


class EngineRuleBody(TypedDict):
    kind: Literal["engine"]
    type: str
    rule_profile: str
    customers: list[str]
    models: list[str]
    parts: list[str]
    rules: list[RuleItem]


class PresenceRuleBody(TypedDict):
    kind: Literal["presence"]


class BoundRule(TypedDict):
    id: str
    title: str
    body: EngineRuleBody | PresenceRuleBody


class RuleDetail(TypedDict):
    id: str
    title: str
    created_at: str
    body: EngineRuleBody | PresenceRuleBody
    source_file_id: str | None
    source_file_version_id: str | None
    predecessor_id: NotRequired[str | None]
    created_by: NotRequired[str | None]
    case_context: NotRequired[CaseContext]


class RuleCandidate(TypedDict):
    title: str
    body: EngineRuleBody
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
    body: EngineRuleBody
    source_file_version_id: str
    predecessor_id: NotRequired[str | None]
    request_id: NotRequired[str]
    case_context: NotRequired[CaseContext]


class JobError(TypedDict):
    error: str


class AnalysisJob(TypedDict):
    id: str
    status: str
    result: NotRequired[Analysis | JobError | None]


class LatestAnalysis(TypedDict):
    analysis: AnalysisJob | None


class FileAnalysisRequest(TypedDict):
    request_id: str
    source_file_id: str
    source_file_version_id: str


class RuleParseJob(TypedDict):
    id: str
    status: str
    result: NotRequired[RuleCandidate | JobError | None]
    source_file_id: NotRequired[str]
    source_file_version_id: NotRequired[str]
    document_type: NotRequired[str]


class MaterialWire(TypedDict):
    file_id: str
    file_version_id: str
    original_filename: str
    role: MaterialRole
    rule_version_id: str | None
    rule: BoundRule | None
    status: str


class CaseContext(TypedDict):
    registration: str


class Draft(TypedDict):
    id: str
    status: str
    items: list[MaterialWire]
    updated_at: str
    predecessor_id: str | None
    analysis_mode: AnalysisMode
    writeback: WritebackTarget
    case_context: NotRequired[CaseContext]


class DraftCreateRequest(TypedDict, total=False):
    predecessor_id: str | None


class DraftInputItem(TypedDict):
    file_version_id: str
    role: MaterialRole
    rule_version_id: str | None


class WritebackTargetRequest(TypedDict):
    provider: Literal["feishu"]
    object_kind: str
    object_ref: str


class DraftSaveRequest(TypedDict, total=False):
    items: list[DraftInputItem]
    analysis_mode: AnalysisMode
    writeback_target: WritebackTargetRequest | None
    case_context: CaseContext


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
    evidence: Evidence | None
    semantic: SemanticEvidence


class ReviewCitation(TypedDict):
    file_version_id: str
    location: str
    excerpt: str


class Review(TypedDict):
    judgment: str
    rationale: str
    next_step: str
    version: int
    citations: NotRequired[list[ReviewCitation]]


class Finding(TypedDict):
    item_key: str
    status: FindingStatus
    item: str
    classification: str
    suggestions: str
    priority: str | None
    locator: FindingLocator | None
    review: Review | None


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
    review: NotRequired[Review | None]


class Coverage(TypedDict, total=False):
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
    coverage: Coverage
    predecessor_id: str | None
    source_draft_id: str | None
    result_attempt: int | None
    created_at: str | None
    analysis_mode: AnalysisMode
    writeback: WritebackTarget
    case_context: NotRequired[CaseContext]
    progress: RoundProgress | None
    failure: RoundFailure | None
    inputs: list[MaterialWire]
    findings: list[Finding]
    executor: NotRequired[str]
    executor_revision: NotRequired[str]


class RoundSummaryRule(TypedDict):
    id: str
    title: str


class RoundSummaryInput(TypedDict):
    role: MaterialRole
    original_filename: str
    file_version_id: str
    rule: RoundSummaryRule | None


class RoundSummary(TypedDict):
    id: str
    execution: str
    predecessor_id: str | None
    created_at: str | None
    analysis_mode: AnalysisMode
    inputs: list[RoundSummaryInput]
    result_counts: dict[str, int]
    reviewed_count: int
    uncovered_count: NotRequired[int]
    case_title: str
    case_scope: str


class RoundList(TypedDict):
    rounds: list[RoundSummary]
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
    citations: NotRequired[list[ReviewCitation]]


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


class DocLink(TypedDict):
    token: str
    name: str
    doc_type: str
    type: str
    url: str


class DocLinksResponse(TypedDict):
    doc_links: list[DocLink]


class FeishuFileImportRequest(TypedDict):
    file_token: str
    doc_type: str


class PreviewRow(TypedDict):
    number: int
    text: str


class PreviewResponse(TypedDict):
    available: bool
    notice: str
    text: NotRequired[str]
    sheets: NotRequired[list[str]]
    sheet: NotRequired[str]
    start_row: NotRequired[int]
    end_row: NotRequired[int]
    rows: NotRequired[list[PreviewRow]]
    next_row: NotRequired[int | None]


class OfficePreviewResponse(TypedDict):
    available: bool
    notice: NotRequired[str]
    script_url: NotRequired[str]
    config: NotRequired[dict[str, Any]]


class ReportExport(TypedDict):
    status: str
    document_id: str | None
    id: NotRequired[str]
    error_code: NotRequired[str | None]


class ReportEnvelope(TypedDict):
    report: ReportExport | None


class WritebackResponse(TypedDict, total=False):
    inserted: bool
    round_id: str
    return_url: str


class HostLoginRequest(TypedDict):
    code: str
    redirect_uri: NotRequired[str]
