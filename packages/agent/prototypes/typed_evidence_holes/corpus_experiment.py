"""Run the real semantic guest locally and score the complete canonical corpus.

The local runner replaces only the process/model boundary. Original documents,
rule compilation, trace replay and bundle validation use the production guest.
Oracle statuses and comments remain outside every model-readable directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import platform
import shutil
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import documents
from agent.execution import gateway as model_gateway
from agent.execution import guest
from agent.execution.guest import Manifest
from audit.document_tools import ReadLimits, active_text
from audit.models import RulePlan, WordCell
from audit.protocol import DocumentRef, FileFormat, ResultBundle, RuleFormat, RuleRef, SubmissionSpec
from corpus_contracts.acceptance_report import write_acceptance_report
from corpus_contracts.live_poc import RULE_DOCS
from corpus_contracts.poc_oracle import load_poc_cases
from corpus_contracts.product_round import MATCH, compare_file
from corpus_contracts.v2_assets_shared import EXCEL_AGENT_MAP, PDF_AGENT_MAP, build_cases
from worker.intake.rule_sources import rule_sources

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
POLICY_REVISION = hashlib.sha256(b"local-original-rule-corpus-v1").hexdigest()


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def save(path: Path, value: Any) -> None:
    path.write_bytes(encoded(value))


@dataclass(frozen=True)
class CorpusCase:
    file_name: str
    file_path: Path
    doc_key: str
    expected: dict[int, str]
    bundle: str = ""

    @property
    def identity(self) -> str:
        return digest((self.bundle + "\0" + self.file_name).encode())


@dataclass(frozen=True)
class RuleSource:
    filename: str
    content: bytes
    clauses: list[dict[str, Any]]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class Unit:
    name: str
    documents: list[CorpusCase]
    targets: list[CorpusCase]


def native_rules(path: Path) -> RuleSource:
    """Read effective numbered rule text; keep the original DOCX bytes as source."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table

    content = path.read_bytes()
    document = Document(BytesIO(content))
    physical_tables = list(document.element.body.iter(qn("w:tbl")))
    # Match the snapshot reader's coordinates, including aliases of merged
    # cells. Physical w:tc indices are not necessarily these grid columns.
    locators_by_cell: dict[Any, dict[str, Any]] = {}
    for table_number, element in enumerate(physical_tables, 1):
        if any(parent.tag == qn("w:del") for parent in element.iterancestors()):
            continue
        parent = next((parent for parent in element.iterancestors() if parent.tag == qn("w:tc")), None)
        if parent is not None and parent not in locators_by_cell:
            raise ValueError("Nested rule table is inside an unobserved Word cell")
        for row_number, row in enumerate(Table(element, document._body).rows, 1):
            for column_number, cell in enumerate(row.cells, 1):
                locators_by_cell.setdefault(cell._tc, WordCell(kind="docx_cell", table=table_number, row=row_number, column=column_number).model_dump())
    locators_by_unit = {}
    tables: dict[str, dict[int, dict[int, dict[str, str]]]] = {}
    for source in rule_sources(path.name, content):
        parts = source["id"].split(":")
        if len(parts) == 4 and parts[1] != "p":
            cell = physical_tables[int(parts[1]) - 1].findall(qn("w:tr"))[int(parts[2]) - 1].findall(qn("w:tc"))[int(parts[3]) - 1]
            # Use the compiler snapshot's effective strike/deletion view.
            source = {**source, "excerpt": active_text(cell, skip_tables=True)}
            if not source["excerpt"]:
                continue
            if cell not in locators_by_cell:
                raise ValueError("Rule text is inside an unobserved Word cell")
            locators_by_unit[source["id"]] = locators_by_cell[cell]
            tables.setdefault(parts[1], {}).setdefault(int(parts[2]), {})[int(parts[3])] = source
    clauses = []
    for table_id, rows in tables.items():
        first = min(rows)
        headers = rows[first]
        ids = [column for column, source in headers.items() if source["excerpt"] in {"审核条款", "条款编号", "规则编号"}]
        bodies = [column for column, source in headers.items() if source["excerpt"] in {"规则说明", "规则描述", "审核要求"}]
        if not ids and not bodies:
            continue  # Examples and supplementary tables remain in the original.
        if len(ids) != 1 or len(bodies) != 1:
            raise ValueError(f"ambiguous_original_rule_table: {path.name} table {table_id}")
        for row, cells in sorted(rows.items()):
            if row == first:
                continue
            identity = cells.get(ids[0], {}).get("excerpt", "")
            body_source = cells.get(bodies[0])
            source_ids = [body_source["id"]] if body_source else []
            physical_row = physical_tables[int(table_id) - 1].findall(qn("w:tr"))[row - 1]
            physical_cell = physical_row.findall(qn("w:tc"))[bodies[0] - 1]
            nested = [table for table in physical_cell.iter(qn("w:tbl")) if not any(parent.tag == qn("w:del") for parent in table.iterancestors())]
            if nested:
                # Rule bodies may contain an actual nested table. Resolve it
                # through the original XML parent relation, never a nearby row.
                text = active_text(physical_cell)
                source_ids = [
                    item["id"]
                    for nested_table in nested
                    for nested_rows in [tables.get(str(physical_tables.index(nested_table) + 1), {})]
                    for nested_cells in nested_rows.values()
                    for item in nested_cells.values()
                ]
                if body_source:
                    source_ids.insert(0, body_source["id"])
                body_source = {"id": source_ids[0], "excerpt": text} if text and source_ids else None
            if not identity.isdecimal() or not body_source or int(identity) < 1:
                raise ValueError(f"incomplete_original_rule_table: {path.name} table {table_id} row {row}")
            clauses.append({"id": str(int(identity)), "rule_to_check": body_source["excerpt"], "source_locators": [locators_by_unit[source_id] for source_id in source_ids]})
    if not clauses or len({clause["id"] for clause in clauses}) != len(clauses):
        raise ValueError(f"missing_or_duplicate_original_rule_identity: {path.name}")
    return RuleSource(path.name, content, clauses, {"kind": "original_docx", "path": str(path.resolve()), "sha256": digest(content), "text_view": "deep_active_text"})


