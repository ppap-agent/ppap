"""Source-addressed facts and explicit clause execution for standalone directory checkers.

These helpers read bounded, source-addressed facts. They do not interpret a
customer rule description, generate checks, or choose a customer/engine route.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from documents.model_access import ModelUnavailable

from audit.contracts import AuditEvidence, AuditResultItem, EvidenceDiagnostic, EvidenceSource
from audit.document_tools import ReadLimits, TableReader, snapshot
from audit.engine.rule_engine.context import GuardStop
from audit.evidence import EvidenceBudget, cell_locator
from audit.fact_questions import BooleanFactEvaluation, FactQuestion


class UnknownFact(Exception):
    """The native source does not establish the required fact."""

    def __init__(self, message, *, questions=(), evaluation=None):
        super().__init__(message)
        self.questions = list(questions)
        self.evaluation = evaluation


class MissingFact(Exception):
    """An observed complete structure lacks a required literal fact."""

    def __init__(self, message, *, need=None):
        super().__init__(message)
        self.need = need


def key(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold().rstrip(":：")


def blank(value: Any) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def number(value: Any) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value).strip().rstrip("%"))
        if not result.is_finite():
            raise ValueError
        return result / 100 if isinstance(value, str) and value.strip().endswith("%") else result
    except (InvalidOperation, ValueError):
        raise UnknownFact("数值无法可靠读取，需人工核对原件。") from None


def header(value: Any, aliases: tuple[str, ...]) -> bool:
    actual = key(value)
    for alias in aliases:
        wanted = key(alias)
        if actual == wanted:
            return True
        actual_chinese = "".join(re.findall(r"[\u4e00-\u9fff]", actual))
        wanted_chinese = "".join(re.findall(r"[\u4e00-\u9fff]", wanted))
        if wanted_chinese and not re.search(r"[a-z]", wanted) and actual_chinese == wanted_chinese:
            return True
        if actual.startswith(wanted) and re.fullmatch(r"[a-z0-9/()_.:—-]+", actual[len(wanted) :]):
            return True
    return False


def _identity(value):
    text = "" if value is None else unicodedata.normalize("NFKC", str(value))
    return "".join(text.split()).casefold()


class NativeFacts:
    def __init__(self, path: str, version: str, witness, *, expected_sha256=None):
        self.data = snapshot(path, limits=ReadLimits())
        if expected_sha256 and expected_sha256 != self.data["source"]["sha256"]:
            raise ValueError("Frozen source identity changed")
        self.version, self.witness = version, witness
        self.format = self.data["source"]["format"]
        if self.format == ".pdf":
            # Table checks always consume the existing PDF -> Excel preparation;
            # graphical facts are separately read from the frozen original.
            from audit.pdf_tables import prepare_pdf_workbook

            self.data = prepare_pdf_workbook(path, self.data, ReadLimits())
        self._unread_visual = self.format == ".xls"
        if self.format == ".docx":
            import zipfile

            with zipfile.ZipFile(path) as archive:
                self._unread_visual = any(name.startswith(("word/header", "word/footer", "word/media/")) for name in archive.namelist())
        self.readers = {table["id"]: TableReader(table, EvidenceBudget()) for table in self.data.get("tables", [])}

    def cell_location(self, table, row, column, fact):
        if self.data.get("prepared_format") == "xlsx":
            source = fact.get("source", {})
            if not all(source.get(field) for field in ("page", "row", "column", "bbox")):
                raise UnknownFact("转换单元格尚无可核对的原 PDF 位置。")
            from audit.pdf_tables import valid_pdf_bbox

            page = next((page for page in self.data.get("pages", []) if page["page"] == source["page"]), None)
            if page is None or not valid_pdf_bbox(source["bbox"], page["width"], page["height"]):
                raise UnknownFact("转换单元格的原 PDF 区域无效，不能定位原件。")
            # The prepared grid and original cell mapping remain in native
            # metadata. The public contract is a location on the frozen PDF,
            # not a locator decorated with private workbook provenance.
            return {
                "kind": "pdf_page",
                "page": source["page"],
                "region": source["bbox"],
                "source_sha256": self.data["source"]["sha256"],
            }
        return cell_locator(self.format, table["id"], row, column).model_dump(mode="json")

    def cell(self, table, row, column):
        fact = self.readers[table["id"]].cell(row, column)
        locator = self.cell_location(table, row, column, fact)
        self.witness(locator, fact.get("value"), self.version)
        if fact.get("state") != "known":
            raise UnknownFact("原件单元格含未算出的公式、图像或读取不确定性。")
        return fact.get("value")

    def observations(self):
        for table in self.data.get("tables", []):
            seen = set()
            for address, fact in table["cells"].items():
                anchor = fact.get("anchor", address)
                if anchor in seen or fact.get("state") != "known" or blank(fact.get("value")):
                    continue
                seen.add(anchor)
                match = re.fullmatch(r"([A-Z]+)(\d+)", address)
                if not match:
                    continue
                column = 0
                for character in match.group(1):
                    column = column * 26 + ord(character) - ord("A") + 1
                locator = self.cell_location(table, int(match.group(2)), column, fact)
                yield str(fact["value"]), locator
        for paragraph in self.data.get("paragraphs", []):
            if paragraph.get("text", "").strip():
                yield paragraph["text"], {"kind": "docx_paragraph", "paragraph": int(paragraph["location"].split(":")[1])}
        for page in self.data.get("pages", []):
            for index, block in enumerate(page.get("blocks", []), 1):
                if block.get("text", "").strip():
                    yield block["text"], {"kind": "pdf_block", "page": page["page"], "block": index}

    def has_unread_content(self):
        return (
            self._unread_visual
            or any(table.get("visual_evidence") or any(item.get("state") != "known" for item in table["cells"].values()) for table in self.data.get("tables", []))
            or any(page.get("image_count", 0) or page.get("state") not in {None, "known"} for page in self.data.get("pages", []))
        )

    def contains(self, text: str) -> bool:
        for value, locator in self.observations():
            if key(text) in key(value):
                self.witness(locator, value, self.version)
                return True
        return False

    def require_title(self, text: str):
        for value, locator in self.observations():
            kind = locator.get("kind")
            address = str(locator.get("cell", ""))
            eligible = kind in {"xlsx_cell", "xls_cell"} and bool(re.search(r"[1-5]$", address)) and int(re.sub(r"^[A-Z]+", "", address) or "0") <= 5
            eligible = eligible or kind == "docx_paragraph" and locator.get("paragraph", 99) <= 3
            eligible = eligible or kind == "docx_cell" and int(locator.get("row", 99)) <= 3
            eligible = eligible or kind == "pdf_block" and locator.get("page") == 1 and locator.get("block", 99) <= 3
            if eligible and key(text) in key(value):
                self.witness(locator, value, self.version)
                return
        raise UnknownFact("尚不能从原件标题位置确认“" + text + "”，正文提及不能代替文件标题。")

    def require_text(self, text: str):
        if self.contains(text):
            return
        if self.has_unread_content():
            raise UnknownFact(f"未能从可读取原件确认“{text}”，图像或未知内容仍需核对。")
        raise MissingFact(f"原件缺少“{text}”。")

    def tables(self, title: tuple[str, ...] = ()):
        unresolved = self.data.get("coverage", {}).get("unresolved_pages", [])
        if self.format == ".pdf" and unresolved:
            raise UnknownFact("PDF仍有未解析页面，不能确认完整表格范围：" + "、".join(map(str, unresolved)))
        if self.format == ".pdf" and self.data.get("prepared_format") != "xlsx":
            return []
        available = [table for table in self.data.get("tables", []) if not any(word in key(table["id"]) for word in ("填写指南", "填表说明", "填写说明", "评分", "準則", "准则", "数据库"))]
        if not title:
            return available
        matched = []
        for table in available:
            first_rows = " ".join(str(item.get("value") or "") for address, item in table["cells"].items() if int(re.sub(r"^[A-Z]+", "", address)) <= 5 and item.get("state") == "known")
            if any(key(word) in key(table["id"]) or key(word) in key(first_rows) for word in title):
                matched.append(table)
        if not matched and len(available) == 1 and any(self.contains(word) for word in title):
            return available
        return matched

    def table(self, title: tuple[str, ...] = ()):
        if self.format == ".pdf":
            if self.data.get("prepared_format") != "xlsx":
                raise UnknownFact("PDF尚未形成可靠的Excel表格，无法检查表格内容。")
        tables = self.tables(title)
        if len(tables) != 1:
            raise UnknownFact("没有唯一可确认的业务表；缺失或多份候选需人工确认。")
        return tables[0]

    def columns(self, table, aliases: dict[str, tuple[str, ...]]):
        matches = []
        best = 0
        for row in range(1, min(table["rows"], 60) + 1):
            columns: dict[str, int] = {}
            for column in range(1, min(table["cols"], 120) + 1):
                item = self.readers[table["id"]].cell(row, column)
                if item.get("state") != "known":
                    continue
                for name, names in aliases.items():
                    if header(item.get("value"), names):
                        if name in columns and columns[name] != column:
                            previous = self.readers[table["id"]].cell(row, columns[name])
                            if previous.get("anchor") == item.get("anchor"):
                                continue
                            raise UnknownFact(f"同一表头有重复的{name}列，尚不能确定作用范围。")
                        columns[name] = column
            if len(columns) > best:
                best, matches = len(columns), [(row, columns)]
            elif len(columns) == best and best:
                matches.append((row, columns))
        # Repeated page headers with the same column layout are safe; every
        # physical data row still gets evaluated, never only the first page.
        layouts = {tuple(sorted(columns.items())) for _row, columns in matches}
        if len(layouts) > 1:
            raise UnknownFact("业务表中有多套不同表头，尚不能确定数据范围。")
        if best != len(aliases):
            if table["rows"] > 60 or table["cols"] > 120:
                raise UnknownFact("表头搜索未覆盖完整范围，不能把未找到的列判为缺失。")
            if self.has_unread_content():
                raise UnknownFact("所需列未能全部读取，未知区域需核对。")
            missing = sorted(set(aliases) - set(matches[0][1] if matches else ()))
            raise MissingFact("缺少列：" + "、".join(missing))
        start, columns = matches[0]
        for name, column in columns.items():
            self.cell(table, start, column)
        return start, columns

    def rows(self, aliases, *, title=(), anchor=(), defer_unknown=False):
        table = self.table(title)
        start, columns = self.columns(table, aliases)
        records = []
        for row in range(start + 1, table["rows"] + 1):
            raw = {name: self.readers[table["id"]].cell(row, column) for name, column in columns.items()}
            if all(blank(item.get("value")) and item.get("state") == "known" for item in raw.values()):
                continue
            if (
                all(item.get("state") == "known" for item in raw.values())
                and any(not blank(item.get("value")) for item in raw.values())
                and all(header(item.get("value"), aliases[name]) for name, item in raw.items() if not blank(item.get("value")))
            ):
                continue
            for name in anchor:
                if raw[name].get("state") != "known" and not defer_unknown:
                    # An unreadable scope anchor cannot establish that the row
                    # is outside the business range, even when resources exist.
                    self.cell(table, row, columns[name])
            if anchor and all(raw[name].get("state") == "known" and blank(raw[name].get("value")) for name in anchor):
                continue
            if any(key(raw[name].get("value")).startswith(marker) for name in anchor for marker in ("编制/日期", "审核/日期", "编制人", "批准人")):
                continue
            records.append(NativeRow(self, table, row, columns))
        if not records:
            raise UnknownFact("没有有效业务数据行，不能按空范围通过。")
        return records

    def field(self, aliases: tuple[str, ...], *, title=(), required=True):
        values = set()
        candidates = []
        unknown = None
        for table in self.tables(title):
            for row in range(1, min(table["rows"], 40) + 1):
                for column in range(1, min(table["cols"], 80) + 1):
                    item = self.readers[table["id"]].cell(row, column)
                    if item.get("state") != "known" or not header(item.get("value"), aliases):
                        continue
                    self.cell(table, row, column)
                    merged = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= column <= span[3]), None)
                    next_column = merged[3] + 1 if merged else column + 1
                    if next_column <= table["cols"]:
                        fact = self.readers[table["id"]].cell(row, next_column)
                        # This established matrix heading names columns, not a
                        # project whose value is "否决". Keep real 项目/value
                        # pairs elsewhere, including conflicting identities.
                        if fact.get("state") == "known" and header(item.get("value"), ("项目", "检验项目", "试验项目")) and header(fact.get("value"), ("否决", "Reject")):
                            continue
                        locator = self.cell_location(table, row, next_column, fact)
                        candidates.append(locator)
                        try:
                            value = self.cell(table, row, next_column)
                        except UnknownFact as exc:
                            unknown = exc
                            continue
                        if not blank(value):
                            values.add(str(value).strip())
        # Explicit colon-separated paragraph/block fields preserve their own
        # value scope; no free text search or arbitrary next line borrowing.
        if not title and self.format in {".docx", ".pdf"}:
            for text, locator in self.observations():
                for line in text.splitlines():
                    parts = re.split(r"[:：]", line, maxsplit=1)
                    if len(parts) == 2 and header(parts[0], aliases) and parts[1].strip():
                        self.witness(locator, line, self.version)
                        values.add(parts[1].strip())
        question = None
        if len(candidates) == 1 and (unknown is not None or not values):
            import hashlib

            from audit.models import public_locator

            locator = candidates[0]
            # Strip internal extraction provenance; retain the original location.
            locator = {name: value for name, value in locator.items() if name not in {"bbox", "source_sha256", "prepared_sheet", "prepared_cell", "prepared_sha256"}}
            if public_locator(locator) is not None:
                identity = json.dumps({"aliases": aliases, "title": title, "locator": locator}, ensure_ascii=False, sort_keys=True)
                question = FactQuestion(
                    file_version_id=self.version,
                    field="field." + hashlib.sha256(identity.encode()).hexdigest()[:24],
                    prompt="原件此处“" + aliases[0] + "”填写的内容是什么？",
                    answer_type="text",
                    locator=locator,
                )
        if unknown is not None:
            raise UnknownFact(str(unknown), questions=[question] if question else [])
        if len(values) > 1:
            raise UnknownFact("身份或字段有多个不同取值，不能任取第一个。")
        if values:
            return next(iter(values))
        if required:
            raise UnknownFact("未能唯一读取字段“" + aliases[0] + "”的对应值。", questions=[question] if question else [])
        return ""


def any_row(rows, predicate) -> bool:
    """A row that proves the fact decides it; unknown rows only matter when none does."""
    unknown = None
    for row in rows:
        try:
            if predicate(row):
                return True
        except UnknownFact as exc:
            unknown = unknown or exc
    if unknown is not None:
        raise unknown
    return False


# Identities a cross-file clause can name, with the labels that state them.
class NativeRow(Mapping[str, Any]):
    def __init__(self, native, table, row, columns):
        self.native, self.table, self.row, self.columns = native, table, row, columns

    def __getitem__(self, name):
        return self.native.cell(self.table, self.row, self.columns[name])

    def __iter__(self):
        return iter(self.columns)

    def __len__(self):
        return len(self.columns)


class _Clause:
    def __init__(self, owner, index, clause_id):
        self._owner, self._index = owner, index
        self.rule_item = SimpleNamespace(id=clause_id)

    def check(self):
        return self._owner.check(self._index)


class SourceAudit:
    """Execute only source-owned, explicitly bound methods, without a version router."""

    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".docx", ".pdf"})

    def __init__(self, audit_file, rule, group_context=None, *, resource):
        self.audit_file, self.rule, self.group_context = audit_file, rule, group_context
        self.resource = resource
        self.logger = logging.getLogger(type(self).__name__)
        self._native = None
        self._visual_cache = {}
        self._evidence_sources: list[EvidenceSource] = []
        rows = getattr(rule, "rules", ()) or ()
        self._rule_rows = {str(row.get("id") if isinstance(row, dict) else row.id): row for row in rows}
        bindings = resource["clauses"]
        self.clause_ids = list(self._rule_rows)
        self.agent_rules = [self._category(cid) for cid in self.clause_ids]
        self.rule_checkers = [getattr(self, bindings[cid]["method"]) if cid in bindings else None for cid in self.clause_ids]
        self.checks = [_Clause(self, index, cid) for index, cid in enumerate(self.clause_ids)]

    def _category(self, clause_id):
        row = self._rule_rows.get(clause_id)
        category = row.get("category") if isinstance(row, dict) else getattr(row, "category", None)
        return str(category or clause_id)

    def evaluate(self, clause_id):
        if clause_id not in self.clause_ids:
            return AuditResultItem(
                classification="规则审核", item=self._category(clause_id), clause_id=clause_id, status="UNABLE", suggestions="此条款没有显式绑定的取证检查。", priority="P0", checks_performed=0
            )
        return self.check(self.clause_ids.index(clause_id))

    def close(self):
        self._native = None
        self._visual_cache.clear()

    def witness(self, locator, value, version=None, *, visual=False):
        source = EvidenceSource(
            kind="visual" if visual else "structure",
            locator=json.dumps({**locator, "file_version_id": version or self.audit_file.audit_id}, ensure_ascii=False, separators=(",", ":")),
            excerpt=str(value or "")[:500],
        )
        if source not in self._evidence_sources:
            self._evidence_sources.append(source)
            if len(self._evidence_sources) > 100:
                versions = [json.loads(item.locator)["file_version_id"] for item in self._evidence_sources]
                redundant = next((index for index, identity in enumerate(versions[:-1]) if versions.count(identity) > 1), None)
                if redundant is None:
                    raise UnknownFact("关联原件数量超出证据预算，不能丢失一份原件位置后判为通过。")
                self._evidence_sources.pop(redundant)

    def native(self):
        if self._native is None:
            self._native = self._source_facts(self.audit_file)
        return self._native

    def _source_facts(self, audit_file):
        metadata = audit_file.file_metadata or {}
        return NativeFacts(audit_file.file_url, audit_file.audit_id, self.witness, expected_sha256=metadata.get("source_sha256") or metadata.get("sha256"))

    def check(self, ith):
        self._evidence_sources = []
        completed = 0
        questions, evaluation, supply, grade = [], None, None, (None, None)
        try:
            if self.rule_checkers[ith] is None:
                raise UnknownFact(self.resource.get("known_gaps", {}).get(self.clause_ids[ith], "此条款尚无可用的原件证据读取器，无法检查。"))
            suffix = Path(self.audit_file.file_url).suffix.lower()
            supported = self.resource["clauses"][self.clause_ids[ith]].get("carriers", self.resource["supported_suffixes"])
            if suffix not in self.resource["supported_suffixes"] or suffix not in supported:
                raise UnknownFact("此检查不能读取这种格式的原件，需人工复核。")
            if not self.audit_file.audit_id:
                raise UnknownFact("缺少冻结原件版本，不能形成业务判断。")
            status, message, *graded = self.rule_checkers[ith]()
            # An optional third value grades an NG: (level, factual reason).
            grade = graded[0] if graded and status == "NG" else (None, None)
            if status == "PASS" and not self._evidence_sources:
                status, message = "UNABLE", "未取得可追溯的原件事实，不能判为通过。"
            completed = int(status in {"PASS", "NG", "NA"})
        except UnknownFact as exc:
            status, message = "UNABLE", str(exc)
            questions, evaluation = exc.questions, exc.evaluation
        except MissingFact as exc:
            status, message = "NG", str(exc)
            completed = 1
            supply = exc.need
        except GuardStop as exc:
            status, message = exc.status, exc.message
        except ModelUnavailable as exc:
            if exc.code in {"model_unavailable", "platform_model_unconfigured", "tenant_model_unavailable", "model_configuration_changed", "model_contract_retired"}:
                status, message = "UNABLE", "当前模型不可用，所需原件事实尚未核实。"
            else:
                status, message = "ERR", "原件模型请求失败或结果尚未确认，尚未取得业务结论。"
        except Exception:
            status, message = "ERR", "原件读取或检查执行失败，尚未取得业务结论。"
        suffix = Path(self.audit_file.file_url).suffix.lower().lstrip(".")
        evidence = None
        if suffix in {"xlsx", "xlsm", "xls", "docx", "doc", "pdf", "png", "jpg", "jpeg", "webp"}:
            evidence = AuditEvidence(
                required="structure_or_visual",
                used=("both" if len({item.kind for item in self._evidence_sources}) > 1 else self._evidence_sources[0].kind) if self._evidence_sources else "none",
                source_format=cast(Any, suffix),
                prepared_format=cast(Any, (self._native.data.get("prepared_format") if self._native is not None else None) or ("docx" if suffix == "doc" else suffix)),
                sources=tuple(self._evidence_sources),
                diagnostics=(EvidenceDiagnostic(code="uaes_source_fact", stage="evaluation", severity="info", message="客户原始条款的实际执行结果；未知事实未转为通过。"),),
            )
        return AuditResultItem(
            clause_id=self.clause_ids[ith],
            classification=self.agent_rules[ith],
            item=self.agent_rules[ith],
            status=status,
            suggestions=message,
            priority="P0",
            evidence=evidence,
            checks_performed=completed,
            fact_questions=questions,
            fact_evaluation=evaluation,
            supply=supply,
            severity=grade[0],
            severity_reason=grade[1],
        )

    def visual_pdf(self, fields: dict[str, str], *, existential_fields=()):
        """Read only requested graphical facts from the original carrier."""
        from audit.checker_lib.visual_facts import VisualFactsUnavailable, read_original_visual_facts

        try:
            return read_original_visual_facts(
                self.audit_file, fields, self.witness, operation="directory_original_visual_facts", cache=self._visual_cache, existential_fields=frozenset(existential_fields)
            )
        except VisualFactsUnavailable as exc:
            raise UnknownFact(str(exc)) from exc

    def visual_result(self, fields, description, *, existential_fields=()):
        values = self.visual_pdf(fields, existential_fields=existential_fields) if existential_fields else self.visual_pdf(fields)
        if not fields or any(name not in values or values[name] is not None and type(values[name]) is not bool for name in fields):
            raise UnknownFact(description + "未返回完整的原件事实，不能判为通过。")
        failed = [fields[name] for name, value in values.items() if value is False]
        if failed:
            return "NG", "原件确认不满足：" + "；".join(failed)
        if any(value is None for value in values.values()):
            questions = []
            evaluation = None
            # A single original page has an unambiguous scope. Multi-page or
            # unlocated facts still need an explicit observation plan.
            if Path(self.audit_file.file_url).suffix.lower() == ".pdf":
                import fitz

                with fitz.open(self.audit_file.file_url) as document:
                    if len(document) == 1 and not document.needs_pass:
                        questions = [
                            FactQuestion(
                                file_version_id=self.audit_file.audit_id,
                                field="visual." + name,
                                prompt="请核对原件：" + fields[name] + "？",
                                answer_type="boolean",
                                locator={"kind": "pdf_page", "page": 1},
                            )
                            for name, value in values.items()
                            if value is None
                        ]
                        evaluation = BooleanFactEvaluation(file_version_id=self.audit_file.audit_id, facts={"visual." + name: value for name, value in values.items()}, description=description)
            raise UnknownFact(description + "尚未全部核实，需人工确认原件。", questions=questions, evaluation=evaluation)
        return "PASS", "已从完整原件核实：" + description

    def controlled_stamp(self):
        observed = self.visual_pdf({"controlled_stamp": "文件中存在真实盖上的受控章；只有“受控章/受控/盖章处”文字标签、空白盖章栏时不算。"}, existential_fields=("controlled_stamp",))
        fact = observed["controlled_stamp"]
        return ("PASS", "已从原件核实受控章。") if fact is True else ("NG", "原件没有受控章。") if fact is False else ("UNABLE", "受控章尚未核实，需人工核对。")
