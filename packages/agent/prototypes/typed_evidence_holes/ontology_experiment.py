"""Run original ontology counterexamples through the real guest and local Grok.

The three scores concern concepts, source binding and final judgments separately.
The hand-written oracle stays outside all model-readable directories. Controlled
execution replaces only the model; --live uses grok-build, grok-4.7 and medium.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import traceback
from collections import Counter
from pathlib import Path
from string import Formatter
from typing import Any

from agent.execution import guest
from audit.bindings import BoundCollection, FileCollection, grouping_support
from audit.document_tools import ReadLimits, TableReader
from audit.evidence import EvidenceBudget
from audit.models import Related, RequireAll, RequireAny, RequireNot, RulePlan, ValueTest
from audit.protocol import ResultBundle, SubmissionSpec
from audit.trace import AuditTrace, evaluate_audit
from corpus_experiment import CorpusCase, Unit, encoded, native_rules, prepare_unit, preserve_code, save
from ontology_cases import Case, Table, cases, write_sources

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def prepare(case: Case, directory: Path, revision: str) -> SubmissionSpec:
    originals = write_sources(case, directory / "originals")
    documents = [CorpusCase(table.filename, originals[table.filename], "1", {}, case.key) for table in case.tables]
    unit = Unit(case.key, documents, [next(document for document in documents if document.file_name == case.target)])
    spec, _ = prepare_unit(unit, directory / "inputs", {"1": native_rules(originals["Requirements.docx"])}, revision, {})
    return spec


def related_nodes(node):
    if isinstance(node, Related):
        yield node
    if isinstance(node, (RequireAll, RequireAny, RequireNot)):
        for child in node.items if isinstance(node, (RequireAll, RequireAny)) else [node.item]:
            yield from related_nodes(child)


def _mandatory_relation_guards(node, allowed: set[str]) -> set[str] | None:
    """This oracle permits a relation and only entailed positive AND guards."""
    if isinstance(node, RequireNot) and isinstance(node.item, RequireNot):
        return _mandatory_relation_guards(node.item.item, allowed)
    if isinstance(node, Related):
        return set()
    if isinstance(node, RequireAll):
        fields = set()
        for child in node.items:
            guards = _mandatory_relation_guards(child, allowed)
            if guards is None:
                return None
            fields.update(guards)
        return fields
    if isinstance(node, ValueTest) and node.op == "not_empty" and node.field in allowed:
        return {node.field}
    return None


def _effective_plans(bundle: ResultBundle, spec: SubmissionSpec):
    from audit.refinement import effective_plans

    base = {target: bundle.plans[rule] for target, rule in spec.targets.items() if rule in bundle.plans}
    return effective_plans(bundle.trace.plan_refinements, bundle.documents, base)


def _collection(table: Table, case: Case, trace: AuditTrace, use, concept, source: str, documents, pending: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Compare declared identity and physical grouping to an independent oracle."""
    concept_errors, binding_errors = [], []
    if isinstance(use, FileCollection):
        if table.identity_columns != ("id",):
            return [source + ": expected record concept, got file concept"], []
        if concept.identity != ["id"]:
            concept_errors.append(source + ": file identity must use input ID")
        selected = set(use.document_ids)
        if case.identity(table.filename) not in selected:
            binding_errors.append(source + ": required file entity is missing")
        if not selected <= documents.keys():
            binding_errors.append(source + ": file entity outside current inputs")
        return concept_errors, binding_errors
    if not isinstance(use, BoundCollection):
        return [], [source + ": collection not bound"]
    bindings = {binding.id: binding for binding in trace.bindings}
    actual_groups: list[tuple[int, ...]] = []
    identity_columns: set[str] = set()
    for scope in use.scopes:
        binding = bindings[scope.binding_id]
        if binding.document_id != case.identity(table.filename) or binding.table != table.sheet:
            binding_errors.append(source + ": wrong source document or table")
            continue
        columns = _columns(trace, BoundCollection(kind="bound", scopes=[scope]), concept.identity)
        identity_columns.update(columns or ("?unbound",))
        if scope.object_choices:
            binding_errors.append(source + ": object choices require observed resolution")
        elif scope.objects:
            actual_groups.extend(tuple(sorted(item.rows)) for item in scope.objects)
            if any(grouping_support(item, binding, documents[binding.document_id], EvidenceBudget())["state"] != "supported" for item in scope.objects):
                binding_errors.append(source + ": object grouping lacks readable member evidence")
        else:
            excluded = {item.row for item in scope.excluded_rows}
            actual_groups.extend((row,) for row in range(binding.first_row, binding.last_row + 1) if row not in excluded)
    if table.identity_columns is not None and not _identity_columns_match(identity_columns, table.identity_columns, len(pending or ())):
        concept_errors.append(source + ": identity columns differ")
    if table.identity_cells and _locations(trace, use, concept.identity) != table.identity_cells:
        concept_errors.append(source + ": identity source cells differ")
    if Counter(actual_groups) != Counter(table.objects):
        binding_errors.append(source + ": object membership/count differs")
    if use.unresolved_scopes or any(scope.unresolved_rows for scope in use.scopes):
        binding_errors.append(source + ": source coverage unresolved")
    return concept_errors, binding_errors


