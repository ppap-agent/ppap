"""Read-only original cell facts shared by fixed preparation checks."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from audit.document_tools import effective, snapshot
from audit.evidence import cell_locator


class UnknownFact(ValueError):
    """The original cannot support this decision."""


class DefectFact(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def key(value):
    value = unicodedata.normalize("NFKC", str(value if value is not None else "")).casefold()
    return re.sub(r"[\s:：()（）\[\]【】_.。-]", "", value)


class SourceFacts:
    """Use the existing portable snapshot; never calculate a missing formula."""

    def __init__(self, audit):
        self.audit = audit
        self.data = snapshot(audit.file_url)
        self.sources = []

    def read(self, table, row, column, *, vertical_only=False):
        observed = effective(table, row, column)
        if observed["state"] != "known":
            raise UnknownFact("原件单元格尚不能可靠读取，不能当作空值或通过。")
        if vertical_only:
            for r1, c1, r2, c2 in table.get("merges", []):
                if r1 <= row <= r2 and c1 <= column <= c2 and c1 != c2 and column != c1:
                    raise UnknownFact("数据横向合并跨越多个语义列，需确认真实列归属。")
        value = observed.get("value")
        locator = {"file_version_id": self.audit.audit_id, **cell_locator(self.data["source"]["format"], table["id"], row, column).model_dump(mode="json")}
        source = {"kind": "structure", "locator": json.dumps(locator, ensure_ascii=False, separators=(",", ":")), "excerpt": str(value if value is not None else "空单元格")[:500]}
        if source not in self.sources and len(self.sources) < 100:
            self.sources.append(source)
        return value

    def evidence(self):
        suffix = Path(self.audit.file_url).suffix.lower().lstrip(".")
        return {
            "schema_version": "1",
            "required": "structure",
            "used": "structure" if self.sources else "none",
            "source_format": suffix,
            "prepared_format": suffix,
            "sources": list(self.sources),
            "diagnostics": [],
        }

    def label(self, table, aliases, before_row):
        candidates = []
        aliases = {key(value) for value in aliases}
        for row in range(1, min(before_row, table["rows"] + 1)):
            for column in range(1, table["cols"] + 1):
                observed = effective(table, row, column)
                if key(observed.get("value")) not in aliases:
                    continue
                self.read(table, row, column)
                right = column + 1
                for r1, c1, r2, c2 in table.get("merges", []):
                    if r1 <= row <= r2 and c1 <= column <= c2:
                        right = c2 + 1
                        break
                if right <= table["cols"]:
                    candidates.append(self.read(table, row, right))
        if not candidates:
            raise UnknownFact("受控表头未定位到" + next(iter(aliases)) + "，需核对模板结构。")
        values = {"".join(unicodedata.normalize("NFKC", str(value if value is not None else "")).split()).casefold() for value in candidates}
        if len(values) != 1:
            raise UnknownFact("同一身份字段有多处冲突值，需确认原件。")
        return candidates[0]

    def observe(self, source_id, operation, *, default_message="已核实原件中的本项准备事实。"):
        self.sources = []
        status, code, message = "satisfied", None, default_message
        try:
            result = operation()
            if isinstance(result, str):
                message = result
            if not self.sources:
                raise UnknownFact("本项尚未取得原件定位，不能视为通过。")
        except DefectFact as exc:
            status, code, message = "defect", exc.code, str(exc)
        except UnknownFact as exc:
            status, message = "unknown", str(exc)
        except Exception:
            status, message = "error", "原件读取或纯规则执行失败，尚无业务结论。"
        return {
            "id": source_id,
            "source_id": source_id,
            "status": status,
            "code": code,
            "message": message,
            "certainty": "confirmed" if status == "defect" else "unknown",
            "critical": bool(code and code.startswith("B")),
            "certified": False,
            "evidence": self.evidence(),
            "next_step": "继续正式审核。" if status == "satisfied" else "重试预检；若仍失败，联系产品维护。" if status == "error" else "核对定位原件；修正或确认资料后重新检查。",
        }


def state_fact(source_id, code, message, *, status="not_checked"):
    return {
        "id": source_id,
        "source_id": source_id,
        "status": status,
        "code": code,
        "message": message,
        "certainty": "unknown",
        "critical": False,
        "certified": False,
        "evidence": None,
        "next_step": "按说明确认原件或补充关联资料。",
    }


def checker_fact(agent, source_id, check, *, code="W02"):
    """Run one fixed preparation predicate through an existing Fluent context."""
    from audit.engine.rule_engine import GuardStop

    ctx, status, message = None, "unknown", "本项尚未核实。"
    try:
        ctx = agent.rule_ctx(agent._load_workbook())
        observed = check(ctx)
        decision, message = observed if isinstance(observed, tuple) else ("PASS", "已核实原件准备事实。")
        if ctx.has_unresolved():
            decision, message = "UNABLE", "存在不可读取单元格；本项尚未核实。"
        status = {"PASS": "satisfied", "NG": "defect", "ERR": "error", "UNABLE": "unknown", "NA": "not_checked"}.get(decision, "unknown")
    except GuardStop as exc:
        status = {"NG": "defect", "ERR": "error"}.get(exc.status, "unknown")
        message = exc.message
    except Exception:
        status, message = "error", "原件读取或准备检查执行失败。"
    if ctx is not None and ctx.has_unresolved():
        status, message = "unknown", "存在不可读取单元格；本项尚未核实，不能判为文件缺陷。"
    evidence = agent._evidence_from_reads(ctx) if ctx is not None else None
    if status == "satisfied" and (not evidence or not evidence.sources):
        status, message = "unknown", "尚未取得原件定位，不能视为通过。"
    return {
        "id": source_id,
        "source_id": source_id,
        "status": status,
        "code": code if status == "defect" else None,
        "message": message,
        "certified": False,
        "critical": False,
        "evidence": evidence.model_dump(mode="json") if evidence else None,
    }