def _v2_key(agent_cls: type) -> str:
    keys = {str(key) for mapping in (EXCEL_AGENT_MAP, PDF_AGENT_MAP) for key, cls in mapping.items() if cls is agent_cls}
    keys = {"2" if key == "2-1" else key for key in keys}
    if len(keys) != 1:
        raise ValueError("ambiguous_canonical_document_type")
    return keys.pop()


def load_corpus(corpus: str, assets: Path) -> tuple[list[CorpusCase], dict[str, RuleSource], dict[str, Any]]:
    """Reuse canonical file mapping; derive model requirements without labels."""
    rules: dict[str, RuleSource] = {}
    if corpus == "v2":
        oracle = assets / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
        source_cases = build_cases(dataset_root=assets / "v2.0plus", expected_csv=oracle, include_unlabeled=False)
        cases = [CorpusCase(case.file_name, case.file_path, _v2_key(case.agent_cls), {index: item.status for index, item in case.expected.items()}) for case in source_cases]
        for key in {case.doc_key for case in cases} - {"2-2"}:
            paths = sorted((assets / "V2.0测试/规则V2.0").glob(f"{int(key):02d} *.docx"))
            if len(paths) != 1:
                raise ValueError("missing_or_ambiguous_original_rule_document")
            rules[key] = native_rules(paths[0])
        # These five trial reports have a different six-clause contract from
        # the seven-clause DVP&R plan DOCX. Only the rule-text CSV column is used.
        descriptions = [{index: item.rule_desc for index, item in case.expected.items()} for case in source_cases if _v2_key(case.agent_cls) == "2-2"]
        if descriptions:
            if any(items != descriptions[0] for items in descriptions) or any(not text.strip() for text in descriptions[0].values()):
                raise ValueError("conflicting_corpus_rule_text")
            clauses = [{"id": str(index), "rule_to_check": text} for index, text in sorted(descriptions[0].items())]
            version = "csv-rule-" + digest(encoded(clauses))
            content = encoded({"kind": "published_rule", "rule_version_id": version, "rule_body": {"rules": clauses}, "clauses": clauses})
            rules["2-2"] = RuleSource(
                "dpvr-corpus-rules.json",
                content,
                clauses,
                {
                    "kind": "canonical_csv_rule_text",
                    "path": str(oracle.resolve()),
                    "sha256": digest(oracle.read_bytes()),
                    "column": "规则",
                    "row_identities": [{"file_name": case.file_name, "rule_indices": sorted(case.expected)} for case in cases if case.doc_key == "2-2"],
                    "generated_source_sha256": digest(content),
                },
            )
    elif corpus == "v3":
        oracle = assets / "poc测试/poc-测试数据统计表.xlsx"
        source_poc = load_poc_cases(dataset_root=assets / "poc测试/POC测试样本", expected_xlsx=oracle)
        cases = [CorpusCase(case.file_name, case.file_path, case.file_seq, {index: item.status for index, item in case.expected.items()}, case.bundle_id) for case in source_poc]
        rules = {key: native_rules(assets / "poc测试/poc规则" / filename) for key, filename in RULE_DOCS.items()}
    else:
        raise ValueError("unknown_corpus")
    if len({case.identity for case in cases}) != len(cases):
        raise ValueError("duplicate_canonical_case")
    return cases, rules, {"corpus": corpus, "oracle_path": str(oracle.resolve()), "oracle_sha256": digest(oracle.read_bytes())}


