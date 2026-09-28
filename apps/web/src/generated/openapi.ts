/* eslint-disable */
// GENERATED FILE. Source authority: FastAPI app.openapi().
// Rebuild: uv run --no-sync python scripts/generate_web_contract.py

export interface components {
  schemas: {
    "Analysis": {
      "document_type": null | string;
      "matched_rule_id": null | string;
      "matching_status": string;
      "metadata": components["schemas"]["Metadata"];
      "notices": Array<string>;
    };
    "AnalysisJob": {
      "id": string;
      "result"?: components["schemas"]["Analysis"] | components["schemas"]["JobError"];
      "status": string;
    };
    "DocxCellLocator": {
      "column": number;
      "kind": "docx_cell";
      "row": number;
      "table": number;
    };
    "DocxParagraphLocator": {
      "kind": "docx_paragraph";
      "paragraph": number;
    };
    "Draft": {
      "analysis_mode"?: "deep" | "standard";
      "id": string;
      "items": Array<components["schemas"]["MaterialWire"]>;
      "predecessor_id"?: null | string;
      "status": string;
      "updated_at"?: string;
      "writeback"?: components["schemas"]["WritebackTarget"];
    };
    "DraftAcceptRequest": {
      "idempotency_key": string;
      "predecessor_id"?: null | string;
    };
    "DraftCreateRequest": {
      "predecessor_id"?: null | string;
    };
    "DraftInputItem": {
      "file_version_id": string;
      "role": "reference" | "target";
      "rule_version_id": null | string;
    };
    "DraftList": {
      "drafts": Array<components["schemas"]["Draft"]>;
      "next_offset": null | number;
    };
    "DraftSaveRequest": {
      "analysis_mode"?: "deep" | "standard";
      "items"?: Array<components["schemas"]["DraftInputItem"]>;
      "writeback_target"?: Record<string, unknown> | null;
    };
    "DriveFile": {
      "name": string;
      "token": string;
      "type": string;
    };
    "DriveFilesList": {
      "files": Array<components["schemas"]["DriveFile"]>;
      "next_page_token"?: string;
    };
    "DocLink": {
      "doc_type": string;
      "name": string;
      "token": string;
      "type": string;
      "url": string;
    };
    "DocLinksResponse": {
      "doc_links": Array<components["schemas"]["DocLink"]>;
    };
    "Evidence": {
      "sources"?: Array<components["schemas"]["EvidenceSource"]>;
    };
    "EvidenceSource": {
      "kind": string;
      "locator": string;
    };
    "FeishuFileImportRequest": {
      "doc_type": string;
      "file_token": string;
    };
    "FileAnalysisRequest": {
      "request_id": string;
      "source_file_id": string;
      "source_file_version_id": string;
    };
    "FileCreateRequest": {
      "byte_size": number;
      "content_type": string;
      "original_filename": string;
    };
    "FileVersion": {
      "byte_size"?: number;
      "content_type"?: string;
      "file_id": string;
      "id": string;
      "original_filename": string;
      "sha256"?: null | string;
      "status": string;
    };
    "Finding": {
      "classification": string;
      "item": string;
      "item_key": string;
      "locator"?: components["schemas"]["FindingLocator"];
      "priority"?: string;
      "review"?: components["schemas"]["Review"];
      "status": "ERR" | "NA" | "NG" | "PASS" | "UNABLE";
      "suggestions": string;
    };
    "FindingLocator": {
      "evidence"?: components["schemas"]["Evidence"];
      "file_id"?: string;
      "file_version_id"?: string;
      "filename"?: string;
      "locator"?: string;
      "rule_item_id"?: string;
      "semantic"?: components["schemas"]["SemanticEvidence"];
    };
    "HostLoginRequest": {
      "code": string;
      "redirect_uri"?: string;
    };
    "IdentityPublic": {
      "app_id": string;
      "provider": string;
      "subject": string;
      "tenant_key": string;
    };
    "JobError": {
      "error": string;
    };
    "LatestAnalysis": {
      "analysis": components["schemas"]["AnalysisJob"] | null;
    };
    "MaterialWire": {
      "file_id": string;
      "file_version_id": string;
      "original_filename": string;
      "role": "reference" | "target";
      "rule"?: components["schemas"]["Rule"];
      "rule_version_id": null | string;
    };
    "Metadata": {
      "customer": null | string;
      "model": null | string;
      "part": null | string;
    };
    "OfficePreviewResponse": {
      "available": boolean;
      "config"?: Record<string, unknown>;
      "notice"?: string;
      "script_url"?: string;
    };
    "PdfBlockLocator": {
      "block": number;
      "kind": "pdf_block";
      "page": number;
    };
    "PreviewResponse": {
      "available": boolean;
      "end_row"?: number;
      "next_row"?: null | number;
      "notice": string;
      "rows"?: Array<components["schemas"]["PreviewRow"]>;
      "sheet"?: string;
      "sheets"?: Array<string>;
      "start_row"?: number;
      "text"?: string;
    };
    "PreviewRow": {
      "number": number;
      "text": string;
    };
    "PrincipalPublic": {
      "display_name": string;
      "identity": components["schemas"]["IdentityPublic"];
      "membership": string;
      "tenant_id": string;
      "user_id": string;
    };
    "PublicConfig": {
      "api_url": string;
      "deep_offered"?: boolean;
      "feishu_app_id"?: string;
      "login": string;
      "profile": string;
    };
    "QuotaEstimate": {
      "accept_allowed"?: boolean;
      "accept_reason"?: string;
      "ai_package_purchased"?: boolean;
      "detailed_message"?: string;
      "enabled"?: boolean;
      "estimated_points"?: number;
      "message"?: string;
      "quota_sufficient"?: boolean;
      "remaining_trial_ai_points"?: number;
      "target_count"?: number;
      "unavailable"?: boolean;
      "usage_charge_enabled"?: boolean;
      "user_quota"?: number;
    };
    "ReportEnvelope": {
      "report": components["schemas"]["ReportExport"] | null;
    };
    "ReportExport": {
      "document_id": null | string;
      "error_code"?: null | string;
      "id": string;
      "status": string;
    };
    "ResubmitRequest": {
      "idempotency_key"?: string;
      "items"?: Array<components["schemas"]["DraftInputItem"]>;
    };
    "Review": {
      "judgment": string;
      "next_step": string;
      "rationale": string;
      "version": number;
    };
    "ReviewRequest": {
      "item_key": string;
      "judgment": string;
      "next_step": string;
      "rationale": string;
      "version": number;
    };
    "Round": {
      "analysis_mode"?: "deep" | "standard";
      "coverage"?: components["schemas"]["Coverage"];
      "created_at"?: string;
      "execution": string;
      "executor"?: string;
      "executor_revision"?: string;
      "failure"?: components["schemas"]["RoundFailure"];
      "findings": Array<components["schemas"]["Finding"]>;
      "id": string;
      "inputs": Array<components["schemas"]["MaterialWire"]>;
      "predecessor_id"?: string;
      "progress"?: components["schemas"]["RoundProgress"];
      "result_counts"?: Record<string, number>;
      "reviewed_count"?: number;
      "writeback"?: components["schemas"]["WritebackTarget"];
    };
    "RoundFailure": {
      "code": string;
      "message": string;
      "retryable": boolean;
    };
    "RoundList": {
      "next_offset": null | number;
      "rounds": Array<components["schemas"]["Round"]>;
    };
    "RoundProgress": {
      "completed_count"?: number;
      "phase": "checking" | "planning" | "reading" | "validating";
      "total_count"?: number;
      "updated_at": string;
    };
    "Rule": {
      "created_at": string;
      "customers"?: Array<string>;
      "id": string;
      "kind": string;
      "models"?: Array<string>;
      "parts"?: Array<string>;
      "predecessor_id"?: null | string;
      "title": string;
      "type"?: string;
    };
    "EngineRuleBody": {
      "customers": Array<string>;
      "kind": "engine";
      "models": Array<string>;
      "parts": Array<string>;
      "rule_profile": string;
      "rules": Array<components["schemas"]["RuleItem"]>;
      "type": string;
    };
    "PresenceRuleBody": {
      "kind": "presence";
    };
    "RuleCandidate": {
      "body": components["schemas"]["EngineRuleBody"];
      "source_file_version_id": string;
      "title": string;
    };
    "RuleCoverageItem": {
      "category"?: string;
      "coverage": "executed" | "mapped" | "unsupported";
      "file_version_id"?: string;
      "id": string;
      "item_key"?: string;
      "priority"?: null | string;
      "reason"?: string;
      "rule_to_check": string;
      "status"?: "ERR" | "NA" | "NG" | "PASS" | "UNABLE";
    };
    "RuleDetail": {
      "body": components["schemas"]["EngineRuleBody"] | components["schemas"]["PresenceRuleBody"];
      "created_at": string;
      "id": string;
      "predecessor_id"?: null | string;
      "source_file_id": null | string;
      "source_file_version_id": null | string;
      "title": string;
    };
    "RuleItem": {
      "category": string;
      "id": string;
      "priority": null | string;
      "rule_to_check": string;
      "type_of_rule": string;
    };
    "RuleParseJob": {
      "document_type"?: string;
      "id": string;
      "result"?: components["schemas"]["JobError"] | components["schemas"]["RuleCandidate"];
      "source_file_id"?: string;
      "source_file_version_id"?: string;
      "status": string;
    };
    "RuleParseRequest": {
      "document_type"?: string;
      "request_id": string;
      "source_file_id": string;
      "source_file_version_id": string;
    };
    "RulePublishRequest": {
      "body": components["schemas"]["EngineRuleBody"];
      "predecessor_id"?: null | string;
      "source_file_version_id": string;
      "title": string;
    };
    "RuleTypes": {
      "types": Array<string>;
    };
    "RulesList": {
      "rules": Array<components["schemas"]["Rule"]>;
    };
    "SemanticEvidence": {
      "clause_id": string;
      "diagnostic"?: string;
      "rule_source_file_version_id"?: string;
      "rule_text": string;
      "rule_version_id": string;
      "scope": components["schemas"]["SemanticScope"];
      "sources": Array<components["schemas"]["SemanticSource"]>;
    };
    "SemanticScope": {
      "checked": number;
      "total": number;
      "unknown": number;
    };
    "SemanticSource": {
      "excerpt": string;
      "file_version_id": string;
      "filename": string;
      "locator": components["schemas"]["DocxCellLocator"] | components["schemas"]["DocxParagraphLocator"] | components["schemas"]["PdfBlockLocator"] | components["schemas"]["XlsxCellLocator"] | components["schemas"]["XlsxRangeLocator"];
      "role": "reference" | "target";
    };
    "UsageEvent": {
      "ai_item_id"?: string;
      "consumed_points": number;
      "created_at": string;
      "external_status"?: string;
      "id": string;
      "is_free_trial": boolean;
      "round_id"?: null | string;
    };
    "UsageList": {
      "events": Array<components["schemas"]["UsageEvent"]>;
      "next_offset": null | number;
    };
    "WritebackResponse": {
      "inserted"?: boolean;
      "return_url"?: string;
      "round_id"?: string;
    };
    "WritebackTarget": {
      "available": boolean;
      "object_kind"?: string;
      "provider"?: string;
    };
    "XlsxCellLocator": {
      "cell": string;
      "kind": "xlsx_cell";
      "sheet": string;
    };
    "XlsxRangeLocator": {
      "end": string;
      "kind": "xlsx_range";
      "sheet": string;
      "start": string;
    };
    "Coverage": {
      "checked": Array<string>;
      "locator_gap": boolean;
      "rule_items"?: Array<components["schemas"]["RuleCoverageItem"]>;
      "unchecked": Array<string>;
    };
  }
}

export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name]
