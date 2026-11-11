"""UAES source facts over the existing native reader and result contracts.

These helpers read bounded, source-addressed facts. They do not interpret a
customer rule description, generate checks, or choose a customer/engine route.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from documents.model_access import ModelUnavailable
from openpyxl.utils.cell import coordinate_to_tuple

from audit.checker_lib.li.base_agent import BaseAgent
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceDiagnostic, EvidenceSource
from audit.document_tools import ReadLimits, TableReader, ref, snapshot, source_title_location
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.engine.rule_engine.context import GuardStop
from audit.evidence import EvidenceBudget, cell_locator
from audit.fact_questions import BooleanFactEvaluation, FactQuestion
from audit.kinds import PpapDocumentType
from audit.precheck.pfmea_local import pair_identity


class UnknownFact(Exception):
    """The native source does not establish the required fact."""

    def __init__(self, message, *, questions=(), evaluation=None):
        super().__init__(message)
        self.questions = list(questions)
        self.evaluation = evaluation


class MissingFact(Exception):
    """An observed complete structure lacks a required literal fact."""


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


def header(value: Any, aliases: tuple[str, ...], *, exact: bool = False) -> bool:
    actual = key(value)
    for alias in aliases:
        wanted = key(alias)
        if actual == wanted:
            return True
        actual_chinese = "".join(re.findall(r"[\u4e00-\u9fff]", actual))
        wanted_chinese = "".join(re.findall(r"[\u4e00-\u9fff]", wanted))
        if wanted_chinese and not re.search(r"[a-z]", wanted) and actual_chinese == wanted_chinese:
            return True
        # An exact label keeps "Number of readings per subgroup" apart from "Number of readings".
        if not exact and actual.startswith(wanted) and re.fullmatch(r"[a-z0-9/()_.:—-]+", actual[len(wanted) :]):
            return True
        # A bilingual label ("データ数 Number of readings") names the same field as its English words, in any order.
        if re.fullmatch(r"[a-z0-9/()_.:—-]+", wanted) and re.search(r"[^\x00-\x7f]", actual):
            words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", str(value or "")).casefold())
            wanted_words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", alias).casefold())
            # Only a multi-word English phrase identifies a field; a lone "CPK" also heads "CPK目标值".
            if len(wanted_words) > 1 and sorted(words) == sorted(wanted_words):
                return True
    return False


class NativeFacts:
    def __init__(self, path: str, version: str, witness):
        self.data = snapshot(path, limits=ReadLimits())
        self.version, self.witness = version, witness
        self.format = self.data["source"]["format"]
        if self.format == ".pdf" and len(self.data["tables"]) > 1 and not self.data.get("coverage", {}).get("unresolved_pages"):
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
            locator = cell_locator(".pdf", f"page-{source['page']}", source["row"], source["column"]).model_dump(mode="json")
            return {
                **locator,
                "bbox": source["bbox"],
                "source_sha256": self.data["source"]["sha256"],
                "prepared_sheet": table["id"],
                "prepared_cell": ref(row, column),
                "prepared_sha256": self.data["prepared_sha256"],
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

    def require_source_title(self, aliases):
        location = source_title_location(self.data, aliases)
        if location is None:
            raise UnknownFact("原件尚未定位到明确的关联文件标题，正文引用不能代替对端身份。")
        if "row" in location:
            table = next(table for table in self.data["tables"] if table["id"] == location["table"])
            self.cell(table, location["row"], location["column"])
        elif location.get("kind") == "docx_paragraph":
            self.witness(location, self.data["paragraphs"][location["paragraph"] - 1]["text"], self.version)
        elif location.get("kind") == "pdf_block":
            page = next(page for page in self.data["pages"] if page["page"] == location["page"])
            self.witness(location, page["blocks"][location["block"] - 1]["text"], self.version)

    def tables(self, title: tuple[str, ...] = ()):
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
            if self.has_unread_content():
                raise UnknownFact("所需列未能全部读取，未知区域需核对。")
            missing = sorted(set(aliases) - set(matches[0][1] if matches else ()))
            raise MissingFact("缺少列：" + "、".join(missing))
        start, columns = matches[0]
        for name, column in columns.items():
            self.cell(table, start, column)
        return start, columns

    def rows(self, aliases, *, title=(), anchor=(), defer_unknown=False, table=None):
        table = table if table is not None else self.table(title)
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
                break
            records.append(NativeRow(self, table, row, columns))
        if not records:
            raise UnknownFact("没有有效业务数据行，不能按空范围通过。")
        return records

    def field(self, aliases: tuple[str, ...], *, title=(), required=True, numeric=False, table=None, exact=False):
        values = set()
        candidates = []
        unknown = None
        whole_sheet = table is not None
        for table in [table] if whole_sheet else self.tables(title):
            # A named report sheet places labelled results anywhere; a document field sits in its header band.
            if whole_sheet:
                cells = [coordinate_to_tuple(address) for address in table["cells"]]
            else:
                cells = [(row, column) for row in range(1, min(table["rows"], 40) + 1) for column in range(1, min(table["cols"], 80) + 1)]
            for row, column in cells:
                item = self.readers[table["id"]].cell(row, column)
                if item.get("state") != "known" or not header(item.get("value"), aliases, exact=exact):
                    continue
                self.cell(table, row, column)
                merged = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= column <= span[3]), None)
                next_column = merged[3] + 1 if merged else column + 1
                if numeric:
                    # A report form may leave spacer columns between a label and its number.
                    while next_column < min(table["cols"], column + 8) and blank(self.readers[table["id"]].cell(row, next_column).get("value")):
                        next_column += 1
                if next_column <= table["cols"]:
                    fact = self.readers[table["id"]].cell(row, next_column)
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
        if not title and not whole_sheet and self.format in {".docx", ".pdf"}:
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


class NativeRow(Mapping[str, Any]):
    def __init__(self, native, table, row, columns):
        self.native, self.table, self.row, self.columns = native, table, row, columns

    def __getitem__(self, name):
        return self.native.cell(self.table, self.row, self.columns[name])

    def __iter__(self):
        return iter(self.columns)

    def __len__(self):
        return len(self.columns)


def _kind_names(kind) -> list[str]:
    from audit.presets import customer_presets

    names = [str(kind)]
    resolved = PpapDocumentType.from_string(str(kind))
    if resolved != PpapDocumentType.UNKNOWN:
        names.append(resolved.get_type_name())
    names.extend(preset.title for preset in customer_presets() if preset.customer == "UAES" and preset.source_type == kind)
    return names


def _kind_matches(kind, terms) -> bool:
    """A term names a type by its text or by the document type it resolves to, such as PFMEA."""
    if any(key(term) in key(name) for term in terms for name in _kind_names(kind)):
        return True
    resolved = PpapDocumentType.from_string(str(kind))
    return resolved != PpapDocumentType.UNKNOWN and any(PpapDocumentType.from_string(term) == resolved for term in terms)


class UaesSourceAgent(BaseAgent):
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".docx", ".pdf"})
    """Use the existing RuleCheck and native source reader for UAES clauses."""

    def __init__(self, audit_file, rule, group_context=None):
        super().__init__(audit_file, rule, group_context)
        self._native = None
        self._evidence_sources: list[EvidenceSource] = []
        declarations = discover_declared_rules(type(self))
        self.agent_rules = [description for _name, description in declarations]
        self.rule_checkers = [getattr(self, name) for name, _description in declarations]

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
            self._native = NativeFacts(self.audit_file.file_url, self.audit_file.audit_id, self.witness)
        return self._native

    def check(self, ith):
        self._evidence_sources = []
        completed = 0
        questions, evaluation = [], None
        try:
            status, message = self.rule_checkers[ith]()
            if status == "PASS" and not self._evidence_sources:
                status, message = "UNABLE", "未取得可追溯的原件事实，不能判为通过。"
            completed = int(status in {"PASS", "NG", "NA"})
        except UnknownFact as exc:
            status, message = "UNABLE", str(exc)
            questions, evaluation = exc.questions, exc.evaluation
        except MissingFact as exc:
            status, message = "NG", str(exc)
            completed = 1
        except GuardStop as exc:
            status, message = exc.status, exc.message
        except ModelUnavailable as exc:
            if exc.code == "personal_model_unavailable":
                status, message = "UNABLE", "当前个人模型凭据不可用，所需原件事实尚未核实。"
            else:
                status, message = "ERR", "原件模型请求失败或结果尚未确认，尚未取得业务结论。"
        except Exception as exc:
            status, message = "ERR", f"原件读取或检查执行失败（{type(exc).__name__}），尚未取得业务结论。"
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
        from audit.engine.checkers.paddle_evidence import observe_shadow

        observe_shadow(self, self.rule_checkers[ith].__name__)
        return AuditResultItem(
            classification=self.agent_rules[ith],
            item=self.agent_rules[ith],
            status=status,
            suggestions=message,
            priority="P0",
            evidence=evidence,
            checks_performed=completed,
            fact_questions=questions,
            fact_evaluation=evaluation,
        )

    def visual_pdf(self, fields: dict[str, str], *, existential_fields=()):
        """Read original graphics: PDF pages through the PDF reader, worksheets through the shared bounded reader."""
        from audit.checker_lib.visual_facts import VisualFactsUnavailable as SheetFactsUnavailable
        from audit.checker_lib.visual_facts import read_original_visual_facts
        from audit.engine.checkers.pdf_presence import read_pdf_presence
        from audit.engine.checkers.visual_facts import VisualFactsUnavailable, read_original_pdf_visual_facts

        try:
            if not hasattr(self, "_paddle_page_cache"):
                self._paddle_page_cache: dict[Any, Any] = {}
            if Path(self.audit_file.file_url).suffix.lower() == ".pdf":
                # Existence holds on any page, so long documents are read page by page.
                observed = {
                    field: read_pdf_presence(self.audit_file, field=field, description=fields[field], witness=self.witness, cache=self._paddle_page_cache, operation="uaes_original_visual_facts")
                    for field in existential_fields
                }
                rest = {field: description for field, description in fields.items() if field not in observed}
                if rest:
                    observed |= read_original_pdf_visual_facts(self.audit_file, rest, self.witness, operation="uaes_original_visual_facts", cache=self._paddle_page_cache)
                return observed
            return read_original_visual_facts(
                self.audit_file, fields, self.witness, operation="uaes_original_visual_facts", cache=self._paddle_page_cache, existential_fields=frozenset(existential_fields)
            )
        except (VisualFactsUnavailable, SheetFactsUnavailable) as exc:
            raise UnknownFact(str(exc)) from exc

    def precheck(self):
        from .precheck import structural_facts

        return structural_facts(self)

    def visual_result(self, fields, description):
        values = self.visual_pdf(fields)
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

    def related_facts(self, item):
        """Read each same-batch original once per target; every clause reuses it."""
        cache = self.__dict__.setdefault("_related_facts", {})
        if item.audit_id not in cache:
            cache[item.audit_id] = NativeFacts(item.file_url, item.audit_id, self.witness)
        return cache[item.audit_id]

    def related(self, terms, *, require_name=False):
        """Use only this frozen GroupContext; pair by source identity, never order."""
        if self.group_context is None:
            raise UnknownFact("当前批次没有关联资料，需补充同零件的独立原件。")
        for kind in self.group_context.unavailable_files.values():
            if not kind or key(kind) in {"unknown", "未知", "未知类型"}:
                raise OSError("当前批次有类别未确认且无法读取的原件，尚不能排除所需对端。")
            names = _kind_names(kind)
            relevant = _kind_matches(kind, terms)
            # A broad MSA type can contain the requested Kappa/GRR/plan subtype.
            broad_msa = any("测量系统分析" in name or "msa" in key(name) for name in names)
            if relevant or broad_msa and any(key(term) in {"kappa", "grr", "msa计划", "测量系统分析计划"} for term in terms):
                raise OSError("当前批次有已提供但无法读取的关联资料。")
        candidates = {item.audit_id: item for kind, items in self.group_context.by_doc_type.items() if _kind_matches(kind, terms) for item in items if item.audit_id != self.audit_file.audit_id}
        photos = {item.audit_id for kind, items in self.group_context.by_doc_type.items() if key(kind) in {"e07", "生产件样品照片"} for item in items}
        # A broad type does not identify its Kappa/GRR subtype, so it stays open.
        classified = {item.audit_id for kind, items in self.group_context.by_doc_type.items() if not ("测量系统分析" in kind or "msa" in key(kind)) for item in items}
        undetermined = False
        for item in self.group_context.by_audit_id.values():
            if item.audit_id == self.audit_file.audit_id or item.audit_id in photos:
                continue
            if item.audit_id in candidates:
                continue
            # A broad type (for example MSA) does not identify its Kappa/GRR
            # subtype. An unclassified reference can also carry the source.
            # Read its own title; an upload name or a body reference is not identity.
            original = self.related_facts(item)
            try:
                original.require_source_title(terms)
            except UnknownFact:
                # A classified file of another type is not the source; an
                # unclassified one without a located title cannot be excluded.
                undetermined = undetermined or item.audit_id not in classified
                continue
            candidates[item.audit_id] = item
        if not candidates and not undetermined:
            raise MissingFact("当前批次缺少所需关联原件（" + terms[0] + "），请补充后复审。")
        if len(candidates) != 1:
            raise UnknownFact("当前批次缺少唯一关联原件，需补充或确认候选，不能任取一份。")
        item = next(iter(candidates.values()))
        if not self.audit_file.project_id or self.audit_file.project_id != item.project_id:
            raise UnknownFact("关联原件项目身份尚未确认一致。")
        other = self.related_facts(item)
        project_aliases = ("项目", "项目名称", "项目号", "UAES项目编号", "车型项目")
        project_required = self.rule.preset_key == "U04"
        own_project = self.native().field(project_aliases, required=project_required)
        peer_project = other.field(project_aliases, required=project_required)
        if (own_project or peer_project) and (not pair_identity(key(own_project)) or not pair_identity(key(peer_project)) or key(own_project) != key(peer_project)):
            raise UnknownFact("关联原件实际项目身份不一致或缺失，尚不能用于当前项目。")
        customer_aliases = ("客户名称", "客户", "customer")
        left_customer = self.audit_file.customer or self.native().field(customer_aliases)
        right_customer = item.customer or other.field(customer_aliases)
        if key(left_customer) != key(right_customer) or key(left_customer) not in {"uaes", "联合汽车电子", "联合汽车电子有限公司"}:
            raise UnknownFact("关联原件客户身份尚未确认一致。")
        part_aliases = ("零件号", "零件编号", "物料编号", "客户零件号", "part number")
        left, right = self.native().field(part_aliases), other.field(part_aliases)
        labels = {key(name) for name in (*part_aliases, "零件名称", "物料名称", "版本", "日期")}
        if not pair_identity(key(left)) or not pair_identity(key(right)):
            raise UnknownFact("关联原件零件号尚未确认，不能用占位值配对。")
        if key(left) in labels or key(right) in labels:
            raise UnknownFact("关联原件零件号值可能是相邻字段标签，需核对原件。")
        if key(left) != key(right):
            raise UnknownFact("关联原件零件号不一致，不能用于当前零件核对。")
        # Frozen version identity must also be known. Do not infer a revision
        # from upload order or borrow the current document's revision.
        revision_aliases = ("版本", "版次", "修订版次", "revision")
        own_revision, peer_revision = key(self.native().field(revision_aliases)), key(other.field(revision_aliases))
        if not pair_identity(own_revision) or not pair_identity(peer_revision) or own_revision != peer_revision:
            raise UnknownFact("关联原件版本不一致，需确认适用版本。")
        if require_name:
            part_name_aliases = ("零件名称", "物料名称", "part name")
            if key(self.native().field(part_name_aliases)) != key(other.field(part_name_aliases)):
                raise MissingFact("同项目同零件编号原件的零件名称不一致。")
        return other

    def related_all(self, *term_sets):
        """A missing source fails the clause, whatever is still unknown about the other sources."""
        sources, unknown = [], None
        for terms in term_sets:
            try:
                sources.append(self.related(terms))
            except UnknownFact as exc:
                unknown = unknown or exc
        if unknown is not None:
            raise unknown
        return sources

    def controlled_stamp(self):
        observed = self.visual_pdf({"controlled_stamp": "文件中存在真实盖上的受控章；只有“受控章/受控/盖章处”文字标签、空白盖章栏时不算。"}, existential_fields=("controlled_stamp",))
        fact = observed["controlled_stamp"]
        return ("PASS", "已从原件核实受控章。") if fact is True else ("NG", "原件没有受控章。") if fact is False else ("UNABLE", "受控章尚未核实，需人工核对。")