def select_units(cases: list[CorpusCase], *, doc_key: str | None = None, case_names: list[str] | None = None, bundle_names: list[str] | None = None) -> tuple[list[Unit], list[CorpusCase]]:
    scoped = [case for case in cases if doc_key is None or case.doc_key == doc_key]
    if not scoped:
        raise ValueError("empty_document_scope")
    if case_names and bundle_names:
        raise ValueError("choose_cases_or_bundles")
    if case_names and (len(set(case_names)) != len(case_names) or set(case_names) - {case.file_name for case in scoped}):
        raise ValueError("unknown_or_duplicate_case_selection")
    if bundle_names and (len(set(bundle_names)) != len(bundle_names) or set(bundle_names) - {case.bundle for case in scoped if case.bundle}):
        raise ValueError("unknown_or_duplicate_bundle_selection")
    targets = [case for case in scoped if (not case_names or case.file_name in case_names) and (not bundle_names or case.bundle in bundle_names)]
    by_unit: dict[str, list[CorpusCase]] = defaultdict(list)
    for case in targets:
        by_unit[case.bundle or case.file_name].append(case)
    units = []
    for name, selected in by_unit.items():
        documents = [case for case in cases if case.bundle == selected[0].bundle] if selected[0].bundle else selected
        units.append(Unit(name, documents, selected))
    return units, scoped