def _identity_columns_match(columns, expected: tuple[str, ...], pending: int = 0) -> bool:
    observed = set(columns or ())
    if not pending:
        return observed == set(expected)
    known = observed - {"?unbound"}
    return "?unbound" in observed and known <= set(expected) and len(set(expected) - known) == pending


def _pending_identity(case: Case, table: Table, trace: AuditTrace, use, concept, clarifications, audit_id: str, actual: str) -> set[str]:
    """An expected meaning question may correctly leave the target unmapped."""
    if not case.clarification or case.status != "UNABLE" or actual != "UNABLE" or table.filename != case.target or not isinstance(use, BoundCollection):
        return set()
    bindings = {binding.id: binding for binding in trace.bindings}
    pending = set()
    for field in concept.identity:
        accepted = True
        for scope in use.scopes:
            binding = bindings[scope.binding_id]
            hole = binding.field_holes.get(field)
            if binding.document_id != case.identity(case.target) or field in binding.columns or any(field in item.fields for item in scope.objects) or hole is None or hole.state != "unresolved":
                accepted = False
                break
            questions = [question for question in binding.interpretations if question.field == field and question.requires_confirmation]
            grounded = False
            for question in questions:
                header_evidence = any(
                    citation.document_id == binding.document_id
                    and citation.locator is not None
                    and citation.locator.kind == "xlsx_cell"
                    and citation.locator.sheet == table.sheet
                    and "".join(char for char in citation.locator.cell if char.isalpha()) in (table.identity_columns or ())
                    and int("".join(char for char in citation.locator.cell if char.isdigit())) in binding.header_rows
                    for citation in question.evidence
                )
                if header_evidence and any(
                    row["id"] == question.id and binding.document_id in row["document_ids"] and {"document_id": audit_id, "rule_id": "1"} in row["scope"].get("rule_targets", [])
                    for row in clarifications
                ):
                    grounded = True
                    break
            if not grounded:
                accepted = False
                break
        if accepted:
            pending.add(field)
    return pending


def _columns(trace: AuditTrace, use, fields: list[str]) -> tuple[str, ...] | None:
    if isinstance(use, FileCollection):
        return tuple(fields)
    if not isinstance(use, BoundCollection):
        return None
    bindings = {binding.id: binding for binding in trace.bindings}
    mappings = set()
    for scope in use.scopes:
        binding = bindings[scope.binding_id]
        columns = []
        for field in fields:
            if scope.objects and all(field in item.fields for item in scope.objects):
                selected = {"".join(char for char in locator.cell if char.isalpha()) for item in scope.objects for locator in item.fields[field] if locator.kind == "xlsx_cell"}
                column = next(iter(selected)) if len(selected) == 1 else "?unbound"
            else:
                column = binding.columns.get(field, "?unbound")
            columns.append(column)
        mappings.add(tuple(columns))
    return next(iter(mappings)) if len(mappings) == 1 else None


