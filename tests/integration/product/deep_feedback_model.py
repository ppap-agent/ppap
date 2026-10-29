"""Controlled model boundary for the real two-document feedback acceptance path.

This module supplies programs and source bindings, never final findings. Parser,
validation, logical object construction and business computation remain real.
"""

import json
from io import BytesIO

from openpyxl import Workbook
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditTrace

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