def prepare_unit(unit: Unit, inputs: Path, sources: dict[str, RuleSource], engine_revision: str, cache: dict[str, RulePlan]) -> tuple[SubmissionSpec, dict[str, Any]]:
    inputs.mkdir(parents=True, exist_ok=False)
    documents: list[DocumentRef] = []
    document_paths: dict[str, str] = {}
    targets: dict[str, str] = {}
    refs: dict[str, RuleRef] = {}
    rule_paths: dict[str, str] = {}
    cached: dict[str, str] = {}
    provenance: dict[str, Any] = {}
    target_ids = {case.identity for case in unit.targets}
    for index, case in enumerate(unit.documents):
        data = case.file_path.read_bytes()
        filename = f"document-{index}{case.file_path.suffix.lower()}"
        (inputs / filename).write_bytes(data)
        document_paths[case.identity] = filename
        documents.append(
            DocumentRef(
                audit_id=case.identity,
                filename=case.file_name,
                sha256=digest(data),
                format=cast(FileFormat, case.file_path.suffix.lower()),
                role="target" if case.identity in target_ids else "context",
            )
        )
        if case.identity not in target_ids:
            continue
        source = sources[case.doc_key]
        source_hash = digest(source.content)
        rule_id = "rule-" + source_hash
        targets[case.identity] = rule_id
        if rule_id in refs:
            continue
        rule_filename = f"rule-{len(refs)}{Path(source.filename).suffix}"
        (inputs / rule_filename).write_bytes(source.content)
        rule_paths[rule_id] = rule_filename
        version = json.loads(source.content)["rule_version_id"] if source.filename.endswith(".json") else None
        refs[rule_id] = RuleRef(
            rule_id=rule_id,
            sha256=source_hash,
            format=cast(RuleFormat, Path(source.filename).suffix),
            rule_version_id=version,
            plan_revision=digest(encoded([source_hash, POLICY_REVISION, engine_revision])),
            clauses=source.clauses,
        )
        provenance[rule_id] = source.provenance
        if rule_id in cache:
            filename = f"cached-plan-{len(cached)}.json"
            (inputs / filename).write_text(cache[rule_id].model_dump_json())
            cached[rule_id] = filename  # guest.compile_plans checks SHA, revision and source.
    spec = SubmissionSpec(
        execution_id=UUID(int=0),
        submission_id=uuid5(NAMESPACE_URL, unit.name),
        project_id="local-corpus-research",
        documents=documents,
        rules=list(refs.values()),
        targets=targets,
        policy_revision=POLICY_REVISION,
        semantic_engine_revision=engine_revision,
    )
    spec = spec.model_copy(update={"execution_id": spec.expected_execution_id()})
    manifest = Manifest(spec=spec, documents=document_paths, rules=rule_paths, cached_snapshots={}, cached_plans=cached)
    (inputs / "manifest.json").write_text(manifest.model_dump_json())
    for path in inputs.iterdir():
        path.chmod(0o400)
    return spec, provenance


def bundle_result(bundle: ResultBundle, spec: SubmissionSpec) -> dict[str, Any]:
    checks = {(check.audit_id, check.rule_id): check for check in bundle.report.checks}
    rules = {rule.rule_id: rule for rule in spec.rules}
    items = []
    for target, rule_id in spec.targets.items():
        for clause in rules[rule_id].clauses:
            failed = rule_id in bundle.failed_rules
            check = None if failed else checks.get((target, clause["id"]))
            if failed:
                status = "ERR"
            elif check is not None:
                status = check.status
            else:
                continue
            items.append({"file_version_id": target, "id": clause["id"], "status": status, "coverage": "execution_error" if failed else "executed"})
    return {"coverage": {"rule_items": items}}