def _locations(trace: AuditTrace, use, fields: list[str]) -> tuple[str, ...] | None:
    """Exact source points distinguish properties that share a physical column."""
    if not isinstance(use, BoundCollection):
        return None
    bindings = {binding.id: binding for binding in trace.bindings}
    result: list[str] = []
    for field in fields:
        points: list[str] = []
        for scope in use.scopes:
            binding = bindings[scope.binding_id]
            excluded = {item.row for item in scope.excluded_rows}
            groups = scope.objects
            if groups:
                for item in groups:
                    if field in item.fields:
                        points.extend(locator.cell for locator in item.fields[field] if locator.kind == "xlsx_cell")
                    elif field in binding.columns:
                        points.extend(binding.columns[field] + str(row) for row in item.rows)
                    else:
                        return None
            elif field in binding.columns:
                points.extend(binding.columns[field] + str(row) for row in range(binding.first_row, binding.last_row + 1) if row not in excluded)
            else:
                return None
        result.extend(dict.fromkeys(points))
    return tuple(result)


def _projection(trace: AuditTrace, use, key) -> str | None:
    columns = _columns(trace, use, key.fields)
    if columns is None:
        return None
    markers = {field: "<" + column + ">" for field, column in zip(key.fields, columns, strict=True)}
    if key.template is not None:
        return "".join(literal + (markers[field] if field else "") for literal, field, _spec, _conversion in Formatter().parse(key.template))
    if key.separator is not None:
        return key.separator.join(markers[field] for field in key.fields)
    return markers[key.fields[0]] if len(key.fields) == 1 else None


