"""FastAPI wire schemas consumed by the Web client.

These types describe HTTP JSON only. UI-only state belongs in apps/web.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from fastapi import FastAPI
from pydantic import TypeAdapter

AnalysisMode = Literal["standard", "deep"]
FindingStatus = Literal["PASS", "NG", "ERR", "UNABLE", "NA"]
MaterialRole = Literal["target", "reference"]
CoverageState = Literal["mapped", "unsupported", "executed", "execution_error"]
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
    personal_drive_upload: bool


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
    request_id: NotRequired[str]
    sha256: NotRequired[str]
    feishu_folder_token: NotRequired[str]


class FileFinalizeRequest(TypedDict, total=False):
    sha256: str


class Metadata(TypedDict):
    """Recognized file information: part is the part name, not its number."""

    customer: str | None
    model: str | None
    part: str | None
    part_number: NotRequired[str | None]


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
    customer_scope: Literal["all", "restricted", "unconfirmed"]


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
    customer_scope: Literal["all", "restricted", "unconfirmed"]
    source_file_id: str | None
    source_file_version_id: str | None
    predecessor_id: NotRequired[str | None]
    created_by: NotRequired[str | None]
    case_context: NotRequired[CaseContext]


class RuleCandidate(TypedDict):
    title: str
    body: EngineRuleBody
    source_file_version_id: str


class RuleSourceUnit(TypedDict):
    id: str
    location: str
    excerpt: str


class RuleSources(TypedDict):
    file_version_id: str
    units: list[RuleSourceUnit]


class RulesList(TypedDict):
    rules: list[Rule]


class RuleLibraryScope(TypedDict):
    customer: str
    part: str
    revision: str
    allCustomers: bool
    allParts: bool
    allRevisions: bool


class RuleLibraryEntry(TypedDict):
    id: str
    title: str
    version: str
    scope: RuleLibraryScope
    reason: str
    created_at: str
    predecessor_id: str | None
    clause_count: int


class RuleLibrary(TypedDict):
    versions: list[RuleLibraryEntry]


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
    customer_scope: NotRequired[Literal["all"]]
    registration: NotRequired[str]


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
    request_id: str
    predecessor_id: str | None
    items: list[DraftInputItem]
    case_context: CaseContext


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
    kind: str
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
    rule_version_id: NotRequired[str]
    rule_item_id: NotRequired[str]
    proof_kind: NotRequired[Literal["query", "semantic"]]
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
    verification: NotRequired[dict[str, str | int] | None]


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
    preparation_allowed: NotRequired[bool]
    result_attempt: int | None
    result_job_id: NotRequired[str | None]
    created_at: str | None
    analysis_mode: AnalysisMode
    writeback: WritebackTarget
    case_context: NotRequired[CaseContext]
    progress: RoundProgress | None
    failure: RoundFailure | None
    inputs: list[MaterialWire]
    findings: list[Finding]
    read_view: NotRequired[AuditRoundReadView]
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
    source_draft_id: NotRequired[str | None]
    task_id: NotRequired[str | None]
    execution: str
    predecessor_id: str | None
    created_at: str | None
    updated_at: NotRequired[str | None]
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
    file_version_id: NotRequired[str | None]
    external_status: NotRequired[str]
    external_record_id: NotRequired[str | None]
    external_confirmed_at: NotRequired[str | None]
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
    request_id: NotRequired[str]


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


class AuditFileValidation(TypedDict):
    state: Literal["pending", "checking", "valid", "invalid", "error"]
    reasons: list[str]


class FileValidationJob(TypedDict):
    id: str
    source_file_id: str
    source_file_version_id: str
    status: str
    result: AuditFileValidation | JobError | None


class LatestFileValidation(TypedDict):
    validation: FileValidationJob | None


class MatchingCandidate(TypedDict):
    id: str
    title: str


class AuditRuleMatch(TypedDict):
    state: Literal["pending", "matching", "matched", "reference", "unmatched", "failed"]
    metadata: Metadata
    document_type: str | None
    rule: BoundRule | None
    gaps: list[str]
    candidates: NotRequired[list[MatchingCandidate]]


class AuditProgress(TypedDict):
    completed: int
    total: int


class AuditCompletionEstimate(TypedDict):
    """Service estimate of task completion, including initial reports under D3.

    Times are timezone-qualified RFC 3339; observed_at <= earliest_at <= latest_at.
    This interval is not a progress counter or a client-side countdown.
    """

    observed_at: str
    earliest_at: str
    latest_at: str


class AuditEvidenceReference(TypedDict):
    file_version_id: str
    locator: SemanticLocator | None
    excerpt: str
    location_gap: str | None
    rendition: Literal["original", "derived"]
    derived_file_version_id: str | None


class AuditClauseEvaluation(TypedDict):
    rule_item: RuleItem
    execution: Literal["queued", "running", "completed", "failed"]
    coverage: CoverageState
    finding: Finding | None
    evidence_verified: bool
    scope: SemanticScope | None
    scope_notice: str | None
    evidence: list[AuditEvidenceReference]
    review_item_key: NotRequired[str]
    review: NotRequired[Review | None]


class AuditResultCounts(TypedDict):
    """Terminal checks only: failed/ERR, uncovered, unknown, then verified PASS/NG.

    Categories are exclusive; queued/running and verified NA do not add counts.
    """

    pass_count: int
    ng_count: int
    unknown_count: int
    not_covered_count: int
    error_count: int


class AuditFileSummary(TypedDict):
    conclusion: Literal["PASS", "NG", "UNKNOWN"]
    text: str
    corrective_actions: list[str]
    extended_suggestions: list[str]


class AuditReportReference(TypedDict):
    task_id: str
    round_id: str
    file_version_id: str
    result_attempt: int
    report_id: str


class AuditReportArtifact(TypedDict):
    artifact_id: str
    filename: str
    content_type: Literal["application/pdf", "application/zip"]
    byte_size: int


class AuditReportPackageRequest(TypedDict):
    request_id: str
    reports: list[AuditReportReference]


class AuditReportPackageRetryRequest(TypedDict):
    request_id: str
    package_id: str
    expected_attempt: int


class AuditReportRegenerationRequest(TypedDict):
    request_id: str
    reference: AuditReportReference


class AuditFileReport(TypedDict):
    state: Literal["not_eligible", "pending", "generating", "ready", "failed"]
    report_id: str | None
    reason: str | None
    generated_at: NotRequired[str | None]
    artifact: NotRequired[AuditReportArtifact | None]
    regenerate_allowed: NotRequired[bool]
    regenerate_reason: NotRequired[str | None]
    generation_request: NotRequired[AuditAcceptance | None]


class AuditFileReview(TypedDict):
    round_id: str
    file_version_id: str
    result_attempt: int | None
    rule: BoundRule
    execution: Literal["queued", "running", "completed", "review_failed"]
    progress: AuditProgress
    failure: RoundFailure | None
    conclusion: Literal["PASS", "NG", "UNKNOWN"] | None
    counts: AuditResultCounts
    checks: list[AuditClauseEvaluation]
    unmapped_findings: NotRequired[list[Finding]]
    summary: AuditFileSummary | None
    report: AuditFileReport


class AuditRoundReadFile(TypedDict):
    file: FileVersion
    role: MaterialRole
    rule: BoundRule | None
    audit: AuditFileReview | None


class AuditRoundReportSource(TypedDict):
    task_id: str | None
    access: AuditArchiveAccess


class AuditRoundReadView(TypedDict):
    files: list[AuditRoundReadFile]
    unassigned_findings: list[Finding]
    report_source: NotRequired[AuditRoundReportSource]


class AuditAcceptance(TypedDict):
    request_id: str
    state: Literal["pending", "accepted", "rejected"]
    reason: str | None


class AuditReportUpdate(TypedDict):
    reference: AuditReportReference
    receipt: AuditAcceptance
    report: AuditFileReport | None


class AuditReportPackageOutcome(TypedDict):
    """Preparation of one frozen report, not a downloadable partial package."""

    reference: AuditReportReference
    state: Literal["pending", "prepared", "failed"]
    reason: str | None
    filename: NotRequired[str]


class AuditReportPackage(TypedDict):
    """One recoverable packaging job; report references are fixed at creation."""

    package_id: str | None
    attempt: NotRequired[int]
    reports: list[AuditReportReference]
    outcomes: NotRequired[list[AuditReportPackageOutcome]]
    receipt: AuditAcceptance
    state: Literal["pending", "generating", "ready", "failed"]
    artifact: AuditReportArtifact | None
    reason: str | None
    retry_allowed: bool


class AuditFileDisplayValues(TypedDict):
    """Complete display snapshot; null clears a value, part is the part name."""

    customer: str | None
    model: str | None
    part_number: str | None
    part: str | None


class AuditFileDisplayInfo(TypedDict):
    """Revisioned display correction, never a replacement for frozen metadata."""

    revision: int
    values: AuditFileDisplayValues


class AuditFileDisplayReference(TypedDict):
    task_id: str
    round_id: str
    file_version_id: str


class AuditFileDisplayRequest(TypedDict):
    """Canonical UUID identities keep the original request and receipt identical."""

    request_id: str
    reference: AuditFileDisplayReference
    expected_revision: int
    values: AuditFileDisplayValues


class AuditFileDisplayReceipt(TypedDict):
    """Only rejected/conflict prove no write; unknown retains the request.

    A saved receipt contains that request's next revision, not the latest Task.
    A conflict contains the current display snapshot for explicit reconciliation.
    No receipt grants current editing permission.
    """

    request: AuditFileDisplayRequest
    state: Literal["pending", "saved", "rejected", "conflict", "unknown"]
    info: AuditFileDisplayInfo | None
    reason: str | None


class AuditRecognitionFile(TypedDict):
    file: FileVersion
    selection: Literal["selected", "unselected", "removed"]
    removal_reason: str | None
    validation: AuditFileValidation
    validation_id: NotRequired[str]
    match: AuditRuleMatch | None
    preferred_rule_version_id: NotRequired[str]
    replacement_of_file_version_id: NotRequired[str]
    reference_only: NotRequired[bool]
    rule_options: NotRequired[list[MatchingCandidate]]


class AuditTaskFile(AuditRecognitionFile):
    audit: NotRequired[AuditFileReview | None]
    display_info: NotRequired[AuditFileDisplayInfo | None]
    display_edit_access: NotRequired[AuditArchiveAccess]


class AuditShareTargetReference(TypedDict):
    id: str
    kind: Literal["user", "group"]


class AuditShareTarget(AuditShareTargetReference):
    display_name: str
    organization_id: str
    eligibility: Literal["eligible", "blocked", "unknown"]
    reason: str | None


class AuditShareTargetsPage(TypedDict):
    organization_id: str
    organization_name: str
    targets: list[AuditShareTarget]
    next_page_token: str | None


class AuditShareContent(TypedDict):
    kind: Literal["link", "pdf", "zip"]
    artifact: AuditReportArtifact | None
    package_id: str | None


class AuditReportShareRequest(TypedDict):
    request_id: str
    organization_id: str
    reports: list[AuditReportReference]
    targets: list[AuditShareTargetReference]
    content: AuditShareContent


class AuditShareAttemptReference(TypedDict):
    target: AuditShareTargetReference
    attempt_id: str


class AuditShareReadRequest(TypedDict):
    """Read specific attempts; a missing transport reply is not a retry permit."""

    request_id: str
    attempts: list[AuditShareAttemptReference]


class AuditShareDelivery(TypedDict):
    target: AuditShareTarget
    attempt_id: str
    state: Literal["pending", "sent", "failed", "unknown"]
    reason: str | None
    retry_allowed: bool


class AuditReportShare(TypedDict):
    """Current recoverable send operation, not a user-facing share history."""

    request: AuditReportShareRequest
    receipt: AuditAcceptance
    deliveries: list[AuditShareDelivery]


class AuditReportShareRetry(TypedDict):
    target: AuditShareTargetReference
    attempt_id: str


class AuditSharedReport(TypedDict):
    name: str
    conclusion: Literal["PASS", "NG"]
    rule: RoundSummaryRule
    reference: AuditReportReference
    report: AuditFileReport


class AuditSharedReports(TypedDict):
    reports: list[AuditSharedReport]
    archive: AuditReportArtifact | None


class RelatedAuditTask(TypedDict):
    id: str
    number: str


class AuditTaskIdentity(TypedDict):
    id: str
    number: str
    state: Literal["matching", "pending_confirm", "reviewing", "completed"]
    analysis_mode: AnalysisMode
    created_at: str
    updated_at: str
    source_draft_id: str | None
    round_id: str | None
    customer_preference: str | None
    completion_reason: Literal["no_matching_rules", "processed"] | None
    estimated_points: int | None
    input_revision: NotRequired[int]
    recognition_request_id: NotRequired[str]
    predecessor_id: NotRequired[str]
    predecessor_task_id: NotRequired[str]
    successor_tasks: NotRequired[list[RelatedAuditTask]]


class AuditRecognitionInput(TypedDict):
    file_version_id: str
    validation_id: str | None
    selection: Literal["selected", "unselected", "removed"]
    removal_reason: str | None
    preferred_rule_version_id: NotRequired[str | None]
    replacement_of_file_version_id: NotRequired[str | None]
    reference_only: NotRequired[bool]


class AuditRecognitionRequest(TypedDict):
    request_id: str
    analysis_mode: AnalysisMode
    customer_preference: str | None
    files: list[AuditRecognitionInput]
    task_id: NotRequired[str]
    expected_revision: NotRequired[int]
    predecessor_id: NotRequired[str]


class AuditReauditInputs(TypedDict):
    predecessor_id: str
    analysis_mode: AnalysisMode
    customer_preference: str | None
    files: list[AuditRecognitionFile]


class AuditRecognitionRead(TypedDict):
    request_id: str
    task_id: str
    state: Literal["accepted", "unknown"]
    input_revision: int | None
    task: AuditTask | None


class AuditTaskEndRequest(TypedDict):
    expected_revision: int
    removals: NotRequired[list[AuditTaskRemoval]]


class AuditTaskConfirmRequest(TypedDict):
    request_id: str
    expected_revision: int


class AuditTaskRemoval(TypedDict):
    file_version_id: str
    removal_reason: str


class AuditRecognitionTasks(TypedDict):
    tasks: list[AuditTaskSummary]
    next_offset: int | None


class AuditTaskSummaryFile(TypedDict):
    file: FileVersion
    selection: Literal["selected", "unselected", "removed"]


class AuditTaskSummary(TypedDict):
    id: str
    number: str
    state: Literal["matching", "pending_confirm", "reviewing", "completed"]
    input_revision: int
    analysis_mode: AnalysisMode
    created_at: str
    updated_at: str
    round_id: str | None
    completion_reason: Literal["no_matching_rules", "processed"] | None
    files: list[AuditTaskSummaryFile]
    execution: NotRequired[Literal["queued", "running", "completed", "failed"]]
    result_counts: NotRequired[dict[str, int]]
    reviewed_count: NotRequired[int]
    uncovered_count: NotRequired[int]


class AuditNotificationDelivery(TypedDict):
    state: Literal["not_due", "queued", "preparing", "sending", "unknown", "sent", "failed", "suppressed"]
    reason: str | None


class AuditNotificationPreference(TypedDict):
    task_id: str
    enabled: bool
    revision: int
    can_change: bool
    available: bool
    delivery: AuditNotificationDelivery


class AuditNotificationPreferenceRequest(TypedDict):
    enabled: bool
    expected_revision: int


class AuditTask(AuditTaskIdentity):
    """Persisted Task/Round view; optional delivery operations need their own API."""

    files: list[AuditTaskFile]
    acceptance: NotRequired[AuditAcceptance]
    progress: NotRequired[AuditProgress]
    completion_estimate: NotRequired[AuditCompletionEstimate | None]
    notification_enabled: NotRequired[bool]
    notification_revision: NotRequired[int]
    usage_notice: NotRequired[str | None]
    report_package: NotRequired[AuditReportPackage | None]
    report_share: NotRequired[AuditReportShare | None]


class AuditResultReference(TypedDict):
    task_id: str
    round_id: str
    file_version_id: str
    result_attempt: int | None


class AuditArchiveAccess(TypedDict):
    state: Literal["allowed", "denied", "unknown", "deleted"]
    reason: str | None


class AuditHistoryEntry(TypedDict):
    document_id: str
    reference: AuditResultReference
    sequence: int
    audited_at: str
    initiator: str
    execution: Literal["queued", "running", "completed", "review_failed"]
    conclusion: Literal["PASS", "NG", "UNKNOWN"] | None
    source_revision: str | None
    report: AuditFileReport
    report_access: AuditArchiveAccess


class AuditArchiveDocument(TypedDict):
    """Opaque product identity mapped to a confirmed provider document, not file_id."""

    document_id: str
    provider: Literal["feishu"]
    title: str
    metadata: Metadata
    document_type: str | None
    updated_at: str
    latest_completed: AuditHistoryEntry | None
    latest_activity: AuditHistoryEntry | None


class AuditArchiveCategory(TypedDict):
    id: str
    label: str
    count: int
    parts: list[AuditArchivePart]


class AuditArchivePart(TypedDict):
    id: str
    label: str
    count: int


class AuditArchiveQuery(TypedDict):
    search: str
    category_id: str | None
    document_type: str | None
    customer: str | None
    initiator: str | None
    from_date: str | None
    through_date: str | None
    page: int
    page_size: Literal[10]


class AuditArchivePage(TypedDict):
    query: AuditArchiveQuery
    documents: list[AuditArchiveDocument]
    total: int
    document_count: int
    categories: list[AuditArchiveCategory]
    document_types: list[str]
    customers: list[str]
    initiators: list[str]


class AuditHistoryPage(TypedDict):
    document: AuditArchiveDocument
    entries: list[AuditHistoryEntry]
    default_reference: AuditResultReference | None


class AuditArchiveOriginal(TypedDict):
    file_version_id: str
    file: FileVersion | None
    access: AuditArchiveAccess


class AuditHistoryDetail(TypedDict):
    """Authorized frozen content, not permission to read the whole source task.

    report_share, when present, belongs to this single full report reference.
    Do not inherit the source task's latest, unrelated share operation.
    """

    entry: AuditHistoryEntry
    file: AuditTaskFile
    frozen_file_version_ids: list[str]
    originals: list[AuditArchiveOriginal]
    source_task_access: AuditArchiveAccess
    report_access: AuditArchiveAccess
    report_share: NotRequired[AuditReportShare | None]


class AuditUploadItem(TypedDict):
    """A request receipt, not proof of parsing or a task before matching.

    retry_allowed requires a terminal failure with no previous write in flight.
    Unknown/non-ready observation alone never permits another upload.
    """

    request_id: str
    state: Literal["uploading", "ready", "failed", "unknown"]
    file: FileVersion | None
    reason: str | None
    retry_allowed: bool
    resume_allowed: NotRequired[bool]


class AuditUsageQuery(TypedDict):
    range: Literal["7d", "30d", "90d", "all"]
    search: str
    page: int
    page_size: Literal[10]


class AuditUsageMetric(TypedDict):
    value: int | None
    reason: str | None


class AuditUsageSummary(TypedDict):
    """Acceptance-month totals; settlement never moves the original obligation."""

    month: str
    timezone: str
    as_of: str
    consumption_count: AuditUsageMetric
    charged_points: AuditUsageMetric
    pending_points: AuditUsageMetric
    free_remaining: AuditUsageMetric
    free_total: AuditUsageMetric


class AuditUsageOrder(TypedDict):
    """Verified debit order and actual debit time, never a usage-report ACK."""

    order_id: str
    confirmed_at: str


class AuditUsageAllocation(TypedDict):
    free_points: int | None
    paid_points: int | None
    reason: str | None


class AuditUsageSettlement(TypedDict):
    state: Literal["charged", "pending", "processing", "unknown", "trial"]
    reason: str | None


class AuditUsageEntry(TypedDict):
    """One stable file-audit obligation, not a free/paid pool slice or attempt."""

    usage_id: str
    occurred_at: str
    debited_at: str | None
    filename: str
    audit_sequence: int | None
    initiator: str
    points: Literal[500]
    allocation: AuditUsageAllocation
    orders: list[AuditUsageOrder]
    order_reason: str | None
    settlement: AuditUsageSettlement
    reference: AuditResultReference
    document_id: str | None
    audit_access: AuditArchiveAccess


class AuditUsagePage(TypedDict):
    """Read-only projection; date windows use summary.timezone, not browser time."""

    organization_id: str
    organization_name: str
    query: AuditUsageQuery
    summary: AuditUsageSummary
    entries: list[AuditUsageEntry]
    total: int
    from_date: str | None
    through_date: str
    notices: list[Literal["ai_disabled", "no_package", "paid_insufficient"]]
    unattributed: NotRequired[AuditUsageUnattributed]


class AuditUsageUnattributed(TypedDict):
    slice_count: int
    points: int
    reason: str | None


class AuditCustomer(TypedDict):
    customer_id: str
    name: str


class AuditCustomerCatalog(TypedDict):
    """Customers with current published rules in the requesting tenant."""

    customers: list[AuditCustomer]


class AuditSupportInfo(TypedDict):
    """Current confirmed service configuration, not invented fallback contacts."""

    state: Literal["configured", "unavailable"]
    config_version: str | None
    phone: str | None
    email: str | None
    service_hours: str | None
    qr_available: bool


def add_ui_contract(app: FastAPI) -> None:
    """Extend native OpenAPI components, without documenting fake endpoints."""
    native_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        spec = native_openapi()
        schemas = spec.setdefault("components", {}).setdefault("schemas", {})
        runtime_schemas = set(schemas)
        definitions = {}
        for contract in (
            AuditTask,
            AuditFileDisplayReceipt,
            AuditUploadItem,
            AuditReportUpdate,
            AuditShareTargetsPage,
            AuditShareReadRequest,
            AuditArchivePage,
            AuditHistoryPage,
            AuditHistoryDetail,
            AuditCustomerCatalog,
            AuditSupportInfo,
            AuditUsagePage,
        ):
            root = TypeAdapter(contract).json_schema(ref_template="#/components/schemas/{model}")
            definitions.update(root.pop("$defs", {}))
            definitions[contract.__name__] = root
        for name, definition in definitions.items():
            if name in schemas:
                existing = {key: value for key, value in schemas[name].items() if key != "x-contract-stage"}
                if existing != definition:
                    raise ValueError(f"conflicting OpenAPI schema: {name}")
            else:
                schemas[name] = definition
        for name in definitions:
            if name.startswith("Audit") and name not in runtime_schemas:
                schemas[name]["x-contract-stage"] = "ui-draft"
        return spec

    app.openapi = openapi  # type: ignore[method-assign]  # FastAPI's schema-extension hook.