def comparison_rows(cases: list[CorpusCase], outcomes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        outcome = outcomes.get(case.identity)
        if outcome is None:
            verdicts = [{"rule_index": index, "expected_status": status, "actual_status": "", "kind": "not_executed"} for index, status in sorted(case.expected.items())]
        elif outcome.get("failure"):
            verdicts = [{"rule_index": index, "expected_status": status, "actual_status": "ERR", "kind": outcome["failure"]} for index, status in sorted(case.expected.items())]
        else:
            verdicts = [
                {"rule_index": item.rule_index, "expected_status": item.expected, "actual_status": item.actual or "", "kind": item.kind}
                for item in compare_file(file_name=case.file_name, version_id=case.identity, expected=case.expected, result=outcome["result"], setup_error=None, expected_coverage="executed")
            ]
        rows.extend({"bundle": case.bundle, "file_seq": case.doc_key, "file_name": case.file_name, **item} for item in verdicts)
    return rows


def preserve_code(output: Path) -> str:
    semantic = Path(guest.__file__).parent
    docops = Path(documents.__file__).parent
    code = output / "code"
    code.mkdir()
    fingerprints = {}
    guide = semantic / "skill" / "references" / "rule-plan.md"
    paths = [path for path in semantic.rglob("*") if path != guide and path.is_file() and path.suffix in {".py", ".md"}]
    paths.append(guide)
    paths += [HERE / name for name in ("corpus_experiment.py", "local_grok_runner.py", "grok_support.py", "hole_experiment.py")]
    paths += list(docops.rglob("*.py"))
    paths += [Path(inspect.getfile(item)) for item in (model_gateway, rule_sources)]
    paths.append(ROOT / "uv.lock")
    for path in paths:
        if path.is_relative_to(semantic):
            name = Path("semantic") / path.relative_to(semantic)
        elif path.is_relative_to(docops):
            name = Path("documents") / path.relative_to(docops)
        else:
            name = Path(path.name)
        data = guest.COMPILATION_GUIDE.encode("utf-8") if path == guide else path.read_bytes()
        destination = code / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        fingerprints[str(name)] = digest(data)
    # Installed versions can differ from the lock during local --no-sync runs.
    runtime = encoded({"python": sys.version, "platform": platform.platform(), "distributions": {item.metadata["Name"]: item.version for item in importlib.metadata.distributions()}})
    (code / "runtime.json").write_bytes(runtime)
    fingerprints["runtime.json"] = digest(runtime)
    return digest(encoded(fingerprints))


def run_experiment(cases, rules, metadata, units, scoped, output: Path, runner_factory, *, seconds: int = 7200) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    revision = preserve_code(output)
    outcomes: dict[str, dict[str, Any]] = {}
    cache: dict[str, RulePlan] = {}
    selected = [case for unit in units for case in unit.targets]
    run = metadata | {
        "kind": "local_semantic_guest_corpus",
        "engine_revision": revision,
        "model": "grok-4.7",
        "reasoning_effort": "medium",
        "full_scope": {"files": len(cases), "rules": sum(len(case.expected) for case in cases)},
        "document_scope": {"files": len(scoped), "rules": sum(len(case.expected) for case in scoped)},
        "selected": {"units": len(units), "files": len(selected), "rules": sum(len(case.expected) for case in selected)},
        "product_acceptance": False,
        "training_eligible": False,
        "baseline_exceptions_applied": False,
        "runs": [],
    }

    def report():
        rows = comparison_rows(cases, outcomes)
        usage = [item["usage"] for item in run["runs"]]
        run["usage_total"] = {
            **{key: sum(item.get(key, 0) for item in usage) for key in ("completed_requests", "requests_started", "input_tokens", "output_tokens")},
            "uncertain": any(item.get("uncertain", True) for item in usage),
            "in_flight": any(item.get("in_flight", True) for item in usage),
        }
        run["not_executed"] = {"files": sum(case.identity not in outcomes for case in cases), "rules": sum(row["kind"] == "not_executed" for row in rows)}
        run["matched_rules"] = sum(row["kind"] == MATCH for row in rows)
        run["strict_full_scope_match_rate"] = run["matched_rules"] / run["full_scope"]["rules"]
        write_acceptance_report(output / "report.json", rows, run=run)

    report()
    for unit in units:
        root = output / ("unit-" + digest(unit.name.encode())[:16])
        root.mkdir()
        summary: dict[str, Any] = {"unit": unit.name, "targets": [case.file_name for case in unit.targets], "run_completed": False, "training_eligible": False}
        runner = None
        bundle = None
        phase = "setup"
        try:
            spec, provenance = prepare_unit(unit, root / "inputs", rules, revision, cache)
            summary.update(execution_id=str(spec.execution_id), rule_sources=provenance, document_sha256={item.filename: item.sha256 for item in spec.documents})
            originals = {path.name: digest(path.read_bytes()) for path in (root / "inputs").iterdir()}
            phase = "execution"
            runner = runner_factory(root / "inputs", root / "runner")
            with runner:
                bundle = guest.execute(root / "inputs", root / "work", runner, time.monotonic() + seconds, limits=ReadLimits(output_bytes=64 * 1024 * 1024))
            summary["source_unchanged"] = all(digest((root / "inputs" / name).read_bytes()) == value for name, value in originals.items())
            if not summary["source_unchanged"]:
                raise ValueError("original_changed_during_execution")
            if runner.usage.get("uncertain") or runner.usage.get("in_flight"):
                raise ValueError("model_usage_unsettled")
            result = bundle_result(bundle, spec)
            outcomes.update({case.identity: {"result": result} for case in unit.targets})
            cache.update(bundle.plans)
            summary["run_completed"] = True
        except Exception as error:
            # Provider bodies, model output and settings never enter error text.
            summary["error"] = {
                "stage": phase,
                "type": type(error).__name__,
                "frames": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno} for frame in traceback.extract_tb(error.__traceback__)[-8:]],
            }
            outcomes.update({case.identity: {"failure": "setup_failure" if phase == "setup" else "execution_failure"} for case in unit.targets})
        finally:
            summary["usage"] = runner.usage if runner is not None else {"requests": 0, "input_tokens": 0, "output_tokens": 0, "uncertain": False, "in_flight": False}
            save(root / "summary.json", summary)
            run["runs"].append(summary)
            report()
        print(json.dumps({"unit": unit.name, "run_completed": summary["run_completed"], "usage": summary["usage"]}, ensure_ascii=False), flush=True)
        if summary["usage"].get("uncertain") or summary["usage"].get("in_flight"):
            break
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=["v2", "v3"], required=True)
    parser.add_argument("--doc-key")
    parser.add_argument("--case", action="append", help="exact canonical filename; repeat for more files")
    parser.add_argument("--bundle", action="append", help="exact V3 bundle name; all bundle files remain available as context")
    parser.add_argument("--assets-root", type=Path, default=ROOT / "assets")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings-root", type=Path, default=ROOT)
    parser.add_argument("--grok", default=shutil.which("grok"))
    parser.add_argument("--live", action="store_true", help="without this flag, print the selected scope and make no model calls")
    parser.add_argument("--seconds", type=int, default=7200)
    parser.add_argument("--max-requests", type=int, default=256)
    parser.add_argument("--input-token-budget", type=int, default=10_000_000)
    parser.add_argument("--output-token-budget", type=int, default=2_000_000)
    args = parser.parse_args()
    if args.output.exists() or min(args.seconds, args.max_requests, args.input_token_budget, args.output_token_budget) < 1:
        parser.error("a new output directory and positive bounded budgets are required")
    try:
        cases, rules, metadata = load_corpus(args.corpus, args.assets_root)
        units, scoped = select_units(cases, doc_key=args.doc_key, case_names=args.case, bundle_names=args.bundle)
    except (OSError, ValueError, AssertionError) as error:
        parser.error(str(error))
    if not args.live:
        print(
            json.dumps(
                {
                    "live": False,
                    "corpus": args.corpus,
                    "full_scope": {"files": len(cases), "rules": sum(len(case.expected) for case in cases)},
                    "selected": [{"unit": unit.name, "targets": [case.file_name for case in unit.targets], "context_files": len(unit.documents) - len(unit.targets)} for unit in units],
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not args.grok:
        parser.error("grok executable is required for --live")
    from local_grok_runner import LocalGrokRunner
    from product.settings import Settings

    settings = Settings(_env_file=args.settings_root / ".env")  # type: ignore[call-arg]

    def runner_factory(inputs, output):
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

    run = run_experiment(cases, rules, metadata, units, scoped, args.output.resolve(), runner_factory, seconds=args.seconds)
    print(json.dumps({"report": str(args.output / "report.json"), "matched_rules": run["matched_rules"], "full_scope": run["full_scope"], "not_executed": run["not_executed"]}, ensure_ascii=False))
    return int(run["matched_rules"] != run["full_scope"]["rules"])


if __name__ == "__main__":
    raise SystemExit(main())