def score(case: Case, bundle: ResultBundle, spec: SubmissionSpec) -> dict[str, Any]:
    """Score outputs, never generate expected business facts from the evaluator."""
    output: dict[str, Any] = {layer: {"state": "unassessable", "errors": []} for layer in ("concept", "binding", "judgment")}
    target = next(iter(spec.targets))
    base = bundle.plans.get(spec.targets[target])
    checks = [check for check in bundle.report.checks if check.audit_id == target and check.rule_id == "1"]
    actual = checks[0].status if len(checks) == 1 else "ERR"
    output["judgment"] = {"state": "correct" if actual == case.status else "incorrect", "errors": [] if actual == case.status else ["final status differs"], "expected": case.status, "actual": actual}
    if base is None:
        return output
    plan = _effective_plans(bundle, spec)[target]
    evaluation = evaluate_audit(bundle.trace, bundle.documents, {target: base})
    uses = [use for use in bundle.trace.uses if use.audit_id == target and use.rule_id == "1"]
    program = plan.rules[0].program
    if not uses or not hasattr(program, "source"):
        output["concept"] = {"state": "incorrect", "errors": ["required record relation was not represented"]}
        return output
    relations = list(related_nodes(program.require))
    concept_errors, binding_errors = [], []
    if program.precondition is not None:
        concept_errors.append("undeclared precondition gate")
    if len(relations) != 1:
        concept_errors.append("expected one directed relation")
    else:
        relation = relations[0]
        declared = next((item for item in plan.ontology.relationships if item.id == relation.relationship_id), None)
        if declared is None or declared.kind not in case.relation_kinds:
            concept_errors.append("relation meaning differs")
        if program.quantifier != "forall" or relation.match != case.match or relation.key_match != "exact":
            concept_errors.append("quantifier or match policy differs")
        if program.when is not None or relation.target_when is not None:
            concept_errors.append("unstated applicability filter")
        source_use, target_use = uses[0].inputs.get(program.source), uses[0].inputs.get(relation.target)
        if relation.source_key.normalization != "exact" or relation.target_key.normalization != "exact":
            concept_errors.append("unstated key normalization")
        role_tables = (case.tables[-1], case.tables[0]) if case.key == "attachment-foreign-key" else case.tables
        required_guards = {comparison.source_field for comparison in relation.comparisons} if case.comparison_nonempty else set()
        allowed_guards = set() if case.key.startswith("literal-concatenation") else set(relation.source_key.fields) | required_guards
        guards = _mandatory_relation_guards(program.require, allowed_guards)
        if guards is None:
            concept_errors.append("required relation must be a positive mandatory AND obligation")
        elif not required_guards <= guards:
            concept_errors.append("explicit nonempty comparison obligation missing")
        concepts = {entity.id: entity for entity in plan.ontology.entities}
        pending_by_entity = {
            name: _pending_identity(case, table, bundle.trace, use, concepts[name], evaluation.clarifications, target, actual)
            for name, table, use in zip((program.source, relation.target), role_tables, (source_use, target_use), strict=True)
            if name in concepts
        }
        if any(pending_by_entity.values()):
            output["pending_identity_mappings"] = {name: sorted(fields) for name, fields in pending_by_entity.items() if fields}
        if case.comparison:
            point_comparison = all(table.comparison_cells for table in role_tables)
            expected = tuple(table.comparison_cells for table in role_tables) if point_comparison else tuple((dict(table.columns)["classification"],) for table in role_tables)
            locations = _locations if point_comparison else _columns
            actual_comparisons = [
                (locations(bundle.trace, source_use, [comparison.source_field]), locations(bundle.trace, target_use, [comparison.target_field]), comparison.mode, comparison.normalization)
                for comparison in relation.comparisons
            ]
            if actual_comparisons != [(expected[0], expected[1], "exact", "exact")]:
                concept_errors.append("required attribute comparison differs")
        elif relation.comparisons:
            concept_errors.append("required attribute comparison differs")
        if case.key.startswith("literal-concatenation"):
            if _projection(bundle.trace, source_use, relation.source_key) != "<A> / <B>" or _projection(bundle.trace, target_use, relation.target_key) != "<A>":
                concept_errors.append("literal projection differs")
        elif case.key == "attachment-foreign-key":
            if _projection(bundle.trace, source_use, relation.source_key) != "<B>" or _projection(bundle.trace, target_use, relation.target_key) != "<id>":
                concept_errors.append("attachment foreign key differs")
        else:
            # Compare the semantic component order, allowing model-chosen field
            # names and an equivalent common order on both sides.
            components = []
            for name, table, use, key in zip((program.source, relation.target), role_tables, (source_use, target_use), (relation.source_key, relation.target_key), strict=True):
                meanings = {column: field for field, column in table.columns}
                columns = _columns(bundle.trace, use, key.fields)
                if table.identity_columns is not None:
                    pending_keys = pending_by_entity.get(name, set()) & set(key.fields)
                    # Accepting an association must not relax the identifier
                    # contract that same_instance enforces through its schema.
                    if len(columns or ()) != len(table.identity_columns) or not _identity_columns_match(columns, table.identity_columns, len(pending_keys)):
                        concept_errors.append("explicit identifier key differs from required source columns")
                    if key.separator is not None or key.template is not None:
                        concept_errors.append("undeclared identifier projection")
                if table.identity_cells and not set(table.identity_cells) <= set(_locations(bundle.trace, use, key.fields) or ()):
                    concept_errors.append("explicit identifier key omits required source cells")
                components.append(
                    tuple(None if field in pending_by_entity.get(name, set()) and column == "?unbound" else meanings.get(column, "?unknown") for field, column in zip(key.fields, columns, strict=True))
                    if columns
                    else None
                )
            if (
                components[0] is None
                or components[1] is None
                or len(components[0]) != len(components[1])
                or any(left != right for left, right in zip(*components, strict=True) if left is not None and right is not None)
            ):
                concept_errors.append("identity component correspondence differs")
        for entity_id, table in zip((program.source, relation.target), role_tables, strict=True):
            if entity_id not in concepts:
                concept_errors.append("required entity missing")
                continue
            errors, sources = _collection(table, case, bundle.trace, uses[0].inputs.get(entity_id), concepts[entity_id], entity_id, bundle.documents, pending_by_entity.get(entity_id))
            concept_errors.extend(errors)
            binding_errors.extend(sources)
    if case.clarification and not evaluation.clarifications:
        concept_errors.append("unresolved meaning did not produce a clarification")
    output["concept"] = {"state": "incorrect" if concept_errors else "correct", "errors": concept_errors}
    output["binding"] = {"state": "incorrect" if binding_errors else "correct", "errors": binding_errors}
    output["clarifications"] = len(evaluation.clarifications)
    output["refinements"] = len(bundle.trace.plan_refinements)
    output["readings"] = evaluation.readings
    return output


