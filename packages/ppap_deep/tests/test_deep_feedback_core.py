"""Independent truth-table acceptance through actual parsers, guest and evaluator."""

import hashlib
import json
import time
from copy import deepcopy
from io import BytesIO

import pytest
from openpyxl import Workbook
from ppap_deep.auditor import semantic_inputs
from ppap_deep.protocol import ExecuteRequest, FrozenFile
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.guest import Manifest, execute
from ppap_deep.semantic.models import Ontology, RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit

EXPLANATION = "本控制计划中的作业单元编号表示工序编号；按原始编号逐字对应，不合并不同编号。"
QUESTION_ID = "cp-operation-code"
CANDIDATE_ID = "operation-code"
RULES = [
    {"id": "R1", "rule_to_check": "流程图中的每个工序编号必须在控制计划中按原始编号一一对应，否则不通过。"},
    {"id": "R2", "rule_to_check": "对应工序的分类必须在流程图和控制计划中一致，否则不通过。"},
    {"id": "R3", "rule_to_check": "流程图中的每个工序编号必须非空，否则不通过。"},
]


def source_bytes(kind):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = kind
    rows = [["工序编号", "分类"], ["010", "CC"], ["020", "SC"]] if kind == "PFD" else [["作业单元编号", "分类"], ["010", "SC"], [None, None], ["020", "SC"]]
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class FeedbackModel:
    input_tokens = 0
    output_tokens = 0

    def __init__(self):
        self.calls = []

    def run(self, work, prompt, schema, deadline, *, reasoning_effort):
        self.calls.append(work.name)
        assert reasoning_effort == "medium"
        if issubclass(schema, RulePlan):
            supplied = prompt.split("identity and ordered clause IDs must match exactly: ", 1)[1]
            identity, _ = json.JSONDecoder().raw_decode(supplied)
            clauses = json.loads((work / "clauses.json").read_text())
            assert [(clause["id"], clause["rule_to_check"]) for clause in clauses] == [(clause["id"], clause["rule_to_check"]) for clause in RULES]
            relation = {"kind": "related", "relationship_id": "corresponds", "target": "cp", "source_key": {"fields": ["code"]}, "target_key": {"fields": ["code"]}, "match": "one_to_one"}
            programs = [
                relation,
                {**relation, "comparisons": [{"source_field": "classification", "target_field": "classification", "mode": "exact"}]},
                {"kind": "value_test", "field": "code", "op": "not_empty"},
            ]
            return RulePlan.model_validate(
                {
                    "source_sha256": identity["source_sha256"],
                    "plan_revision": identity["plan_revision"],
                    "ontology": {
                        "entities": [
                            {"id": entity, "grain": "按明确工序编号标识的一个工序，可跨多行", "attributes": {"code": "string", "classification": "string"}, "identity": ["code"]}
                            for entity in ["pfd", "cp"]
                        ],
                        "relationships": [{"id": "corresponds", "kind": "same_instance", "source": "pfd", "target": "cp", "meaning": "源工序在目标文件中按原始编号一一对应"}],
                    },
                    "rules": [
                        {
                            "id": clause["id"],
                            "source_locator": {"kind": "published_clause", "clause_id": clause["id"]},
                            "program": {"kind": "records", "source": "pfd", "quantifier": "forall", "require": program},
                            "empty_scope_status": "UNABLE",
                            "empty_scope_basis": "原文未规定没有工序时的结论。",
                        }
                        for clause, program in zip(clauses, programs, strict=True)
                    ],
                }
            )
        assert schema is AuditTrace
        documents = json.loads((work / "documents.json").read_text())
        targets = json.loads((work / "targets.json").read_text())
        bindings, identities = [], {}
        for document_id, document in documents.items():
            table = document["tables"][0]
            kind = table["id"]
            assert kind in {"PFD", "CP"}
            name = kind.lower()
            identities[name] = document_id
            binding = {
                "id": name,
                "document_id": document_id,
                "source_sha256": document["source"]["sha256"],
                "table": kind,
                "header_rows": [1],
                "first_row": 2,
                "last_row": table["rows"],
                "columns": {"code": "A", "classification": "B"},
                "mapping_basis": "原件首行字段及明确工序身份",
                "boundary_basis": "示例表完整正文",
            }
            if kind == "CP":
                binding["interpretations"] = [
                    {
                        "id": QUESTION_ID,
                        "field": "code",
                        "question": "本控制计划的作业单元编号是否表示工序编号？",
                        "candidates": [{"id": CANDIDATE_ID, "label": "按工序编号理解", "explanation": EXPLANATION}],
                        "evidence": [{"document_id": document_id, "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "A1"}}],
                    }
                ]
            bindings.append(binding)
        pfd = {"kind": "bound", "scopes": [{"binding_id": "pfd", "objects": [{"id": "010", "rows": [2]}, {"id": "020", "rows": [3]}]}]}
        cp = {"kind": "bound", "scopes": [{"binding_id": "cp", "objects": [{"id": "010", "rows": [2, 3]}, {"id": "020", "rows": [4]}]}]}
        for collection, table in [(pfd, "PFD"), (cp, "CP")]:
            for item in collection["scopes"][0]["objects"]:
                item["basis"] = "原始工序编号及其后续空白行共同描述一个工序；不同编号分别保留。"
                item["evidence"] = [{"kind": "xlsx_cell", "sheet": table, "cell": f"A{row}"} for row in item["rows"]]
        uses = [{"audit_id": target, "rule_id": clause["id"], "inputs": {"pfd": pfd, **({"cp": cp} if clause["id"] != "R3" else {})}} for target in targets for clause in RULES]
        return AuditTrace.model_validate({"bindings": bindings, "uses": uses})

PFD = "00000000-0000-4000-8000-000000000010"
CP = "00000000-0000-4000-8000-000000000020"
RULE = "00000000-0000-4000-8000-000000000030"
ENGINE = "a" * 64


def run_case(path, appendix=None, *, previous=None, files=None, engine=ENGINE, limits=None, calls=None, rules=None, model=None):
    path.mkdir()
    inputs, work = path / "inputs", path / "work"
    inputs.mkdir()
    body = {"kind": "engine", "rules": RULES if rules is None else rules}
    if appendix is not None:
        body["appendix"] = appendix
    refs, names = [], {}
    for identity, kind in [(PFD, "PFD"), (CP, "CP")]:
        raw = files[identity] if files is not None else source_bytes(kind)
        (inputs / (kind + ".xlsx")).write_bytes(raw)
        names[identity] = kind + ".xlsx"
        refs.append(
            FrozenFile(
                file_version_id=identity,
                filename=kind + ".xlsx",
                sha256=hashlib.sha256(raw).hexdigest(),
                role="reference" if kind == "PFD" else "target",
                rule_version_id=RULE if kind == "CP" else None,
                rule_body=body if kind == "CP" else None,
            )
        )
    request = ExecuteRequest(protocol_revision="ppap-deep-v2", tenant_id="test-tenant", round_id="00000000-0000-4000-8000-000000000001", files=refs, executor="ppap-deep", executor_revision=engine)
    spec, packets = semantic_inputs(request, engine)
    for identity, raw in packets.items():
        (inputs / (identity + ".json")).write_bytes(raw)
    cached_snapshots, cached_plans = {}, {}
    if previous is not None:
        for identity, observation in previous.documents.items():
            filename = "cached-observation-" + identity + ".json"
            (inputs / filename).write_text(json.dumps(observation))
            cached_snapshots["document:" + identity] = filename
        for identity, plan in previous.plans.items():
            filename = "cached-plan-" + identity + ".json"
            (inputs / filename).write_text(plan.model_dump_json())
            cached_plans[identity] = filename
    manifest = Manifest(spec=spec, documents=names, rules={identity: identity + ".json" for identity in packets}, cached_snapshots=cached_snapshots, cached_plans=cached_plans)
    (inputs / "manifest.json").write_text(manifest.model_dump_json())
    model = model or FeedbackModel()
    result = execute(inputs, work, model, time.monotonic() + 60, **({"limits": limits} if limits else {}))
    if calls is not None:
        calls.extend(model.calls)
    evaluation = evaluate_audit(result.trace, result.documents, {target: result.plans[rule_id] for target, rule_id in spec.targets.items()})
    return result, evaluation


def confirmed(scope=None):
    return [
        {
            "id": "synthetic-confirmation",
            "kind": "interpretation",
            "question_id": QUESTION_ID,
            "candidate_id": CANDIDATE_ID,
            "content": EXPLANATION,
            "scope": scope or {"document_ids": [CP], "rule_ids": ["R1", "R2"]},
        }
    ]


def test_real_guest_confirmed_scope_removes_ambiguity_but_preserves_conflict(tmp_path):
    before, initial = run_case(tmp_path / "before")
    assert [check.status for check in initial.report.checks] == ["UNABLE", "UNABLE", "PASS"]
    assert len(initial.clarifications) == 1
    question = initial.clarifications[0]
    assert question["document_ids"] == [CP]
    assert question["rule_ids"] == ["R1", "R2"]
    assert question["sources"][0]["locator"]["cell"] == "A1"
    assert question["sources"][0]["excerpt"] == "作业单元编号"
    after, result = run_case(tmp_path / "after", confirmed())
    assert [check.status for check in result.report.checks] == ["PASS", "NG", "PASS"]
    assert not result.clarifications
    # Actual native field materialization follows the rule, not all binding columns.
    cp_readings = {reading["rule_id"]: reading["fields"] for reading in result.readings if reading["binding_id"] == "cp"}
    assert cp_readings == {"R1": ["code"], "R2": ["classification", "code"]}
    # Four physical CP rows become two logical process objects without erasing originals.
    assert [check.total for check in result.report.checks] == [2, 2, 2]
    assert after.documents[CP]["tables"][0]["cells"]["B2"]["value"] == "SC"
    assert after.documents[PFD]["tables"][0]["cells"]["B2"]["value"] == "CC"
    assert before.plans[RULE].source_sha256 != after.plans[RULE].source_sha256
    _, removed = run_case(tmp_path / "removed", [])
    assert [check.status for check in removed.report.checks] == ["UNABLE", "UNABLE", "PASS"]


@pytest.mark.parametrize("scope", [{"document_ids": [PFD], "rule_ids": ["R1", "R2"]}, {"document_ids": [CP], "rule_ids": ["not-R1"]}])
def test_confirmations_do_not_leak_outside_document_or_rule_scope(tmp_path, scope):
    _, result = run_case(tmp_path / "scoped", confirmed(scope))
    assert [check.status for check in result.report.checks] == ["UNABLE", "UNABLE", "PASS"]


def test_binding_cannot_drop_physical_rows_while_forming_objects(tmp_path):
    bundle, _ = run_case(tmp_path / "base", confirmed())
    raw = bundle.trace.model_dump(mode="json")
    for use in raw["uses"]:
        for scope in use["inputs"].get("cp", {}).get("scopes", []):
            scope["objects"][0]["rows"] = [2]
    with pytest.raises(ValueError, match="cover exactly"):
        evaluate_audit(AuditTrace.model_validate(raw), bundle.documents, {CP: bundle.plans[RULE]})


def test_cross_file_source_values_never_disappear_when_codes_change(tmp_path):
    bundle, _ = run_case(tmp_path / "base", confirmed())
    documents = deepcopy(bundle.documents)
    documents[CP]["tables"][0]["cells"]["A2"]["value"] = "0100"
    result = evaluate_audit(bundle.trace, documents, {CP: bundle.plans[RULE]})
    assert [check.status for check in result.report.checks] == ["NG", "NG", "PASS"]


@pytest.mark.parametrize("digest_matches,expected", [(True, "PASS"), (False, "NG")])
def test_file_relationship_and_new_release_manifest_rule_cross_the_real_guest(tmp_path, digest_matches, expected):
    """The model returns a plan and bindings; native SHA values decide the result."""
    reference = source_bytes("PFD")
    digest = hashlib.sha256(reference).hexdigest()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ReleaseManifest"
    sheet.append(["Attachment ID", "Declared SHA256"])
    sheet.append([PFD, digest if digest_matches else "0" * 64])
    data = BytesIO()
    workbook.save(data)
    workbook.close()
    clauses = [{"id": "N1", "rule_to_check": "每个发布清单条目的附件ID必须指向本次提交的文件，且声明的SHA256必须等于该文件的SHA256，否则不通过。"}]

    class ReleaseModel:
        input_tokens = output_tokens = 0

        def __init__(self):
            self.calls = []

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            self.calls.append(work.name)
            if issubclass(schema, RulePlan):
                identity, _ = json.JSONDecoder().raw_decode(prompt.split("identity and ordered clause IDs must match exactly: ", 1)[1])
                assert json.loads((work / "clauses.json").read_text()) == clauses
                assert not (work / "documents.json").exists()
                return schema.model_validate({
                    "source_sha256": identity["source_sha256"], "plan_revision": identity["plan_revision"],
                    "ontology": {
                        "entities": [
                            {"id": "entries", "grain": "one release manifest entry", "attributes": {"attachment_id": "string", "digest": "string"}, "identity": ["attachment_id"]},
                            {"id": "files", "grain": "one supplied file", "attributes": {"id": "string", "sha256": "string"}, "identity": ["id"]},
                        ],
                        "relationships": [{"id": "describes", "kind": "association", "source": "entries", "target": "files", "meaning": "entry refers to the supplied attachment by its original ID"}],
                    },
                    "rules": [{"id": "N1", "source_locator": {"kind": "published_clause", "clause_id": "N1"},
                               "program": {"kind": "records", "source": "entries", "quantifier": "forall", "require": {
                                   "kind": "related", "relationship_id": "describes", "target": "files", "match": "one_to_one",
                                   "source_key": {"fields": ["attachment_id"]}, "target_key": {"fields": ["id"]},
                                   "comparisons": [{"source_field": "digest", "target_field": "sha256", "mode": "exact"}],
                               }}, "empty_scope_status": "UNABLE", "empty_scope_basis": "The rule does not define an empty manifest result."}],
                })
            documents = json.loads((work / "documents.json").read_text())
            return schema.model_validate({
                "bindings": [{"id": "entries", "document_id": CP, "source_sha256": documents[CP]["source"]["sha256"],
                              "table": "ReleaseManifest", "header_rows": [1], "first_row": 2, "last_row": 2,
                              "columns": {"attachment_id": "A", "digest": "B"}, "mapping_basis": "Both original column headers", "boundary_basis": "Complete one-entry manifest"}],
                "uses": [{"audit_id": CP, "rule_id": "N1", "inputs": {
                    "entries": {"kind": "bound", "scopes": [{"binding_id": "entries"}]},
                    "files": {"kind": "files", "document_ids": list(documents)},
                }}],
            })

    model = ReleaseModel()
    bundle, result = run_case(tmp_path / "guest", files={PFD: reference, CP: data.getvalue()}, rules=clauses, model=model)
    assert model.calls == ["compile-0", "audit"]
    assert [(check.status, check.total) for check in result.report.checks] == [(expected, 1)]
    assert bundle.documents[PFD]["source"]["sha256"] == digest
    # An outside file cannot be added even if it would make the foreign key match.
    trace = bundle.trace.model_copy(deep=True)
    trace.uses[0].inputs["files"].document_ids.append("outside-file")
    with pytest.raises(ValueError, match="outside frozen input"):
        evaluate_audit(trace, bundle.documents, {CP: bundle.plans[RULE]})


def test_bounded_model_tool_responses_and_complete_relevant_coverage(tmp_path):
    """A controlled StageRunner exercises the same public tools as a real agent."""
    import subprocess
    import sys

    class ProbeModel(FeedbackModel):
        def __init__(self):
            super().__init__()
            self.responses = []
            self.probes = []

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            result = super().run(work, prompt, schema, deadline, reasoning_effort=reasoning_effort)
            if issubclass(schema, RulePlan):
                return result
            self.probes = [table["probe"] for doc in json.loads((work / "layout-probes.json").read_text()).values() for table in doc["tables"]]
            for binding in result.bindings:
                # The rules cover the two-row PFD body. Rows 4 onward are an
                # explicitly separate notes region, not additional process data.
                if binding.id == "pfd":
                    binding.last_row = 3
                    binding.boundary_basis = "Rows 2–3 are the complete process table; later rows belong to the notes region."
                command = [sys.executable, "-m", "ppap_deep.semantic.document_tools", "view", str(work / "documents.json"),
                           "--document", binding.document_id, "--table", binding.table, "--rows", f"1:{binding.last_row}", "--columns", "A:B"]
                response = subprocess.run(command, cwd=work, check=True, capture_output=True).stdout
                self.responses.append(response)
            (work / "selected-execution.json").write_text(result.model_dump_json())
            response = subprocess.run(
                [sys.executable, "-m", "ppap_deep.semantic.audit_tools", "evaluate", "selected-execution.json", "--out", "bounded-evaluation.json"],
                cwd=work, check=True, capture_output=True,
            ).stdout
            self.responses.append(response)
            return result

    metrics, bodies, probes = [], [], []
    for row_count in (100, 10000):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "PFD"
        for row in [["工序编号", "分类"], ["010", "CC"], ["020", "SC"]]:
            sheet.append(row)
        for row in range(13, row_count + 1):
            sheet.cell(row, 1, "unrelated-notes-" + str(row))
        raw = BytesIO()
        workbook.save(raw)
        workbook.close()
        model = ProbeModel()
        _, result = run_case(tmp_path / str(row_count), confirmed(), files={PFD: raw.getvalue(), CP: source_bytes("CP")}, model=model)
        assert [check.status for check in result.report.checks] == ["PASS", "NG", "PASS"]
        assert [check.total for check in result.report.checks] == [2, 2, 2]
        assert len(model.responses) == 3
        assert all(b"unrelated-notes-" not in response for response in model.responses)
        bodies.append([[json.loads(line) for line in response.splitlines()[1:]] for response in model.responses[:2]])
        probes.append(model.probes)
        coverage = [{key: reading[key] for key in ("rule_id", "binding_id", "fields", "first_row", "last_row")} for reading in result.readings]
        assert all((reading["first_row"], reading["last_row"]) == (2, 3 if reading["binding_id"] == "pfd" else 4) for reading in coverage)
        assert len(coverage) == 5
        metrics.append({"physical_rows": row_count, "tool_calls": len(model.responses), "response_bytes": [len(response) for response in model.responses], "program_readings": coverage})
    assert bodies[0] == bodies[1]
    assert probes[0] == probes[1]
    (tmp_path / "bounded-model-io.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))


def test_ontology_rejects_unknown_identity_attributes_and_relationship_endpoints():
    with pytest.raises(ValueError, match="Identity"):
        Ontology.model_validate({"entities": [{"id": "process", "grain": "process", "attributes": {"code": "string"}, "identity": ["missing"]}]})
    with pytest.raises(ValueError, match="endpoint"):
        Ontology.model_validate({"relationships": [{"id": "edge", "kind": "association", "source": "missing", "target": "missing", "meaning": "directed"}]})


@pytest.mark.parametrize("file_format", ["xlsx", "docx", "pdf"])
def test_multiple_objects_in_one_native_row_are_counted_by_declared_grain(tmp_path, file_format):
    import fitz
    from docx import Document
    from openpyxl import Workbook

    path = tmp_path / ("objects." + file_format)
    headers = ["CodeA", "ValueA", "CodeB", "ValueB"]
    values = ["010", "CC", "020", "SC"]
    if file_format == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Objects"
        sheet.append(headers)
        sheet.append(values)
        workbook.save(path)
        workbook.close()
        table_id = "Objects"
        locations = [{"kind": "xlsx_cell", "sheet": table_id, "cell": column + "2"} for column in "ABCD"]
    elif file_format == "docx":
        document = Document()
        table = document.add_table(rows=2, cols=4)
        for row, entries in enumerate((headers, values)):
            for column, entry in enumerate(entries):
                table.cell(row, column).text = entry
        document.save(path)
        table_id = "table:1"
        locations = [{"kind": "docx_cell", "table": 1, "row": 2, "column": column} for column in range(1, 5)]
    else:
        document = fitz.open()
        page = document.new_page(width=420, height=150)
        for x in (10, 110, 210, 310, 410):
            page.draw_line((x, 10), (x, 90))
        for y in (10, 50, 90):
            page.draw_line((10, y), (410, y))
        for row, entries in enumerate((headers, values)):
            for column, entry in enumerate(entries):
                page.insert_text((15 + column * 100, 30 + row * 40), entry)
        document.save(path)
        document.close()
        table_id = "page-1"
        locations = [{"kind": "pdf_cell", "page": 1, "row": 2, "column": column} for column in range(1, 5)]
    document = snapshot(path)
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rule",
            "plan_revision": "a" * 64,
            "ontology": {
                "entities": [{"id": "records", "grain": "one code object, multiple objects may share a physical row", "attributes": {"code": "string", "value": "string"}, "identity": ["code"]}]
            },
            "rules": [
                {
                    "id": "G1",
                    "source_locator": {"kind": "published_clause", "clause_id": "G1"},
                    "program": {"kind": "records", "source": "records", "quantifier": "forall", "require": {"kind": "value_test", "field": "value", "op": "not_empty"}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No records is unknown.",
                }
            ],
        }
    )
    scope = {
        "binding_id": "body",
        "objects": [
            {
                "id": "first",
                "rows": [2],
                "basis": "The first pair of source fields describes the first code object",
                "evidence": [locations[0], locations[1]],
                "fields": {"code": [locations[0]], "value": [locations[1]]},
            },
            {
                "id": "second",
                "rows": [2],
                "basis": "The second pair of source fields describes a distinct code object",
                "evidence": [locations[2], locations[3]],
                "fields": {"code": [locations[2]], "value": [locations[3]]},
            },
        ],
    }
    trace = AuditTrace.model_validate(
        {
            "bindings": [
                {
                    "id": "body",
                    "document_id": "file",
                    "source_sha256": document["source"]["sha256"],
                    "table": table_id,
                    "header_rows": [1],
                    "first_row": 2,
                    "last_row": 2,
                    "columns": {"code": "A", "value": "B"},
                    "mapping_basis": "The four source fields describe two distinct objects",
                    "boundary_basis": "The complete single physical row",
                }
            ],
            "uses": [{"audit_id": "file", "rule_id": "G1", "inputs": {"records": {"kind": "bound", "scopes": [scope]}}}],
        }
    )
    result = evaluate_audit(trace, {"file": document}, {"file": plan})
    assert (result.report.checks[0].status, result.report.checks[0].total) == ("PASS", 2)
    assert [observation["logical_object"]["attributes"]["code"][0]["value"] for observation in result.observations] == ["010", "020"]
    raw = trace.model_dump(mode="json")
    raw["uses"][0]["inputs"]["records"]["scopes"][0]["objects"][1]["fields"]["value"] = []
    with pytest.raises(ValueError, match="nonempty source"):
        AuditTrace.model_validate(raw)


def fact_answers(trace, evaluated, choose):
    raw = trace.model_dump(mode="json")
    raw["answers"] = []
    for need in evaluated.needs:
        if need.value_type == "boolean":
            raw["answers"].append({"need_id": need.id, "value": "false", "reason": "固定样本中不同源工序不是同一实例"})
        else:
            raw["answers"].append(
                {
                    "kind": "fact",
                    "need_id": need.id,
                    "input_id": need.input_id,
                    "origin": "model",
                    "value": choose(need),
                    "reason": "按固定样本的独立预期选择已校验的解释",
                    "evidence": [{"document_id": CP, "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "A1"}}],
                }
            )
    return AuditTrace.model_validate(raw)


def test_object_scope_typed_answer_rebuilds_business_objects_in_the_same_evaluator(tmp_path):
    bundle, _ = run_case(tmp_path / "base", confirmed())
    raw = bundle.trace.model_dump(mode="json")
    for use in raw["uses"]:
        for scope in use["inputs"].get("cp", {}).get("scopes", []):
            objects = scope.pop("objects")
            scope["object_choices"] = [
                {"id": "by-code", "label": "一工序可跨两行", "objects": objects},
                {
                    "id": "physical-rows",
                    "label": "每物理行一个对象",
                    "objects": [
                        {"id": str(row), "rows": [row], "basis": "This alternative treats each physical row as a record", "evidence": [{"kind": "xlsx_cell", "sheet": "CP", "cell": f"A{row}"}]}
                        for row in range(2, 5)
                    ],
                },
            ]
    trace = AuditTrace.model_validate(raw)
    initial = evaluate_audit(trace, bundle.documents, {CP: bundle.plans[RULE]})
    assert [check.status for check in initial.report.checks] == ["UNABLE", "UNABLE", "PASS"]
    assert {need.kind for need in initial.needs} == {"object_scope"}
    answered = fact_answers(trace, initial, lambda _need: "by-code")
    result = evaluate_audit(answered, bundle.documents, {CP: bundle.plans[RULE]})
    assert [check.status for check in result.report.checks] == ["PASS", "NG", "PASS"]
    assert [check.total for check in result.report.checks] == [2, 2, 2]
    invalid = answered.model_dump(mode="json")
    invalid["answers"][0]["value"] = "invented-object-group"
    with pytest.raises(ValueError, match="requested type"):
        evaluate_audit(AuditTrace.model_validate(invalid), bundle.documents, {CP: bundle.plans[RULE]})


def test_relationship_typed_answer_keeps_direction_and_host_compares_original_values(tmp_path):
    bundle, _ = run_case(tmp_path / "base", confirmed())
    # Independently specified rule: establish correspondence from cited evidence,
    # then compare the original classification; different code spellings are retained.
    raw_plan = bundle.plans[RULE].model_dump(mode="json")
    for rule in raw_plan["rules"][:2]:
        rule["program"]["require"]["key_match"] = "evidence"
    plan = RulePlan.model_validate(raw_plan)
    documents = deepcopy(bundle.documents)
    documents[CP]["tables"][0]["cells"]["A2"]["value"] = "STATION-A"
    initial = evaluate_audit(bundle.trace, documents, {CP: plan})
    assert any(need.kind == "relationship" for need in initial.needs)

    def choose(need):
        relation = need.context["relation"]
        return {**relation, "exists": relation["source"]["object_id"] == relation["target"]["object_id"]}

    answered = fact_answers(bundle.trace, initial, choose)
    result = evaluate_audit(answered, documents, {CP: plan})
    assert [check.status for check in result.report.checks] == ["PASS", "NG", "PASS"]
    assert documents[CP]["tables"][0]["cells"]["A2"]["value"] == "STATION-A"
    invalid = answered.model_dump(mode="json")
    edge = next(answer for answer in invalid["answers"] if answer.get("kind") == "fact")
    edge["value"]["source"], edge["value"]["target"] = edge["value"]["target"], edge["value"]["source"]
    with pytest.raises(ValueError, match="requested type or object scope"):
        evaluate_audit(AuditTrace.model_validate(invalid), documents, {CP: plan})


def test_interpretation_typed_answer_recommends_without_bypassing_user_confirmation(tmp_path):
    bundle, initial = run_case(tmp_path / "base")
    assert {need.kind for need in initial.needs} == {"interpretation"}
    answered = fact_answers(bundle.trace, initial, lambda _need: CANDIDATE_ID)
    result = evaluate_audit(answered, bundle.documents, {CP: bundle.plans[RULE]})
    assert [check.status for check in result.report.checks] == ["UNABLE", "UNABLE", "PASS"]
    assert result.clarifications[0]["recommended_candidate_id"] == CANDIDATE_ID
    # Ordinary, source-supported model interpretation uses the same fact interface.
    # Its provenance remains model-origin and never becomes a user appendix.
    raw = bundle.trace.model_dump(mode="json")
    for binding in raw["bindings"]:
        for interpretation in binding["interpretations"]:
            interpretation["requires_confirmation"] = False
    ordinary = AuditTrace.model_validate(raw)
    pending = evaluate_audit(ordinary, bundle.documents, {CP: bundle.plans[RULE]})
    answered = fact_answers(ordinary, pending, lambda _need: CANDIDATE_ID)
    result = evaluate_audit(answered, bundle.documents, {CP: bundle.plans[RULE]})
    assert [check.status for check in result.report.checks] == ["PASS", "NG", "PASS"]
    assert not result.clarifications
    assert bundle.plans[RULE].appendix == []


def test_boolean_local_judgments_also_accept_the_shared_typed_fact_contract(tmp_path):
    bundle, _ = run_case(tmp_path / "base", confirmed())
    raw_plan = bundle.plans[RULE].model_dump(mode="json")
    raw_plan["rules"][2]["program"]["require"] = {"kind": "judge", "question": "此对象符合独立构造的语义条件吗？", "fields": ["code"]}
    plan = RulePlan.model_validate(raw_plan)
    initial = evaluate_audit(bundle.trace, bundle.documents, {CP: plan})
    raw = bundle.trace.model_dump(mode="json")
    raw["answers"] = [
        {
            "kind": "fact",
            "need_id": need.id,
            "input_id": need.input_id,
            "value": need.context["source"]["record"]["row"] == "object:010",
            "origin": "model",
            "reason": "固定真值表：010满足，020不满足",
            "evidence": [{"document_id": PFD, "locator": {"kind": "xlsx_cell", "sheet": "PFD", "cell": "A2" if need.context["source"]["record"]["row"] == "object:010" else "A3"}}],
        }
        for need in initial.needs
    ]
    result = evaluate_audit(AuditTrace.model_validate(raw), bundle.documents, {CP: plan})
    assert [check.status for check in result.report.checks] == ["PASS", "NG", "NG"]


def test_cross_region_layout_changes_preserve_business_grain_values_and_conclusions(tmp_path):
    from openpyxl import Workbook

    observed = []
    for split, first_class in [(False, "CC"), (True, "CC"), (True, "SC")]:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Processes"
        sheet.append(["Code", "Class"])
        if split:
            for row in [("010", None), ("020", None), (None, first_class), (None, "SC")]:
                sheet.append(row)
            objects = [
                {
                    "id": code,
                    "rows": rows,
                    "fields": {"code": [{"kind": "xlsx_cell", "sheet": "Processes", "cell": code_cell}], "classification": [{"kind": "xlsx_cell", "sheet": "Processes", "cell": class_cell}]},
                }
                for code, rows, code_cell, class_cell in [("010", [2, 4], "A2", "B4"), ("020", [3, 5], "A3", "B5")]
            ]
        else:
            sheet.append(["010", first_class])
            sheet.append(["020", "SC"])
            objects = [{"id": "010", "rows": [2]}, {"id": "020", "rows": [3]}]
        for item in objects:
            item["basis"] = "The code and classification regions describe the same ordered process objects" if split else "Each labeled row describes a distinct process"
            item["evidence"] = [{"kind": "xlsx_cell", "sheet": "Processes", "cell": f"A{row}"} for row in item["rows"]]
        path = tmp_path / f"regions-{split}-{first_class}.xlsx"
        workbook.save(path)
        workbook.close()
        document = snapshot(path)
        plan = RulePlan.model_validate(
            {
                "source_sha256": "fixed-rule",
                "plan_revision": "a" * 64,
                "ontology": {
                    "entities": [
                        {
                            "id": "processes",
                            "grain": "one process identified by its original code; attributes may be in separated regions",
                            "attributes": {"code": "string", "classification": "string"},
                            "identity": ["code"],
                        }
                    ]
                },
                "rules": [
                    {
                        "id": "R1",
                        "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                        "program": {"kind": "records", "source": "processes", "quantifier": "forall", "require": {"kind": "value_test", "field": "classification", "op": "eq", "value": "SC"}},
                        "empty_scope_status": "UNABLE",
                        "empty_scope_basis": "No process is unknown.",
                    }
                ],
            }
        )
        trace = AuditTrace.model_validate(
            {
                "bindings": [
                    {
                        "id": "body",
                        "document_id": "file",
                        "source_sha256": document["source"]["sha256"],
                        "table": "Processes",
                        "header_rows": [1],
                        "first_row": 2,
                        "last_row": 5 if split else 3,
                        "columns": {"code": "A", "classification": "B"},
                        "mapping_basis": "Two explicitly identified process attributes",
                        "boundary_basis": "Complete process regions",
                    }
                ],
                "uses": [{"audit_id": "file", "rule_id": "R1", "inputs": {"processes": {"kind": "bound", "scopes": [{"binding_id": "body", "objects": objects}]}}}],
            }
        )
        before = deepcopy(document)
        result = evaluate_audit(trace, {"file": document}, {"file": plan})
        assert document == before
        observed.append((result.report.checks[0].status, result.report.checks[0].total))
        if split:
            assert result.observations[0]["logical_object"]["source_rows"] == [2, 4]
            assert [entry["evidence"]["citation"]["locator"]["cell"] for entry in result.observations[0]["logical_object"]["attributes"]["classification"] if entry["value"]] == ["B4"]
    assert observed == [("NG", 2), ("NG", 2), ("PASS", 2)]


@pytest.mark.parametrize("variant", ["identical", "appendix_removed", "content_changed", "scope_changed", "engine_changed", "missing_ontology", "old_revision"])
def test_real_guest_cached_plan_reuse_obeys_complete_input_identity(tmp_path, variant):
    files = {PFD: source_bytes("PFD"), CP: source_bytes("CP")}
    base, _ = run_case(tmp_path / "base", confirmed(), files=files)
    appendix, engine = confirmed(), ENGINE
    previous = base.model_copy(deep=True)
    if variant == "appendix_removed":
        appendix = []
    elif variant == "content_changed":
        appendix[0]["content"] = "本字段的含义仍需重新解释，不能沿用上次确认。"
    elif variant == "scope_changed":
        appendix[0]["scope"]["rule_ids"] = ["R1"]
    elif variant == "engine_changed":
        engine = "b" * 64
    elif variant == "missing_ontology":
        previous.plans[RULE].ontology.entities = []
        previous.plans[RULE].ontology.relationships = []
    elif variant == "old_revision":
        previous.plans[RULE].plan_revision = "c" * 64
    calls = []
    result, evaluation = run_case(tmp_path / "cached", appendix, files=files, previous=previous, engine=engine, calls=calls)
    assert [stage for stage in calls if stage.startswith("compile")] == ([] if variant == "identical" else ["compile-0"])
    assert result.plans[RULE].appendix == appendix
    expected = ["UNABLE", "UNABLE", "PASS"] if variant in {"appendix_removed", "content_changed"} else ["PASS", "UNABLE", "PASS"] if variant == "scope_changed" else ["PASS", "NG", "PASS"]
    assert [check.status for check in evaluation.report.checks] == expected


def test_native_snapshot_cache_cannot_bypass_different_actual_read_parameters(tmp_path):
    from ppap_deep.semantic.document_tools import ParserLimitError, ReadLimits

    files = {PFD: source_bytes("PFD"), CP: source_bytes("CP")}
    base, _ = run_case(tmp_path / "base", confirmed(), files=files)
    # A native observation may be reused for the same bytes and parser parameters.
    previous = base.model_copy(deep=True)
    previous.documents[PFD]["source"]["file"] = "prior-native-read.xlsx"
    cached, _ = run_case(tmp_path / "same-parameters", confirmed(), files=files, previous=previous)
    assert cached.documents[PFD]["source"]["file"] == "prior-native-read.xlsx"
    with pytest.raises(ParserLimitError, match="cell visits"):
        run_case(tmp_path / "tighter-parameters", confirmed(), files=files, previous=previous, limits=ReadLimits(cells=1))


def test_existing_native_observation_does_not_cache_logical_grain_or_old_fact_answers(tmp_path):
    bundle, initial = run_case(tmp_path / "base")
    answered = fact_answers(bundle.trace, initial, lambda _need: CANDIDATE_ID)
    current = evaluate_audit(answered, bundle.documents, {CP: bundle.plans[RULE]})
    assert current.clarifications[0]["recommended_candidate_id"] == CANDIDATE_ID
    changed_plan = bundle.plans[RULE].model_copy(deep=True)
    changed_plan.ontology.entities[1].grain = "a different explicit business grain"
    with pytest.raises(ValueError, match="stale"):
        evaluate_audit(answered, bundle.documents, {CP: changed_plan})
    changed_trace = bundle.trace.model_dump(mode="json")
    changed_trace["answers"] = answered.model_dump(mode="json")["answers"]
    changed_trace["bindings"][1]["columns"]["code"] = "B"
    with pytest.raises(ValueError, match="stale"):
        evaluate_audit(AuditTrace.model_validate(changed_trace), bundle.documents, {CP: bundle.plans[RULE]})
    before = deepcopy(bundle.documents)
    # Fresh logical construction is independent of prior answer/candidate state.
    fresh = evaluate_audit(bundle.trace, bundle.documents, {CP: changed_plan})
    assert [check.status for check in fresh.report.checks] == ["UNABLE", "UNABLE", "PASS"]
    assert bundle.documents == before


def test_new_compiler_schema_requires_ontology_while_pure_unit_ruleplan_remains_usable():
    from ppap_deep.semantic.models import CompiledRulePlan

    schema = CompiledRulePlan.model_json_schema()
    assert "ontology" in schema["required"]
    assert schema["$defs"]["CompiledOntology"]["properties"]["entities"]["minItems"] == 1
    raw = {
        "source_sha256": "rule",
        "plan_revision": "a" * 64,
        "rules": [
            {
                "id": "R1",
                "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                "program": {"kind": "document", "require": {"kind": "judge", "question": "A bounded legacy unit question"}},
                "empty_scope_status": "UNABLE",
                "empty_scope_basis": "Unknown without evidence",
            }
        ],
    }
    assert RulePlan.model_validate(raw).ontology.entities == []
    with pytest.raises(ValueError, match="ontology"):
        CompiledRulePlan.model_validate(raw)


def test_observation_parameters_are_a_value_not_mutable_parser_configuration(tmp_path):
    from ppap_deep.semantic.document_tools import ReadLimits

    path = tmp_path / "source.xlsx"
    path.write_bytes(source_bytes("PFD"))
    limits = ReadLimits(cells=100)
    observed = snapshot(path, limits=limits)
    observed["reader_parameters"]["cells"] = 1
    assert limits.cells == 100


def test_confirmation_scope_separates_same_rule_id_for_two_targets_reading_one_document(tmp_path):
    other_target = "00000000-0000-4000-8000-000000000040"
    bundle, _ = run_case(tmp_path / "base")
    documents = {**bundle.documents, other_target: deepcopy(bundle.documents[CP])}
    raw_trace = bundle.trace.model_dump(mode="json")
    raw_trace["uses"] += [{**deepcopy(use), "audit_id": other_target} for use in raw_trace["uses"]]
    trace = AuditTrace.model_validate(raw_trace)
    plan = bundle.plans[RULE]
    pending = evaluate_audit(trace, documents, {CP: plan, other_target: plan})
    assert [check.status for check in pending.report.checks] == ["UNABLE", "UNABLE", "PASS", "UNABLE", "UNABLE", "PASS"]
    assert pending.clarifications[0]["scope"]["document_ids"] == [CP]
    assert pending.clarifications[0]["scope"]["rule_targets"] == [
        {"document_id": CP, "rule_id": "R1"},
        {"document_id": CP, "rule_id": "R2"},
        {"document_id": other_target, "rule_id": "R1"},
        {"document_id": other_target, "rule_id": "R2"},
    ]
    scoped = plan.model_copy(deep=True)
    scoped.appendix = confirmed({"document_ids": [CP], "rule_ids": ["R1"], "rule_targets": [{"document_id": CP, "rule_id": "R1"}]})
    result = evaluate_audit(trace, documents, {CP: scoped, other_target: scoped})
    assert [check.status for check in result.report.checks] == ["PASS", "UNABLE", "PASS", "UNABLE", "UNABLE", "PASS"]
    assert result.clarifications[0]["scope"]["rule_targets"] == [
        {"document_id": CP, "rule_id": "R2"},
        {"document_id": other_target, "rule_id": "R1"},
        {"document_id": other_target, "rule_id": "R2"},
    ]
    # The interpreted document and the audited target are independent identities.
    scoped.appendix[0]["scope"]["rule_targets"] = [{"document_id": other_target, "rule_id": "R1"}]
    result = evaluate_audit(trace, documents, {CP: scoped, other_target: scoped})
    assert [check.status for check in result.report.checks] == ["UNABLE", "UNABLE", "PASS", "PASS", "UNABLE", "PASS"]
    # Older direct replay fixtures without exact target pairs keep explicit broadcast semantics.
    del scoped.appendix[0]["scope"]["rule_targets"]
    legacy = evaluate_audit(trace, documents, {CP: scoped, other_target: scoped})
    assert [check.status for check in legacy.report.checks] == ["PASS", "UNABLE", "PASS", "PASS", "UNABLE", "PASS"]


@pytest.mark.parametrize("rule_targets", [[], None, "R1", [{"document_id": CP, "rule_id": "different"}], [{"document_id": "outside-input", "rule_id": "R1"}]])
def test_present_but_unmatched_rule_target_scope_never_becomes_broadcast(tmp_path, rule_targets):
    scope = {"document_ids": [CP], "rule_ids": ["R1", "R2"], "rule_targets": rule_targets}
    _, result = run_case(tmp_path / "scoped", confirmed(scope))
    assert [check.status for check in result.report.checks] == ["UNABLE", "UNABLE", "PASS"]
