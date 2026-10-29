"""Evaluate a compact execution certificate; inspect bounded local questions."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .bindings import UnresolvedCollection
from .document_tools import save
from .models import All, AnyOf, CollectionExists, ColumnExists, Expr, Judge, Not, RecordsProgram, Related, RulePlan, ValueTest
from .trace import AuditEvaluation, AuditTrace, ProgramUse, evaluate_audit


def prepare_audit(plans_by_target: dict[str, RulePlan]) -> tuple[AuditTrace, list[dict[str, Any]]]:
    """Derive binding work from the program; never infer positions or facts.

    The obligations are a navigation view, not another rule authority. Fields
    in conditional branches may not be needed once their conditions are known.
    The initial trace is valid but unresolved, never a completed model result.
    """
    uses: list[ProgramUse] = []
    obligations: list[dict[str, Any]] = []
    for audit_id, plan in plans_by_target.items():
        for rule in plan.rules:
            fields: dict[str, set[str]] = {}
            questions: list[dict[str, Any]] = []

            def include(name: str, names: list[str] | tuple[str, ...] = ()) -> None:
                fields.setdefault(name, set()).update(names)

            def visit(node: Expr | None, current: str | None, path: str) -> None:
                if node is None:
                    return
                if isinstance(node, ValueTest):
                    if current is None:
                        raise ValueError("A native value needs a record input")
                    include(current, [node.field])
                elif isinstance(node, ColumnExists):
                    include(node.input, [node.field])
                elif isinstance(node, CollectionExists):
                    include(node.input)
                elif isinstance(node, (All, AnyOf, Not)):
                    for index, child in enumerate(node.items if isinstance(node, (All, AnyOf)) else [node.item]):
                        visit(child, current, f"{path}/{index}")
                elif isinstance(node, Judge):
                    if current is not None:
                        include(current, node.fields)
                    for name in node.inputs:
                        include(name)
                    questions.append({"program_path": path, "question": node.question, "record_input": current, "fields": node.fields, "context_inputs": node.inputs})
                elif isinstance(node, Related):
                    if current is None:
                        raise ValueError("A relation needs a source input")
                    include(current, node.source_key.fields + [item.source_field for item in node.comparisons])
                    include(node.target, node.target_key.fields + [item.target_field for item in node.comparisons])
                    visit(node.target_when, node.target, path + "/target_when")
                else:
                    raise ValueError("Unsupported program node")

            program = rule.program
            visit(program.precondition, None, "precondition")
            source = program.source if isinstance(program, RecordsProgram) else None
            if isinstance(program, RecordsProgram):
                include(program.source)
                visit(program.when, program.source, "when")
            visit(program.require, source, "require")
            uses.append(ProgramUse(audit_id=audit_id, rule_id=rule.id, inputs={name: UnresolvedCollection(kind="unresolved", reason=f"尚未绑定集合 {name}") for name in fields}))
            obligations.append({"audit_id": audit_id, "rule_id": rule.id, "inputs": {name: {"fields": sorted(names)} for name, names in fields.items()}, "semantic_questions": questions})
    return AuditTrace(uses=uses), obligations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("execution")
    evaluate.add_argument("--documents", default="documents.json")
    evaluate.add_argument("--plans", default="plans.json")
    evaluate.add_argument("--targets", default="targets.json")
    evaluate.add_argument("--out", required=True)
    view = commands.add_parser("view")
    view.add_argument("evaluation")
    view.add_argument("--need")
    view.add_argument("--offset", type=int, default=0)
    view.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if args.command == "evaluate":
        trace = AuditTrace.model_validate_json(Path(args.execution).read_text())
        documents = json.loads(Path(args.documents).read_text())
        plans = {key: RulePlan.model_validate(value) for key, value in json.loads(Path(args.plans).read_text()).items()}
        targets = json.loads(Path(args.targets).read_text())
        result = evaluate_audit(trace, documents, {audit_id: plans[rule_id] for audit_id, rule_id in targets.items()})
        save(args.out, result.model_dump(mode="json"))
        answered = {answer.need_id for answer in trace.answers}
        pending = [need.id for need in result.needs if need.id not in answered]
        print(
            json.dumps(
                {
                    "output": args.out,
                    "checks": len(result.report.checks),
                    "statuses": dict(Counter(check.status for check in result.report.checks)),
                    "pending_questions": len(pending),
                    "question_examples": pending[:8],
                    "evidence_gaps": len(result.evidence_gaps),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.offset < 0 or not 1 <= args.limit <= 32:
        parser.error("offset must be nonnegative and limit must be 1..32")
    result = AuditEvaluation.model_validate_json(Path(args.evaluation).read_text())
    needs = [need for need in result.needs if args.need is None or need.id == args.need]
    if args.need is not None and not needs:
        parser.error("unknown local question")
    end = args.offset + args.limit
    print(json.dumps({"needs": [need.model_dump(mode="json") for need in needs[args.offset : end]], "count": len(needs), "next_offset": end if end < len(needs) else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