class ControlledRunner:
    """A deterministic model boundary, supplying programs/bindings, not findings."""

    def __init__(self, case: Case, inputs: Path, output: Path):
        self.case, self.output = case, output
        self.spec = guest.Manifest.model_validate_json((inputs / "manifest.json").read_bytes()).spec
        self.input_tokens = self.output_tokens = self.tool_calls = self.completed_tool_calls = self.compactions = 0
        self.calls: list[str] = []
        self.plan = None
        self.file_relation = "文件实体" in case.rule
        self.literal_relation = "字面分隔符" in case.rule
        self.ambiguous = "未规定其含义" in case.rule
        self.strengthen_identity = "共同确定" in case.rule
        self.point_form = "字段名在A列、填写值在B列" in case.rule
        self.name_nonempty = "名称必须逐字一致并且非空" in case.rule

    def __enter__(self):
        self.output.mkdir()
        return self

    def __exit__(self, *_):
        return False

    @property
    def usage(self):
        return {"requests_started": len(self.calls), "completed_requests": len(self.calls), "input_tokens": None, "output_tokens": None, "uncertain": False, "in_flight": False, "source": "controlled"}

    def run(self, work, _prompt, schema, _deadline, *, reasoning_effort):
        assert reasoning_effort == "medium"
        self.calls.append(schema.__name__)
        if issubclass(schema, RulePlan):
            self.plan = self._plan()
            return schema.model_validate(self.plan.model_dump(mode="json"))
        assert schema is AuditTrace and self.plan is not None
        return self._trace(json.loads((work / "documents.json").read_text()))

    def _plan(self):
        source, target = (self.case.tables[-1], self.case.tables[0]) if self.case.key == "attachment-foreign-key" else self.case.tables
        file_entity = self.file_relation

        def entity(name, table, is_file=False):
            columns = dict(table.columns)
            identity = [field for field in ("part", "code", "label") if field in columns]
            return {
                "id": name,
                "grain": "one input file" if is_file else "one form across labeled source rows" if self.point_form else "one business object, preserving each independent source occurrence",
                "attributes": {"id": "string", "format": "string"} if is_file else dict.fromkeys(columns, "string"),
                "identity": ["id"] if is_file else identity,
            }

        left, right = entity("source", source), entity("target", target, file_entity)
        if self.strengthen_identity:
            left["identity"] = right["identity"] = ["code"]
        source_key = {"fields": list(left["identity"])}
        target_key = {"fields": list(right["identity"])}
        if self.literal_relation:
            source_key = {"fields": ["code", "name"], "separator": " / "}
            target_key = {"fields": ["label"]}
        if file_entity:
            source_key = {"fields": ["attachment"]}
            target_key = {"fields": ["id"]}
        ref = self.spec.rules[0]
        plan = RulePlan.model_validate(
            {
                "source_sha256": ref.sha256,
                "plan_revision": ref.plan_revision,
                "ontology": {
                    "entities": [left, right],
                    "relationships": [
                        {
                            "id": "corresponds",
                            "kind": "association" if file_entity or self.literal_relation else "same_instance",
                            "source": "source",
                            "target": "target",
                            "meaning": "Use the rule's explicitly stated relation",
                        }
                    ],
                },
                "rules": [
                    {
                        "id": "1",
                        "source_locator": ref.clauses[0]["source_locators"][0],
                        "program": {
                            "kind": "records",
                            "source": "source",
                            "quantifier": "forall",
                            "require": {
                                "kind": "related",
                                "relationship_id": "corresponds",
                                "target": "target",
                                "source_key": source_key,
                                "target_key": target_key,
                                "match": "any" if file_entity or self.literal_relation else "one_to_one",
                                "key_match": "exact",
                                "comparisons": [{"source_field": "name", "target_field": "name", "mode": "exact"}]
                                if self.point_form
                                else [{"source_field": "classification", "target_field": "classification", "mode": "exact"}]
                                if "分类必须一致" in self.case.rule
                                else [],
                            },
                        },
                        "empty_scope_status": "UNABLE",
                        "empty_scope_basis": "The rule does not establish a pass for no source objects.",
                    }
                ],
            }
        )

        if self.point_form and self.name_nonempty:
            program = plan.rules[0].program
            program.require = RequireAll(kind="all", items=[ValueTest(kind="value_test", field="name", op="not_empty"), program.require])
        return plan

    def _trace(self, documents):
        source, target = (self.case.tables[-1], self.case.tables[0]) if self.case.key == "attachment-foreign-key" else self.case.tables
        bindings, inputs = [], {}
        for name, table in (("source", source), ("target", target)):
            document_id = self.case.identity(table.filename)
            if self.case.key == "attachment-foreign-key" and name == "target":
                inputs[name] = {"kind": "files", "document_ids": [document_id]}
                continue
            observed_table = next(item for item in documents[document_id]["tables"] if item["id"] == table.sheet)
            binding = {
                "id": name,
                "document_id": document_id,
                "source_sha256": documents[document_id]["source"]["sha256"],
                "table": table.sheet,
                "header_rows": [1],
                "first_row": 2,
                "last_row": observed_table["rows"],
                "columns": {} if self.point_form else dict(table.columns),
                "mapping_basis": "Original header fields identify these columns.",
                "boundary_basis": "All source data rows are included.",
            }
            if self.ambiguous and name == "target":
                binding["interpretations"] = [
                    {
                        "id": "unit-meaning",
                        "field": "code",
                        "question": "本文件的作业单元编号是否表示工序编号？",
                        "candidates": [
                            {"id": "process", "label": "表示工序", "explanation": "本文件作业单元编号表示工序编号。"},
                            {"id": "equipment", "label": "表示设备", "explanation": "本文件作业单元编号表示设备编号。"},
                        ],
                        "evidence": [{"document_id": document_id, "locator": {"kind": "xlsx_cell", "sheet": table.sheet, "cell": "A1"}}],
                    }
                ]
            bindings.append(binding)
            groups = []
            reader = TableReader(observed_table, EvidenceBudget())
            for row in range(2, observed_table["rows"] + 1):
                if self.point_form and groups or "空工序编号行共同描述" in self.case.rule and name == "target" and reader.cell(row, 1)["value"] in (None, ""):
                    groups[-1].append(row)
                else:
                    groups.append([row])
            objects = [
                {
                    "id": f"{name}-{index}",
                    "rows": list(rows),
                    "basis": "Source rows follow the rule's stated object grouping; independent occurrences are retained.",
                    "evidence": [{"kind": "xlsx_cell", "sheet": table.sheet, "cell": "A" + str(row)} for row in rows],
                }
                for index, rows in enumerate(groups)
            ]
            if self.point_form:
                fields = {}
                for row in range(2, observed_table["rows"] + 1):
                    field = {"零件名称": "name", "零件编号": "code"}.get(reader.cell(row, 1)["value"])
                    if field:
                        fields.setdefault(field, []).append({"kind": "xlsx_cell", "sheet": table.sheet, "cell": "B" + str(row)})
                objects[0]["fields"] = fields
            inputs[name] = {"kind": "bound", "scopes": [{"binding_id": name, "objects": objects}]}
        raw = {"bindings": bindings, "uses": [{"audit_id": next(iter(self.spec.targets)), "rule_id": "1", "inputs": inputs}]}
        if self.strengthen_identity:
            from audit.refinement import plan_identity

            refined = self.plan.model_copy(deep=True)
            for entity in refined.ontology.entities:
                entity.identity = ["code", "product"]
            refined.rules[0].program.require.source_key.fields = ["code", "product"]
            refined.rules[0].program.require.target_key.fields = ["code", "product"]
            raw["plan_refinements"] = [
                {
                    "audit_id": next(iter(self.spec.targets)),
                    "base_plan_id": plan_identity(self.plan),
                    "plan": refined.model_dump(mode="json"),
                    "reason": "The original rule and both product columns show that operation number alone does not establish identity.",
                    "rule_evidence": [self.plan.rules[0].source_locator.model_dump(mode="json")],
                    "document_evidence": [{"document_id": self.case.identity(table.filename), "locator": {"kind": "xlsx_cell", "sheet": table.sheet, "cell": "A1"}} for table in self.case.tables],
                }
            ]
        return AuditTrace.model_validate(raw)


