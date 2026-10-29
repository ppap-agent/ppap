"""The local guest experiment must retain original rules and the full denominator."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.evidence import resolve
from ppap_deep.semantic.guest import Manifest
from ppap_deep.semantic.guest_model import ModelProtocolError
from ppap_deep.semantic.models import RulePlan, WordCell
from ppap_deep.semantic.trace import AuditTrace


@pytest.fixture
def experiment(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    return importlib.import_module("corpus_experiment")


def original_rule(tmp_path):
    document = Document()
    document.add_paragraph("Original requirements; preserve this surrounding context.")
    table = document.add_table(rows=2, cols=3)
    for cell, text in zip(table.rows[0].cells, ["审核条款", "规则说明", "备注"], strict=True):
        cell.text = text
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "Value must be at least 3.\nKeep the original wording."
    table.cell(1, 2).text = "Supplementary source context."
    path = tmp_path / "original-rules.docx"
    document.save(path)
    return path


def original_case(experiment, tmp_path, name, value, expected, *, bundle=""):
    path = tmp_path / name
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Readings"
    sheet.append(["Value"])
    sheet.append([value])
    workbook.save(path)
    workbook.close()
    return experiment.CorpusCase(name, path, "1", {1: expected}, bundle)


class RecordedModel:
    """Replace only the external model; run the actual parser and validators."""

    def __init__(self, inputs, output, *, fail=False, uncertain=False):
        self.inputs, self.output = inputs, output
        self.spec = Manifest.model_validate_json((inputs / "manifest.json").read_bytes()).spec
        self.input_tokens = self.output_tokens = self.tool_calls = self.completed_tool_calls = self.compactions = 0
        self.compile_calls = self.audit_calls = 0
        self.fail, self.uncertain = fail, uncertain

    @property
    def usage(self):
        return {"requests": self.compile_calls + self.audit_calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "uncertain": self.uncertain, "in_flight": False}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, work, _prompt, schema, _deadline, *, reasoning_effort):
        self.input_tokens += 10
        self.output_tokens += 5
        if self.fail:
            raise ModelProtocolError("private provider body must not enter public error summaries")
        if issubclass(schema, RulePlan):
            self.compile_calls += 1
            source = json.loads((work / "rule-source.json").read_text())
            ref = next(rule for rule in self.spec.rules if rule.sha256 == source["source"]["sha256"])
            return RulePlan.model_validate(
                {
                    "source_sha256": ref.sha256,
                    "plan_revision": ref.plan_revision,
                    "ontology": {"entities": [{"id": "records", "grain": "one independently observed reading", "attributes": {"value": "number"}, "identity": ["value"]}]},
                    "rules": [
                        {
                            "id": "1",
                            "source_locator": {"kind": "docx_cell", "table": 1, "row": 2, "column": 2},
                            "program": {"kind": "records", "source": "records", "require": {"kind": "value_test", "field": "value", "op": "ge", "value": 3}, "quantifier": "forall"},
                            "empty_scope_status": "UNABLE",
                            "empty_scope_basis": "No conclusion is defined for an empty source.",
                        }
                    ],
                }
            )
        assert schema is AuditTrace
        self.audit_calls += 1
        documents = json.loads((work / "documents.json").read_text())
        targets = json.loads((work / "targets.json").read_text())
        return AuditTrace.model_validate(
            {
                "bindings": [
                    {
                        "id": target,
                        "document_id": target,
                        "source_sha256": documents[target]["source"]["sha256"],
                        "table": "Readings",
                        "header_rows": [1],
                        "first_row": 2,
                        "last_row": 2,
                        "columns": {"value": "A"},
                        "mapping_basis": "Value is in A1",
                        "boundary_basis": "Single body row",
                    }
                    for target in targets
                ],
                "uses": [{"audit_id": target, "rule_id": "1", "inputs": {"records": {"kind": "bound", "scopes": [{"binding_id": target}]}}} for target in targets],
            }
        )


def test_original_numbered_clauses_keep_verbatim_text_and_surrounding_document(experiment, tmp_path):
    path = original_rule(tmp_path)
    source = experiment.native_rules(path)
    assert source.content == path.read_bytes()
    assert source.clauses == [{"id": "1", "rule_to_check": "Value must be at least 3.\nKeep the original wording.", "source_locators": [{"kind": "docx_cell", "table": 1, "row": 2, "column": 2}]}]
    assert source.provenance["kind"] == "original_docx"
    assert source.provenance["sha256"] == experiment.digest(path.read_bytes())


def test_nested_rule_text_excludes_deleted_tables_without_renumbering(experiment, tmp_path):
    from docx.oxml import OxmlElement

    path = original_rule(tmp_path)
    document = Document(path)
    cell = document.tables[0].cell(1, 1)
    cell.text = ""
    deleted = cell.add_table(rows=1, cols=1)
    deleted.cell(0, 0).text = "DELETED RULE MUST NOT RETURN"
    marker = OxmlElement("w:del")
    deleted._tbl.addprevious(marker)
    marker.append(deleted._tbl)
    nested = cell.add_table(rows=1, cols=1).cell(0, 0)
    nested.text = "The active nested rule."
    nested.add_table(rows=1, cols=1).cell(0, 0).text = "The deeper condition."
    cell.add_table(rows=1, cols=1)  # An empty table contributes no source.
    document.save(path)
    source = experiment.native_rules(path)
    clause = source.clauses[0]
    assert clause["rule_to_check"] == "The active nested rule.\nThe deeper condition."
    assert clause["source_locators"] == [
        {"kind": "docx_cell", "table": 3, "row": 1, "column": 1},
        {"kind": "docx_cell", "table": 4, "row": 1, "column": 1},
    ]
    data = snapshot(path)
    assert [resolve(data, WordCell.model_validate(item)).values for item in clause["source_locators"]] == [("The active nested rule.",), ("The deeper condition.",)]


@pytest.mark.parametrize("sparse", [False, True])
def test_rule_body_after_merged_columns_uses_reader_coordinates(experiment, tmp_path, sparse):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = Document()
    table = document.add_table(rows=2, cols=5 if sparse else 4)
    offset = int(sparse)
    table.cell(0, offset).merge(table.cell(0, offset + 1)).text = "审核条款"
    table.cell(0, offset + 2).text = "规则说明"
    table.cell(1, offset).merge(table.cell(1, offset + 1)).text = "1"
    table.cell(1, offset + 2).text = "The rule body, not the ordinal."
    if sparse:
        for row in table.rows:
            physical = row._tr.findall(qn("w:tc"))
            row._tr.remove(physical[0])
            row._tr.remove(physical[-1])
            for name in ("gridBefore", "gridAfter"):
                omitted = OxmlElement(f"w:{name}")
                omitted.set(qn("w:val"), "1")
                row._tr.get_or_add_trPr().append(omitted)
    path = tmp_path / "merged-before-body.docx"
    document.save(path)

    clause = experiment.native_rules(path).clauses[0]
    assert clause["source_locators"] == [{"kind": "docx_cell", "table": 1, "row": 2, "column": 3}]
    data = snapshot(path)
    body = resolve(data, WordCell.model_validate(clause["source_locators"][0]))
    assert body.known and body.values == (clause["rule_to_check"],)
    assert resolve(data, WordCell(kind="docx_cell", table=1, row=2, column=2)).values == ("1",)


def test_vertical_merge_retains_first_anchor_and_rejects_unobserved_continuation(experiment, tmp_path):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    path = original_rule(tmp_path)
    document = Document(path)
    body = document.tables[0].cell(1, 1)
    body.text = ""
    nested = body.add_table(rows=2, cols=1)
    nested.cell(0, 0).merge(nested.cell(1, 0)).text = "Shared rule text."
    document.save(path)
    clause = experiment.native_rules(path).clauses[0]
    assert clause["source_locators"] == [{"kind": "docx_cell", "table": 2, "row": 1, "column": 1}]
    assert resolve(snapshot(path), WordCell.model_validate(clause["source_locators"][0])).values == ("Shared rule text.",)

    continuation = nested._tbl.findall(qn("w:tr"))[1].findall(qn("w:tc"))[0]
    paragraph, run, text = OxmlElement("w:p"), OxmlElement("w:r"), OxmlElement("w:t")
    text.text = "Different text hidden by the merged anchor."
    run.append(text)
    paragraph.append(run)
    continuation.append(paragraph)
    document.save(path)
    with pytest.raises(ValueError, match="Rule text is inside an unobserved Word cell"):
        experiment.native_rules(path)


def test_effective_native_rule_uses_the_compiler_snapshot_strike_view(experiment, tmp_path):
    path = original_rule(tmp_path)
    document = Document(path)
    cell = document.tables[0].cell(1, 1)
    cell.text = ""
    cell.paragraphs[0].add_run("Retired condition. ").font.strike = True
    cell.paragraphs[0].add_run("Active condition.")
    document.save(path)
    assert experiment.native_rules(path).clauses[0]["rule_to_check"] == "Active condition."


def test_canonical_scope_is_complete_and_rules_do_not_follow_filename_guesses(experiment):
    cases, rules, _ = experiment.load_corpus("v2", experiment.ROOT / "assets")
    assert (len(cases), sum(len(case.expected) for case in cases)) == (164, 840)
    unnumbered = next(case for case in cases if case.file_name.startswith("座舱 - 材料分供方清单"))
    assert unnumbered.doc_key == "3"
    dpvr = [case for case in cases if case.doc_key == "2-2"]
    assert len(dpvr) == 5
    assert sum(len(case.expected) for case in dpvr) == 30
    assert rules["2-2"].provenance["kind"] == "canonical_csv_rule_text"
    packet = json.loads(rules["2-2"].content)
    assert len(packet["clauses"]) == 6
    assert all(set(clause) == {"id", "rule_to_check"} for clause in packet["clauses"])
    assert "expected_status" not in rules["2-2"].content.decode()
    assert "manual_reason" not in rules["2-2"].content.decode()
    assert rules["2"].provenance["kind"] == "original_docx"
    assert len(rules["2"].clauses) == 7


def test_v3_selection_keeps_whole_bundle_as_context_and_full_scope(experiment):
    cases, _, _ = experiment.load_corpus("v3", experiment.ROOT / "assets")
    assert (len(cases), sum(len(case.expected) for case in cases), len({case.bundle for case in cases})) == (27, 220, 8)
    units, scoped = experiment.select_units(cases, doc_key="8", bundle_names=["Aries Spoiler"])
    assert len(units) == 1
    assert len(units[0].documents) == 3
    assert len(units[0].targets) == 1
    assert units[0].targets[0].doc_key == "8"
    assert len(scoped) == 7
    with pytest.raises(ValueError, match="unknown_or_duplicate"):
        experiment.select_units(cases, bundle_names=["not a canonical bundle"])


def test_real_guest_replays_sources_and_reuses_only_validated_rule_plans(experiment, tmp_path, monkeypatch):
    source = experiment.native_rules(original_rule(tmp_path))
    cases = [original_case(experiment, tmp_path, "first.xlsx", 3, "PASS"), original_case(experiment, tmp_path, "second.xlsx", 2, "NG")]
    units, scoped = experiment.select_units(cases)
    models = []

    def factory(inputs, output):
        model = RecordedModel(inputs, output)
        models.append(model)
        return model

    monkeypatch.setattr(experiment, "preserve_code", lambda _: "a" * 64)
    output = tmp_path / "run"
    run = experiment.run_experiment(cases, {"1": source}, {"corpus": "v2"}, units, scoped, output, factory)
    assert run["matched_rules"] == 2
    assert run["not_executed"] == {"files": 0, "rules": 0}
    assert [model.compile_calls for model in models] == [1, 0]
    assert [model.audit_calls for model in models] == [1, 1]
    assert all(item["source_unchanged"] and item["run_completed"] for item in run["runs"])
    for path in output.glob("unit-*/inputs/*"):
        if path.suffix == ".json":
            assert not any(secret in path.read_text() for secret in ["expected_status", "manual_reason", "oracle_path"])
    report = json.loads((output / "report.json").read_text())
    assert [row["actual_status"] for row in report["rule_results"]] == ["PASS", "NG"]
    assert report["run"]["training_eligible"] is False
    with pytest.raises(FileExistsError):
        experiment.run_experiment(cases, {"1": source}, {}, units, scoped, output, factory)


def test_failure_and_unselected_cases_stay_in_the_denominator(experiment, tmp_path, monkeypatch):
    source = experiment.native_rules(original_rule(tmp_path))
    cases = [original_case(experiment, tmp_path, "selected.xlsx", 3, "PASS"), original_case(experiment, tmp_path, "unselected.xlsx", 2, "NG")]
    units, scoped = experiment.select_units(cases, case_names=["selected.xlsx"])
    monkeypatch.setattr(experiment, "preserve_code", lambda _: "a" * 64)
    output = tmp_path / "run"
    run = experiment.run_experiment(cases, {"1": source}, {}, units, scoped, output, lambda inputs, output: RecordedModel(inputs, output, fail=True))
    assert run["full_scope"] == {"files": 2, "rules": 2}
    assert run["not_executed"] == {"files": 1, "rules": 1}
    report = json.loads((output / "report.json").read_text())
    assert [row["kind"] for row in report["rule_results"]] == ["execution_failure", "not_executed"]
    assert report["summary"]["total_rules"] == 2
    assert "private provider body" not in (output / "report.json").read_text()
    assert run["strict_full_scope_match_rate"] == 0


def test_unknown_usage_stops_the_next_unit_and_does_not_claim_a_completed_result(experiment, tmp_path, monkeypatch):
    source = experiment.native_rules(original_rule(tmp_path))
    cases = [original_case(experiment, tmp_path, "first.xlsx", 3, "PASS"), original_case(experiment, tmp_path, "next.xlsx", 3, "PASS")]
    units, scoped = experiment.select_units(cases)
    monkeypatch.setattr(experiment, "preserve_code", lambda _: "a" * 64)
    run = experiment.run_experiment(cases, {"1": source}, {}, units, scoped, tmp_path / "run", lambda inputs, output: RecordedModel(inputs, output, uncertain=True))
    assert len(run["runs"]) == 1
    assert run["runs"][0]["run_completed"] is False
    assert run["matched_rules"] == 0
    assert run["not_executed"] == {"files": 1, "rules": 1}


def test_cached_plan_with_different_code_revision_is_recompiled(experiment, tmp_path):
    source = experiment.native_rules(original_rule(tmp_path))
    case = original_case(experiment, tmp_path, "case.xlsx", 3, "PASS")
    units, _ = experiment.select_units([case])
    first = tmp_path / "first"
    spec, _ = experiment.prepare_unit(units[0], first / "inputs", {"1": source}, "a" * 64, {})
    model = RecordedModel(first / "inputs", first / "runner")
    bundle = experiment.guest.execute(first / "inputs", first / "work", model, float("inf"))
    second = tmp_path / "second"
    new_spec, _ = experiment.prepare_unit(units[0], second / "inputs", {"1": source}, "b" * 64, bundle.plans)
    assert new_spec.rules[0].plan_revision != spec.rules[0].plan_revision
    new_model = RecordedModel(second / "inputs", second / "runner")
    experiment.guest.execute(second / "inputs", second / "work", new_model, float("inf"))
    assert new_model.compile_calls == 1


def test_preserve_code_records_loaded_guide_after_disk_changes(experiment, tmp_path, monkeypatch):
    semantic = tmp_path / "semantic"
    semantic.mkdir()
    module = semantic / "guest.py"
    module.write_text("# Unchanged guest source\n")
    guide = semantic / "skill" / "references" / "rule-plan.md"
    guide.parent.mkdir(parents=True)
    monkeypatch.setattr(experiment.guest, "__file__", str(module))
    loaded = experiment.guest.COMPILATION_GUIDE.encode("utf-8")
    revisions = []
    for index, disk_content in enumerate(("Unloaded disk revision A", "Unloaded disk revision B", None)):
        if disk_content is None:
            guide.unlink()
        else:
            guide.write_text(disk_content, encoding="utf-8")
        output = tmp_path / f"run-{index}"
        output.mkdir()
        revisions.append(experiment.preserve_code(output))
        assert (output / "code/semantic/skill/references/rule-plan.md").read_bytes() == loaded
    assert len(set(revisions)) == 1

    monkeypatch.setattr(experiment.guest, "COMPILATION_GUIDE", "A newly loaded guide")
    output = tmp_path / "new-process"
    output.mkdir()
    assert experiment.preserve_code(output) != revisions[0]
    assert (output / "code/semantic/skill/references/rule-plan.md").read_text() == "A newly loaded guide"


def test_preserve_code_captures_parser_dependency_and_runtime(experiment, tmp_path, monkeypatch):
    parser = tmp_path / "ppap_docops"
    parser.mkdir()
    package = parser / "__init__.py"
    package.write_text("# Parser package\n")
    source = parser / "pdf" / "reader.py"
    source.parent.mkdir()
    monkeypatch.setattr(experiment.ppap_docops, "__file__", str(package))
    revisions = []
    for index, content in enumerate(("# First parser\n", "# Changed parser\n")):
        source.write_text(content)
        output = tmp_path / f"run-{index}"
        output.mkdir()
        revisions.append(experiment.preserve_code(output))
        assert (output / "code/ppap_docops/pdf/reader.py").read_text() == content
        assert (output / "code/uv.lock").read_bytes() == (experiment.ROOT / "uv.lock").read_bytes()
        runtime = json.loads((output / "code/runtime.json").read_text())
        assert runtime["python"] == experiment.sys.version
        assert runtime["distributions"]["PyMuPDF"] == experiment.importlib.metadata.version("PyMuPDF")
    assert revisions[0] != revisions[1]
