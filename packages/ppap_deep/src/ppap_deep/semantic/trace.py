"""One replayable evaluator owns coverage, computation and report generation.

The model supplies physical bindings, collection uses and local answers. It does
not copy computed observations or decide aggregate counts and statuses.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Literal

from pydantic import Field, model_validator

from .bindings import AbsentCollection, Binding, BoundCollection, CollectionUse, FileCollection, UnresolvedCollection, binding_scope, scoped_records
from .document_tools import bind, ref
from .evidence import EvidenceBudget, EvidenceLimitError, cell_locator, resolve_optional
from .holes import EvidenceHole, TypedAnswer, validate_answer
from .models import (
    All,
    AnyOf,
    Artifact,
    AuditReport,
    Check,
    Citation,
    CollectionExists,
    ColumnExists,
    Diagnostic,
    DocumentProgram,
    ExcelRange,
    Expr,
    Judge,
    Not,
    PlannedRule,
    RecordsProgram,
    Related,
    RulePlan,
    ValueTest,
)
from .relation_tools import match_rows
from .values import predicate_result


class ProgramUse(Artifact):
    audit_id: str
    rule_id: str
    inputs: dict[str, CollectionUse] = Field(default_factory=dict, max_length=64)


class Judgment(Artifact):
    need_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    value: Literal["true", "false", "unknown"]
    reason: str = Field(min_length=1, max_length=4096)
    evidence: list[Citation] = Field(default_factory=list, max_length=64)
    diagnostic: Diagnostic | None = None

    @model_validator(mode="after")
    def explained_answer(self) -> Judgment:
        if not self.reason.strip() or self.value != "unknown" and self.diagnostic is not None:
            raise ValueError("A local answer needs a reason; only unknown answers carry a diagnostic")
        return self


class AuditTrace(Artifact):
    """Small execution certificate; old plans/traces are deliberately rejected."""

    bindings: list[Binding] = Field(default_factory=list, max_length=256)
    uses: list[ProgramUse] = Field(max_length=16384)
    answers: list[Judgment | TypedAnswer] = Field(default_factory=list, max_length=16384)


JudgmentNeed = EvidenceHole


class AuditEvaluation(Artifact):
    report: AuditReport
    observations: list[dict[str, Any]]
    needs: list[JudgmentNeed]
    evidence_gaps: list[tuple[str, str]]
    clarifications: list[dict[str, Any]] = Field(default_factory=list)
    readings: list[dict[str, Any]] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _ref(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if row is None else {"binding_id": row["binding_id"], "row": row["row_id"]}


def _and(values: list[bool | None]) -> bool | None:
    return False if False in values else None if None in values else True


def _or(values: list[bool | None]) -> bool | None:
    return True if True in values else None if None in values else False


def _fact(value: bool | None, reason: str, evidence: list[Citation] | None = None, diagnostic: Diagnostic | None = None) -> dict[str, Any]:
    return {"value": value, "reason": reason, "evidence": evidence or [], "diagnostic": diagnostic if value is None else None}


def _diagnostic(state: str) -> Diagnostic:
    if state == "unknown_visual_evidence":
        return "visual_only_evidence"
    if state in {"unknown_uncovered_grid", "unknown_geometry"}:
        return "unsupported_structure"
    return "parser_unknown" if state.startswith("unknown_") else "unresolved_relation"


def program_has_semantics(program: DocumentProgram | RecordsProgram) -> bool:
    def semantic(value: Any) -> bool:
        if isinstance(value, dict):
            return value.get("kind") == "judge" or value.get("key_match") in {"semantic", "evidence"} or value.get("mode") == "semantic" or any(semantic(item) for item in value.values())
        return isinstance(value, list) and any(semantic(item) for item in value)

    return semantic(program.model_dump(mode="json"))


class _Evaluator:
    def __init__(self, trace: AuditTrace, documents: dict[str, dict[str, Any]], plans: dict[str, RulePlan], budget: EvidenceBudget):
        self.trace, self.documents, self.plans, self.budget = trace, documents, plans, budget
        self.citation_budget = EvidenceBudget()
        self.bindings = {binding.id: binding for binding in trace.bindings}
        self.answers = {answer.need_id: answer for answer in trace.answers}
        self.uses = {(use.audit_id, use.rule_id): use for use in trace.uses}
        if len(self.bindings) != len(trace.bindings) or len(self.answers) != len(trace.answers) or len(self.uses) != len(trace.uses):
            raise ValueError("Duplicate execution identity")
        self.expected = {(audit_id, rule.id) for audit_id, plan in plans.items() for rule in plan.rules}
        if not self.uses.keys() <= self.expected:
            raise ValueError("Execution outside frozen target clauses")
        if not plans.keys() <= documents.keys():
            raise ValueError("Target outside frozen submission")
        self.materialized: dict[str, dict[str, Any]] = {}
        self.read_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
        for binding in trace.bindings:
            if binding.document_id not in documents:
                raise ValueError("Binding document outside frozen submission")
            binding_scope(binding, documents[binding.document_id], budget)
        self.readings: list[dict[str, Any]] = []
        self.questions: dict[str, JudgmentNeed] = {}
        self.clarifications: dict[str, dict[str, Any]] = {}
        self.reached_answers: set[str] = set()
        self.records: list[dict[str, Any]] = []
        self.evidence_gaps: set[tuple[str, str]] = set()
        self.collection_cache: dict[str, dict[str, Any]] = {}
        self.relation_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self.population: list[dict[str, Any]] = []
        self.current_use: ProgramUse | None = None
        self.current_rule: PlannedRule
        self.audit_id: str
        self.identity: dict[str, Any] = {}

    def required_fields(self, name: str) -> set[str]:
        """Read fields actually referenced by this rule, plus object identity."""
        needed: set[str] = set()

        def visit(node: Expr | None, current: str | None) -> None:
            if node is None:
                return
            if isinstance(node, ValueTest) and current == name:
                needed.add(node.field)
            elif isinstance(node, Judge) and current == name:
                needed.update(node.fields)
            elif isinstance(node, ColumnExists) and node.input == name:
                needed.add(node.field)
            elif isinstance(node, (All, AnyOf, Not)):
                for child in node.items if isinstance(node, (All, AnyOf)) else [node.item]:
                    visit(child, current)
            elif isinstance(node, Related):
                if current == name:
                    needed.update(node.source_key.fields)
                    needed.update(comparison.source_field for comparison in node.comparisons)
                if node.target == name:
                    needed.update(node.target_key.fields)
                    needed.update(comparison.target_field for comparison in node.comparisons)
                visit(node.target_when, node.target)

        program = self.current_rule.program
        source = program.source if isinstance(program, RecordsProgram) else None
        visit(program.precondition, None)
        visit(program.require, source)
        if isinstance(program, RecordsProgram):
            visit(program.when, source)
        for entity in self.plans[self.audit_id].ontology.entities:
            if entity.id == name:
                needed.update(entity.identity)
        return needed

    def collection(self, name: str) -> dict[str, Any]:
        if name in self.collection_cache:
            return self.collection_cache[name]
        use = self.current_use.inputs.get(name) if self.current_use else None
        rows, exclusions, issues = [], [], []
        if isinstance(use, BoundCollection):
            ranges: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
            seen: set[str] = set()
            for scope in use.scopes:
                binding = self.bindings.get(scope.binding_id)
                if binding is None or binding.id in seen:
                    raise ValueError("Unknown or duplicate collection binding")
                seen.add(binding.id)
                previous = ranges[binding.document_id, binding.table]
                if any(binding.first_row <= last and first <= binding.last_row for first, last in previous):
                    raise ValueError("Overlapping collection scopes")
                previous.append((binding.first_row, binding.last_row))
                concept = next((entity for entity in self.plans[self.audit_id].ontology.entities if entity.id == name), None)
                if (scope.objects or scope.object_choices) and concept is None:
                    raise ValueError("Logical objects require a rule-declared entity grain and identity")
                if concept and not set(concept.identity) <= (binding.columns.keys() | binding.field_holes.keys()):
                    raise ValueError("Logical identity attributes have not been bound")
                if concept and any(not set(item.fields) <= concept.attributes.keys() for item in scope.objects):
                    raise ValueError("Logical attributes outside the rule ontology")
                required = self.required_fields(name)
                available = (
                    binding.columns.keys()
                    | binding.field_holes.keys()
                    | {field for item in scope.objects for field in item.fields}
                    | {field for choice in scope.object_choices for item in choice.objects for field in item.fields}
                )
                selected_fields = required & available or {min(available)}
                active_binding = binding.model_copy(
                    update={
                        "columns": {field: column for field, column in binding.columns.items() if field in selected_fields},
                        "field_holes": {field: hole for field, hole in binding.field_holes.items() if field in selected_fields},
                    }
                )
                materialization_key = (binding.id, tuple(sorted(selected_fields)))
                cached = materialization_key in self.read_cache
                if not cached:
                    self.read_cache[materialization_key] = bind(self.documents[binding.document_id], active_binding.model_dump(mode="json"), budget=self.budget)
                self.materialized[binding.id] = self.read_cache[materialization_key]
                self.readings.append(
                    {
                        "audit_id": self.audit_id,
                        "rule_id": self.current_rule.id,
                        "binding_id": binding.id,
                        "fields": sorted(selected_fields),
                        "first_row": binding.first_row,
                        "last_row": binding.last_row,
                        "reused": cached,
                    }
                )
                selected_scope = scope
                unresolved_objects = False
                if scope.object_choices:
                    if concept and any(not set(item.fields) <= concept.attributes.keys() for choice in scope.object_choices for item in choice.objects):
                        raise ValueError("Logical candidate attributes outside rule ontology")
                    # Every offered candidate must itself cover real, authorized source rows.
                    candidates = {}
                    for choice in scope.object_choices:
                        candidate_scope = scope.model_copy(update={"objects": choice.objects, "object_choices": []})
                        candidates[choice.id] = candidate_scope
                        scoped_records(self.materialized[binding.id], binding, candidate_scope, self.documents[binding.document_id], self.budget)
                    selected, _answer = self.request_fact(
                        "object_scope",
                        "objects",
                        f"collection/{name}/{binding.id}",
                        "请选择符合规则对象粒度的来源组合",
                        {
                            "document_ids": [binding.document_id],
                            "object_choices": [choice.model_dump(mode="json") for choice in scope.object_choices],
                            "source_scope": {
                                "document_id": binding.document_id,
                                "table": binding.table,
                                "first_row": binding.first_row,
                                "last_row": binding.last_row,
                                "header_rows": binding.header_rows,
                            },
                        },
                    )
                    if selected is None:
                        unresolved_objects = True
                        issues.append("逻辑对象范围尚未确定")
                    else:
                        selected_scope = candidates[selected]
                active_scope = selected_scope.model_copy(
                    update={
                        "objects": [item.model_copy(update={"fields": {field: locators for field, locators in item.fields.items() if field in selected_fields}}) for item in selected_scope.objects]
                    }
                )
                selected, removed = scoped_records(self.materialized[binding.id], active_binding, active_scope, self.documents[binding.document_id], self.budget)
                if unresolved_objects:
                    selected = []
                for requirement in binding.interpretations:
                    if requirement.field not in selected_fields:
                        continue
                    if requirement.field not in binding.columns and requirement.field not in binding.field_holes:
                        raise ValueError("Interpretation concerns an undeclared field")
                    for citation in requirement.evidence:
                        if citation.document_id != binding.document_id or citation.locator is None:
                            raise ValueError("Interpretation evidence outside bound document")
                        if not resolve_optional(self.documents[citation.document_id], citation.locator, self.budget):
                            raise ValueError("Interpretation requires readable source evidence")
                    confirmed = any(
                        entry.get("question_id") == requirement.id
                        and entry.get("kind") in {"interpretation", "mapping"}
                        and self.confirmation_scope(entry.get("scope"), binding.document_id)
                        and any(entry.get("candidate_id") == candidate.id and entry.get("content") == candidate.explanation for candidate in requirement.candidates)
                        for entry in self.plans[self.audit_id].appendix
                    )
                    recommendation = None
                    if not confirmed:
                        recommendation, answer = self.request_fact(
                            "interpretation",
                            "interpretation",
                            f"interpretation/{name}/{binding.id}/{requirement.id}",
                            requirement.question,
                            {
                                "document_ids": [binding.document_id],
                                "candidate_ids": [candidate.id for candidate in requirement.candidates],
                                "requires_confirmation": requirement.requires_confirmation,
                                "allowed_evidence": [citation.model_dump(mode="json") for citation in requirement.evidence],
                            },
                        )
                        if recommendation is not None and answer is not None and not requirement.requires_confirmation:
                            confirmed = True
                            for record in selected:
                                record["interpretations"] = [
                                    *record.get("interpretations", []),
                                    {"id": requirement.id, "candidate_id": recommendation, "origin": answer.origin, "evidence": [citation.model_dump(mode="json") for citation in answer.evidence]},
                                ]
                    if not confirmed:
                        for record in selected:
                            record["states"] = {**record["states"], requirement.field: "unknown_interpretation"}
                        self._clarification(requirement, binding)
                        if recommendation is not None:
                            self.clarifications[requirement.id]["recommended_candidate_id"] = recommendation
                rows.extend(selected)
                exclusions.extend(removed)
            issues.extend(use.unresolved_scopes)
            state = "bound"
        elif isinstance(use, FileCollection):
            if len(set(use.document_ids)) != len(use.document_ids) or not set(use.document_ids) <= self.documents.keys():
                raise ValueError("File entity outside frozen input")
            for document_id in use.document_ids:
                source = self.documents[document_id]["source"]
                values = {"id": document_id, "sha256": source["sha256"], "format": source["format"]}
                rows.append({"binding_id": "file:" + document_id, "row_id": document_id, "values": values, "states": dict.fromkeys(values, "known"), "evidence": {}, "scope_state": "known"})
            state = "bound"
        elif isinstance(use, AbsentCollection):
            if len(set(use.searched_documents)) != len(use.searched_documents) or not set(use.searched_documents) <= self.documents.keys():
                raise ValueError("Absence search outside frozen submission")
            state = "absent"
        else:
            state = "unresolved"
            issues.append(use.reason if isinstance(use, UnresolvedCollection) else f"尚未绑定集合 {name}")
        result = {
            "state": state,
            "rows": rows,
            "exclusions": exclusions,
            "issues": issues,
            "unresolved_records": [_ref(row) for row in rows if row["scope_state"] != "known"],
            "use": use,
            "diagnostic": use.diagnostic if isinstance(use, UnresolvedCollection) else "unresolved_relation",
        }
        self.collection_cache[name] = result
        return result

    def request_fact(
        self, kind: Literal["object_scope", "relationship", "interpretation"], value_type: Literal["objects", "relationship", "interpretation"], path: str, question: str, context: dict[str, Any]
    ) -> tuple[Any, TypedAnswer | None]:
        self.budget.consume(1)
        need_id = _digest({"execution": self.identity, "path": path, "kind": kind, "context": context})
        need = EvidenceHole(
            id=need_id, input_id=_digest(self.identity), audit_id=self.audit_id, rule_id=self.current_rule.id, kind=kind, program_path=path, question=question, context=context, value_type=value_type
        )
        if need_id not in self.questions and len(self.questions) >= 16384:
            raise EvidenceLimitError("Local evidence hole limit exceeded")
        self.questions[need_id] = need
        answer = self.answers.get(need_id)
        if answer is None:
            return None, None
        if not isinstance(answer, TypedAnswer):
            raise ValueError("Typed evidence request requires a fact answer")
        self.reached_answers.add(need_id)
        return validate_answer(need, answer, self.documents, budget=self.budget), answer

    def confirmation_scope(self, scope: Any, interpreted_document_id: str) -> bool:
        if not isinstance(scope, dict) or not isinstance(scope.get("document_ids"), list) or not isinstance(scope.get("rule_ids"), list):
            return False
        if interpreted_document_id not in scope["document_ids"] or self.current_rule.id not in scope["rule_ids"]:
            return False
        # Legacy direct replay can explicitly broadcast a rule ID. New product
        # confirmations name the audited target separately from the source file.
        if "rule_targets" not in scope:
            return True
        targets = scope["rule_targets"]
        return isinstance(targets, list) and {"document_id": self.audit_id, "rule_id": self.current_rule.id} in targets

    def _clarification(self, requirement, binding: Binding) -> None:
        identity = _digest(
            {"plans": {key: plan.source_sha256 for key, plan in sorted(self.plans.items())}, "documents": {key: document["source"]["sha256"] for key, document in sorted(self.documents.items())}}
        )
        if requirement.id not in self.clarifications:
            self.clarifications[requirement.id] = {
                "id": requirement.id,
                "input_id": identity,
                "kind": "interpretation",
                "question": requirement.question,
                "document_ids": [binding.document_id],
                "rule_ids": [],
                "candidates": [candidate.model_dump(mode="json") for candidate in requirement.candidates],
                "sources": [citation.model_dump(mode="json") for citation in requirement.evidence],
                "scope": {"document_ids": [binding.document_id], "rule_ids": [], "rule_targets": []},
            }
        item = self.clarifications[requirement.id]
        if (
            item["question"] != requirement.question
            or item["candidates"] != [candidate.model_dump(mode="json") for candidate in requirement.candidates]
            or item["document_ids"] != [binding.document_id]
        ):
            raise ValueError("Shared interpretation identity has conflicting meaning")
        if self.current_rule.id not in item["rule_ids"]:
            item["rule_ids"].append(self.current_rule.id)
            item["scope"]["rule_ids"].append(self.current_rule.id)
        target = {"document_id": self.audit_id, "rule_id": self.current_rule.id}
        if target not in item["scope"]["rule_targets"]:
            item["scope"]["rule_targets"].append(target)

    def row_context(self, row: dict[str, Any] | None, fields: list[str] | None = None) -> dict[str, Any] | None:
        if row is None:
            return None
        fields = list(row["values"]) if fields is None else fields
        return {
            "record": _ref(row),
            "fields": {field: {"value": row["values"].get(field), "state": row["states"].get(field, "unresolved"), "evidence": row["evidence"].get(field)} for field in fields},
        }

    def citations(self, row: dict[str, Any] | None, fields: list[str]) -> list[Citation]:
        if row is None:
            return []
        return [Citation.model_validate(row["evidence"][field]["citation"]) for field in dict.fromkeys(fields) if field in row["evidence"]]

    def headers(self, binding: Binding) -> list[Citation]:
        document = self.documents[binding.document_id]
        table = next(item for item in document["tables"] if item["id"] == binding.table)
        if document["source"]["format"] in {".xlsx", ".xlsm", ".xls"}:
            return [
                Citation(document_id=binding.document_id, locator=ExcelRange(kind="xlsx_range", sheet=binding.table, start=ref(row, 1), end=ref(row, table["cols"]))) for row in binding.header_rows
            ]
        return [
            Citation(document_id=binding.document_id, locator=cell_locator(document["source"]["format"], binding.table, row, column))
            for row in binding.header_rows
            for column in dict.fromkeys((1, table["cols"]))
        ]

    def ask(self, path: str, question: str, context: dict[str, Any], kind: Literal["semantic_predicate", "semantic_key", "semantic_value"] = "semantic_predicate") -> dict[str, Any]:
        self.budget.consume(1)
        need_id = _digest({"execution": self.identity, "path": path, "kind": kind, "question": question, "context": context})
        need = JudgmentNeed(id=need_id, input_id=_digest(self.identity), audit_id=self.audit_id, rule_id=self.current_rule.id, kind=kind, program_path=path, question=question, context=context)
        if need_id not in self.questions and len(self.questions) >= 16384:
            raise EvidenceLimitError("Local judgment limit exceeded")
        self.questions[need_id] = need
        answer = self.answers.get(need_id)
        if answer is None:
            return _fact(None, question, diagnostic="unresolved_relation")
        self.reached_answers.add(need_id)
        if isinstance(answer, TypedAnswer):
            value = validate_answer(need, answer, self.documents, budget=self.budget)
            return _fact(value, answer.reason, answer.evidence, "unresolved_relation")
        if not answer.reason.strip():
            raise ValueError("Local answer lacks a reason")
        return _fact({"true": True, "false": False, "unknown": None}[answer.value], answer.reason, answer.evidence, answer.diagnostic or "unresolved_relation")

    def expr(self, node: Expr, row: dict[str, Any] | None, path: str) -> dict[str, Any]:
        fact = self._expr(node, row, path)
        if isinstance(node, (CollectionExists, ColumnExists)):
            inputs = [node.input]
        elif isinstance(node, Judge):
            inputs = node.inputs
        elif isinstance(node, Related):
            inputs = [node.target]
        else:
            inputs = []
        if not inputs:
            return fact
        # Keep scope observations even when a containing Boolean masks this
        # leaf's uncertainty. They do not add a separate global requirement.
        return {**fact, "input_scopes": {name: {key: collection[key] for key in ("state", "issues", "unresolved_records")} for name in inputs for collection in [self.collection(name)]}}

    def _expr(self, node: Expr, row: dict[str, Any] | None, path: str) -> dict[str, Any]:
        self.budget.consume(1)
        if isinstance(node, ValueTest):
            if row is None:
                return _fact(None, "字段检查尚无当前记录", diagnostic="unresolved_relation")
            state = row["states"].get(node.field, "unresolved")
            if state != "known":
                if state == "unknown_conflicting_values":
                    return _fact(None, f"对象属性 {node.field} 有相互冲突的原生值", self.citations(row, [node.field]), "unresolved_relation")
                if state == "unknown_interpretation":
                    return _fact(None, f"字段 {node.field} 的解释待确认", self.citations(row, [node.field]), "rule_ambiguity")
                entity = (
                    next((entity for entity in self.plans[self.audit_id].ontology.entities if entity.id == self.current_rule.program.source), None)
                    if isinstance(self.current_rule.program, RecordsProgram)
                    else None
                )
                value_type = entity.attributes.get(node.field) if entity else None
                if value_type:
                    fact_binding = self.bindings.get(row["binding_id"])
                    fact_context = {"source": self.row_context(row, [node.field]), "document_ids": [fact_binding.document_id] if fact_binding else list(self.documents), "field": node.field}
                    if fact_binding:
                        fact_context["source_scope"] = {"document_id": fact_binding.document_id, "table": fact_binding.table, "rows": row.get("source_rows", [row["row_id"]])}
                        if node.field in row["evidence"]:
                            observations = row.get("observations", {}).get(node.field, [])
                            fact_context["allowed_evidence"] = [observation["evidence"]["citation"] for observation in observations if observation.get("evidence")] or [
                                row["evidence"][node.field]["citation"]
                            ]
                    need_id = _digest({"execution": self.identity, "path": path, "context": fact_context, "kind": "attribute"})
                    need = EvidenceHole(
                        id=need_id,
                        input_id=_digest(self.identity),
                        audit_id=self.audit_id,
                        rule_id=self.current_rule.id,
                        kind="attribute",
                        program_path=path,
                        question=f"请提供有来源的字段 {node.field}",
                        context=fact_context,
                        value_type=value_type,
                    )
                    if need_id not in self.questions and len(self.questions) >= 16384:
                        raise EvidenceLimitError("Local evidence hole limit exceeded")
                    self.questions[need_id] = need
                    answer = self.answers.get(need_id)
                    if answer is not None:
                        if not isinstance(answer, TypedAnswer):
                            raise ValueError("Attribute request requires a typed fact answer")
                        self.reached_answers.add(need_id)
                        value = validate_answer(need, answer, self.documents, budget=self.budget)
                        if answer.state == "provided":
                            return {
                                **_fact(predicate_result(node, value), answer.reason, answer.evidence),
                                "inferred_fact": {"origin": answer.origin, "value": value, "native": self.row_context(row, [node.field])},
                            }
                return _fact(None, f"字段 {node.field} 未能读取或绑定", self.citations(row, [node.field]) if row else [], _diagnostic(state))
            value = predicate_result(node, row["values"][node.field])
            return _fact(
                value,
                f"字段 {node.field} 执行 {node.op}：{'满足' if value is True else '不满足' if value is False else '类型不支持'}",
                self.citations(row, [node.field]),
                "unsupported_structure" if value is None else None,
            )
        if isinstance(node, (CollectionExists, ColumnExists)):
            collection = self.collection(node.input)
            if collection["state"] == "unresolved" or collection["issues"]:
                return _fact(None, f"集合 {node.input} 范围尚未确定", diagnostic="unresolved_relation")
            if isinstance(node, CollectionExists):
                if collection["unresolved_records"]:
                    return _fact(None, f"集合 {node.input} 的记录成员资格尚未确定", diagnostic="unsupported_structure")
                return _fact(collection["state"] != "absent", f"集合 {node.input} {'已定位' if collection['state'] == 'bound' else '已搜索，未找到'}")
            use = collection["use"]
            if isinstance(use, AbsentCollection):
                return _fact(False, f"集合 {node.input} 已确认不存在")
            values = []
            evidence: list[Citation] = []
            if isinstance(use, FileCollection):
                return _fact(all(node.field in record["values"] for record in collection["rows"]), "文件实体属性存在性检查")
            for scope in use.scopes:
                binding = self.bindings[scope.binding_id]
                hole = binding.field_holes.get(node.field)
                values.append(True if node.field in binding.columns else False if hole and hole.state == "absent" else None)
                evidence.extend(self.headers(binding))
            value = _and(values)
            if value is True and collection["unresolved_records"]:
                return _fact(None, f"集合 {node.input} 的记录成员资格尚未确定", evidence, "unsupported_structure")
            return _fact(value, f"集合 {node.input} 的字段 {node.field} 存在性检查", evidence, "unresolved_relation")
        if isinstance(node, (All, AnyOf, Not)):
            children = [self.expr(child, row, f"{path}/{i}") for i, child in enumerate(node.items if isinstance(node, (All, AnyOf)) else [node.item])]
            values = [child["value"] for child in children]
            value = _and(values) if isinstance(node, All) else _or(values) if isinstance(node, AnyOf) else None if values[0] is None else not values[0]
            # Retain all component facts, including masked uncertainty, in the host trace.
            return {
                **_fact(
                    value,
                    "；".join(child["reason"] for child in children),
                    [cite for child in children for cite in child["evidence"]],
                    next((child["diagnostic"] for child in children if child["value"] is None), None),
                ),
                "components": children,
            }
        if isinstance(node, Judge):
            missing = next((row["states"].get(field, "unresolved") for field in node.fields if row["states"].get(field) != "known"), None) if row else None
            if missing is not None:
                return _fact(None, "局部判断所需的原始字段尚未确定", self.citations(row, node.fields), _diagnostic(missing))
            inputs = {}
            for name in node.inputs:
                collection = self.collection(name)
                if collection["state"] == "unresolved" or collection["issues"] or collection["unresolved_records"]:
                    return _fact(None, f"局部判断所需集合 {name} 尚未完整绑定", diagnostic="unresolved_relation")
                use = collection["use"]
                inputs[name] = {
                    "state": collection["state"],
                    "count": len(collection["rows"]),
                    "binding_ids": [scope.binding_id for scope in use.scopes] if isinstance(use, BoundCollection) else [],
                    "order": "declared scope order, then physical row",
                }
            context: dict[str, Any] = {
                "source": self.row_context(row, node.fields) if row else None,
                "requested_fields": node.fields if row is None else [],
                "inputs": inputs,
                "frozen_documents": {identity: document["source"]["sha256"] for identity, document in sorted(self.documents.items())},
            }
            if row is not None:
                adjacent_binding = self.bindings.get(row["binding_id"])
                adjacent = [item for item in self.materialized[adjacent_binding.id]["records"] if type(row["row_id"]) is int and abs(item["row_id"] - row["row_id"]) == 1] if adjacent_binding else []
                context["adjacent"] = [self.row_context({**item, "binding_id": adjacent_binding.id}, node.fields) for item in adjacent] if adjacent_binding else []
            return self.ask(path, node.question, context)
        if isinstance(node, Related):
            return self.relation(node, row, path)
        raise ValueError("Unsupported cached program node")

    def relation(self, node: Related, row: dict[str, Any] | None, path: str) -> dict[str, Any]:
        if row is None:
            return _fact(None, "关系检查缺少源记录", diagnostic="unresolved_relation")
        if path not in self.relation_cache:
            collection = self.collection(node.target)
            targets = []
            for target in collection["rows"]:
                membership = self.expr(node.target_when, target, path + "/target_when") if node.target_when else _fact(True, "全部目标记录")
                if membership["value"] is not False:
                    targets.append({**target, "scope_state": "known" if membership["value"] is True and target["scope_state"] == "known" else "unresolved"})

            def judge(kind, source, target, index, comparison):
                fields_a = node.source_key.fields if kind in {"key", "source_key"} else [comparison.source_field]
                fields_b = node.source_key.fields if kind == "source_key" else node.target_key.fields if kind == "key" else [comparison.target_field]
                question = "两条记录是否表示同一对象？" if kind in {"key", "source_key"} else "两个字段值是否满足规则要求的语义一致性？"
                if node.key_match == "evidence" and kind == "key":
                    source_ref = {"binding_id": source["binding_id"], "object_id": str(source["row_id"])}
                    target_ref = {"binding_id": target["binding_id"], "object_id": str(target["row_id"])}
                    value, _answer = self.request_fact(
                        "relationship",
                        "relationship",
                        path + f"/{kind}/{index}",
                        question,
                        {
                            "document_ids": sorted({self.bindings[record["binding_id"]].document_id for record in (source, target) if record["binding_id"] in self.bindings}) or sorted(self.documents),
                            "objects": [source_ref, target_ref],
                            "relation": {"source": source_ref, "target": target_ref},
                            "source": self.row_context(source, fields_a),
                            "target": self.row_context(target, fields_b),
                        },
                    )
                    return value.get("exists", True) if value is not None else None
                fact = self.ask(
                    path + f"/{kind}/{index}",
                    question,
                    {"source": self.row_context(source, fields_a), "target": self.row_context(target, fields_b)},
                    "semantic_value" if kind == "value" else "semantic_key",
                )
                return fact["value"]

            facts = match_rows(self.population, targets, node, self.budget, judge)
            by_ref = {}
            for fact in facts["records"]:
                value = True if fact["state"] == "matched" else False if fact["state"] in {"missing", "conflict"} else None
                incomplete = collection["state"] == "unresolved" or bool(collection["issues"])
                scope_unknown = value is True and (incomplete or bool(collection["unresolved_records"]))
                # An unseen ANY candidate can satisfy an observed mismatch;
                # a confirmed ALL/one-to-one conflict cannot be repaired by it.
                if value is False and (fact["state"] == "missing" or node.match == "any") and incomplete:
                    scope_unknown = True
                if scope_unknown:
                    value = None
                citations = []
                for observation in fact["source_values"].values():
                    if observation["evidence"]:
                        citations.append(Citation.model_validate(observation["evidence"]["citation"]))
                for candidate in fact["candidates"]:
                    for observation in candidate["target_key"].values():
                        if observation["evidence"]:
                            citations.append(Citation.model_validate(observation["evidence"]["citation"]))
                    for check in candidate["checks"]:
                        for side in ("source", "target"):
                            if check[side]["evidence"]:
                                citations.append(Citation.model_validate(check[side]["evidence"]["citation"]))
                reasons = {"matched": "对应记录满足要求", "missing": "未找到对应记录", "conflict": "对应关系或字段值冲突", "unresolved": "对应关系尚未确定"}
                reason = "关联记录范围或成员资格尚未完整确认" if scope_unknown else reasons[fact["state"]]
                by_ref[_digest(fact["source"])] = {**_fact(value, reason, citations, "unresolved_relation"), "relation": fact}
            self.relation_cache[path] = by_ref
        return self.relation_cache[path][_digest(_ref(row))]

    def record(self, role: str, row: dict[str, Any] | None, fact: dict[str, Any]) -> dict[str, Any]:
        logical = {}
        if row is not None and "source_rows" in row:
            logical = {"logical_object": {"id": row["row_id"], "source_rows": row["source_rows"], "attributes": row["observations"], "interpretations": row.get("interpretations", [])}}
        return {"audit_id": self.audit_id, "rule_id": self.current_rule.id, "role": role, "record": _ref(row), **logical, **fact}

    def check(self, audit_id: str, planned: PlannedRule) -> Check:
        self.audit_id, self.current_rule = audit_id, planned
        self.current_use = self.uses.get((audit_id, planned.id))
        self.collection_cache, self.relation_cache, self.population = {}, {}, []
        self.identity = {
            "audit_id": audit_id,
            "rule": planned.model_dump(mode="json"),
            "ontology": self.plans[audit_id].ontology.model_dump(mode="json"),
            "appendix": self.plans[audit_id].appendix,
            "rule_source_sha256": self.plans[audit_id].source_sha256,
            "plan_revision": self.plans[audit_id].plan_revision,
            "documents": {key: value["source"]["sha256"] for key, value in sorted(self.documents.items())},
            "use": self.current_use.model_dump(mode="json") if self.current_use else None,
            "bindings": [binding.model_dump(mode="json") for binding in sorted(self.trace.bindings, key=lambda item: item.id)],
        }
        program, records = planned.program, []
        if self.current_use is None:
            records.append(self.record("scope", None, _fact(None, "此条款尚未提交执行绑定", diagnostic="unresolved_relation")))
        else:
            if program.precondition is not None:
                records.append(self.record("prerequisite", None, self.expr(program.precondition, None, "precondition")))
            if isinstance(program, DocumentProgram):
                records.append(self.record("applicable", None, self.expr(program.require, None, "require")))
            else:
                source = self.collection(program.source)
                # Keep unknown populations distinct from a proved zero-trigger set.
                if source["state"] != "bound" and not source["issues"]:
                    records.append(self.record("scope", None, _fact(None, "尚未建立完整源记录范围", diagnostic="unresolved_relation")))
                for exclusion in source["exclusions"]:
                    binding = self.bindings[exclusion["binding_id"]]
                    records.append(
                        self.record(
                            "excluded",
                            {"binding_id": binding.id, "row_id": exclusion["row"]},
                            _fact(True, exclusion["reason"], [Citation(document_id=binding.document_id, locator=exclusion["evidence_locator"])]),
                        )
                    )
                membership = []
                self.population = source["rows"]
                for row in source["rows"]:
                    when = self.expr(program.when, row, "when") if program.when else _fact(True, "规则适用于此记录")
                    membership.append((row, when))
                self.population = [
                    {**row, "scope_state": "known" if when["value"] is True and row["scope_state"] == "known" else "unresolved"} for row, when in membership if when["value"] is not False
                ]
                self.relation_cache = {}  # require operates on the declared eligible source population.
                for row, when in membership:
                    if when["value"] is False and row["scope_state"] == "known":
                        records.append(self.record("excluded", row, when))
                    elif when["value"] is None or row["scope_state"] != "known":
                        records.append(
                            {
                                **self.record(
                                    "applicable",
                                    row,
                                    _fact(
                                        None,
                                        "记录成员资格或规则适用性尚未确定",
                                        when["evidence"] or [Citation.model_validate(source) for source in row.get("scope_evidence", [])] or self.citations(row, list(row["values"])),
                                        "unsupported_structure" if row["scope_state"] != "known" else when["diagnostic"] or "unresolved_relation",
                                    ),
                                ),
                                "applicability": when,
                            }
                        )
                    else:
                        fact = self.expr(program.require, row, "require")
                        records.append({**self.record("applicable", row, fact), "applicability": when})
            # Dependent scope uncertainty belongs to its leaf expression, so
            # Boolean logic can mask it. The quantified source population is
            # still independently required, even for EXISTS with a known hit.
            for name in self.current_use.inputs:
                self.collection(name)
            if isinstance(program, RecordsProgram):
                collection = self.collection(program.source)
                for issue in collection["issues"]:
                    records.append(self.record("scope", None, _fact(None, issue, diagnostic=collection["diagnostic"])))
        applicable = [record for record in records if record["role"] == "applicable"]
        prerequisites = [record for record in records if record["role"] == "prerequisite"]
        unknown = sum(record["value"] is None for record in records)
        known_failure = any(record["value"] is False for record in prerequisites)
        if isinstance(program, RecordsProgram) and program.quantifier == "exists":
            failed = known_failure or bool(applicable) and not any(record["value"] is True for record in applicable) and not unknown
        else:
            failed = known_failure or any(record["value"] is False for record in applicable)
        status = "NG" if failed else "UNABLE" if unknown else planned.empty_scope_status if not applicable else "PASS"
        ordered = sorted(records, key=lambda item: 0 if item["value"] is False else 1 if item["value"] is None else 2)
        evidence: list[Citation] = []
        seen: set[str] = set()
        for record in ordered:
            for citation in record["evidence"]:
                key = citation.model_dump_json()
                if key in seen:
                    continue
                seen.add(key)
                document = self.documents.get(citation.document_id)
                if document is None:
                    raise ValueError("Citation outside frozen submission")
                if resolve_optional(document, citation.locator, self.citation_budget) is None:
                    self.evidence_gaps.add((audit_id, planned.id))
                if len(evidence) < 64:
                    evidence.append(citation)
        if not evidence and status != "UNABLE":
            self.evidence_gaps.add((audit_id, planned.id))
        # Save complete mechanical observations separately from compact public reason/evidence.
        self.records.extend(records)
        decisive = [record for record in ordered if record["value"] is False] if status == "NG" else [record for record in ordered if record["value"] is None] if status == "UNABLE" else []
        reason = "；".join(dict.fromkeys(record["reason"] for record in decisive))[:4096]
        if status == "NG" and unknown:
            reason += f"；另有 {unknown} 项尚未确定，详见执行观察。"
        if not reason:
            reason = planned.empty_scope_basis if not applicable else "完整声明范围内的适用记录满足规则要求"
        diagnostic = next((record["diagnostic"] for record in ordered if record["value"] is None), None)
        if status == "UNABLE" and diagnostic is None:
            diagnostic = "rule_ambiguity"
        return Check(
            audit_id=audit_id,
            rule_id=planned.id,
            status=status,
            reason=reason,
            total=len(records),
            checked=len(records) - unknown,
            unknown=unknown,
            evidence=evidence,
            diagnostic=diagnostic if status == "UNABLE" else None,
        )

    def run(self) -> AuditEvaluation:
        checks = [self.check(audit_id, rule) for audit_id, plan in self.plans.items() for rule in plan.rules]
        if self.answers.keys() != self.reached_answers:
            raise ValueError("Unknown or stale local judgment answer")
        return AuditEvaluation(
            report=AuditReport(checks=checks),
            observations=self.records,
            needs=list(self.questions.values()),
            evidence_gaps=sorted(self.evidence_gaps),
            clarifications=list(self.clarifications.values()),
            readings=self.readings,
        )


def evaluate_audit(trace: AuditTrace, documents: dict[str, dict[str, Any]], plans_by_target: dict[str, RulePlan], *, budget: EvidenceBudget | None = None) -> AuditEvaluation:
    return _Evaluator(trace, documents, plans_by_target, budget or EvidenceBudget()).run()


def derive_report(trace: AuditTrace, documents: dict[str, dict[str, Any]], plans_by_target: dict[str, RulePlan], budget: EvidenceBudget | None = None) -> AuditReport:
    return evaluate_audit(trace, documents, plans_by_target, budget=budget).report


def validate_trace(trace: AuditTrace, report: AuditReport, documents: dict[str, dict[str, Any]], plans_by_target: dict[str, RulePlan], budget: EvidenceBudget | None = None) -> set[tuple[str, str]]:
    evaluated = evaluate_audit(trace, documents, plans_by_target, budget=budget)
    expected = {(check.audit_id, check.rule_id): check for check in evaluated.report.checks}
    actual = {(check.audit_id, check.rule_id): check for check in report.checks}
    if len(actual) != len(report.checks) or actual.keys() != expected.keys():
        raise ValueError("Report coverage differs from replay")
    for identity, check in actual.items():
        derived = expected[identity]
        for field in ("status", "total", "checked", "unknown", "diagnostic"):
            if getattr(check, field) != getattr(derived, field):
                raise ValueError("Report differs from deterministic replay")
    return set(evaluated.evidence_gaps)