def run(selected: tuple[Case, ...], output: Path, runner_factory, *, seconds: int, live: bool) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    revision = preserve_code(output)
    # Include the actual loaded source fixtures/harness in the same code snapshot.
    for path in (Path(__file__), HERE / "ontology_cases.py"):
        (output / "code" / path.name).write_bytes(path.read_bytes())
    revision = hashlib.sha256(encoded([revision, *(hashlib.sha256(path.read_bytes()).hexdigest() for path in (Path(__file__), HERE / "ontology_cases.py"))])).hexdigest()
    report: dict[str, Any] = {
        "kind": "local_ontology_counterexamples",
        "live": live,
        "model": "grok-4.7",
        "reasoning_effort": "medium",
        "engine_revision": revision,
        "training_eligible": False,
        "human_gold": False,
        "selected": len(selected),
        "runs": [],
    }
    save(output / "report.json", report)
    for case in selected:
        directory = output / ("unit-" + hashlib.sha256(case.key.encode()).hexdigest()[:16])
        directory.mkdir()
        spec = prepare(case, directory, revision)
        source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (directory / "inputs").iterdir()}
        summary = {
            "case": case.key,
            "execution_id": str(spec.execution_id),
            "source_sha256": source_hashes,
            "completed": False,
            "training_eligible": False,
            "human_gold": False,
            "usage_source": "provider" if live else "controlled",
            "scores": {layer: {"state": "unassessable", "errors": ["execution has not produced a validated result"]} for layer in ("concept", "binding", "judgment")},
        }
        runner = runner_factory(case, directory / "inputs", directory / "runner")
        started = time.monotonic()
        try:
            with runner:
                bundle = guest.execute(directory / "inputs", directory / "work", runner, time.monotonic() + seconds, limits=ReadLimits(output_bytes=64 * 1024 * 1024))
            unchanged = all(hashlib.sha256((directory / "inputs" / name).read_bytes()).hexdigest() == value for name, value in source_hashes.items())
            if not unchanged:
                raise ValueError("original_source_changed")
            if runner.usage.get("uncertain") or runner.usage.get("in_flight"):
                raise ValueError("model_usage_unsettled")
            summary.update(completed=True, source_unchanged=True, scores=score(case, bundle, spec))
        except Exception as error:
            # Do not copy provider response bodies, secrets or hidden reasoning.
            summary["error"] = {
                "type": type(error).__name__,
                "frames": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno} for frame in traceback.extract_tb(error.__traceback__)[-8:]],
            }
            summary["scores"] = {layer: {"state": "unassessable", "errors": ["execution did not produce a validated result"]} for layer in ("concept", "binding", "judgment")}
        finally:
            summary.update(
                seconds=round(time.monotonic() - started, 3), usage=runner.usage, tool_calls=runner.tool_calls, completed_tool_calls=runner.completed_tool_calls, compactions=runner.compactions
            )
            summary["stages"] = runner.summary.get("stages", []) if live else list(runner.calls)
            summary["plan_repairs"] = len(list((directory / "work").glob("compile-repair-*")))
            summary["audit_repairs"] = len(list((directory / "work").glob("audit-repair")))
            save(directory / "summary.json", summary)
            report["runs"].append(summary)
            report["layers"] = {layer: dict(Counter(item["scores"][layer]["state"] for item in report["runs"])) for layer in ("concept", "binding", "judgment")}
            report["not_executed"] = len(selected) - len(report["runs"])
            usage_rows = [item["usage"] for item in report["runs"]]
            report["usage_total"] = {
                **{key: sum(item.get(key, 0) for item in usage_rows) for key in ("requests_started", "completed_requests")},
                **{key: sum(item[key] for item in usage_rows) if all(isinstance(item.get(key), int) for item in usage_rows) else None for key in ("input_tokens", "output_tokens")},
                "uncertain": any(item.get("uncertain", True) for item in usage_rows),
                "in_flight": any(item.get("in_flight", True) for item in usage_rows),
                "tool_calls": sum(item["tool_calls"] for item in report["runs"]),
                "completed_tool_calls": sum(item["completed_tool_calls"] for item in report["runs"]),
                "compactions": sum(item["compactions"] for item in report["runs"]),
                "plan_repairs": sum(item["plan_repairs"] for item in report["runs"]),
                "audit_repairs": sum(item["audit_repairs"] for item in report["runs"]),
                "upstream_retries": 0,
            }
            save(output / "report.json", report)
        print(
            json.dumps(
                {
                    "case": case.key,
                    "completed": summary["completed"],
                    "scores": {layer: value["state"] for layer, value in summary["scores"].items() if layer in {"concept", "binding", "judgment"}},
                    "usage": summary["usage"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if summary["usage"].get("uncertain") or summary["usage"].get("in_flight"):
            break
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", help="exact counterexample key; repeat to select more")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--settings-root", type=Path, default=ROOT)
    parser.add_argument("--grok", default=shutil.which("grok"))
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--max-requests", type=int, default=128)
    parser.add_argument("--input-token-budget", type=int, default=5_000_000)
    parser.add_argument("--output-token-budget", type=int, default=1_000_000)
    args = parser.parse_args()
    selected = tuple(case for case in cases() if not args.case or case.key in args.case)
    if not selected or args.case and set(args.case) - {case.key for case in selected}:
        parser.error("select existing counterexample keys")
    if args.output.exists() or min(args.seconds, args.max_requests, args.input_token_budget, args.output_token_budget) <= 0:
        parser.error("use a new output directory and positive limits")
    if args.live:
        if not args.grok:
            parser.error("grok executable required")
        from local_grok_runner import LocalGrokRunner
        from product.settings import Settings

        settings = Settings(_env_file=args.settings_root / ".env")  # type: ignore[call-arg]

        def factory(_case, inputs, output):
            return LocalGrokRunner(
                settings,
                output,
                args.grok,
                input_root=inputs,
                seconds=args.seconds,
                max_requests=args.max_requests,
                input_token_budget=args.input_token_budget,
                output_token_budget=args.output_token_budget,
            )
    else:
        factory = ControlledRunner
    report = run(selected, args.output.resolve(), factory, seconds=args.seconds, live=args.live)
    print(json.dumps({"report": str(args.output.resolve() / "report.json"), "layers": report["layers"], "not_executed": report["not_executed"]}, ensure_ascii=False))
    return int(report["not_executed"] > 0 or any(report["layers"][layer].get("correct", 0) != len(selected) for layer in ("concept", "binding", "judgment")))


if __name__ == "__main__":
    raise SystemExit(main())
