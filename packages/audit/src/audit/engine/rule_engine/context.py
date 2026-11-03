from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol

from openpyxl.worksheet.worksheet import Worksheet

from . import resolver
from .contracts import Decision, RuleResource, SheetModel
from .excel_ops import DefaultExcelOps

DEFAULT_NG_COLLECTION_TOPN = 5
MAX_CELL_READS = 32


def _column_name(column: int) -> str:
    letters = ""
    number = column
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters or "A"


class GuardStop(Exception):
    """Structured short-circuit used by typed rule context."""

    def __init__(self, decision: Decision, message: str) -> None:
        super().__init__(message)
        self.decision = decision
        self.message = message

    @property
    def status(self) -> str:
        if self.decision == Decision.UNABLE:
            return "UNABLE"
        return "ERR" if self.decision == Decision.ERR else "NG"


@dataclass(frozen=True, slots=True)
class ResolvedSection:
    ws: Worksheet
    section_name: str
    header_row: int


@dataclass(frozen=True, slots=True)
class ResolvedColumn:
    column_name: str
    header_col: int
    data_start_row: int


@dataclass(frozen=True, slots=True)
class ResolvedFieldBlock:
    block_name: str
    worksheets: tuple[Worksheet, ...]


@dataclass(frozen=True, slots=True)
class ResolvedField:
    field_name: str
    ws: Worksheet
    label_row: int
    label_col: int
    value_row: int | None
    value_col: int | None


class ExcelOps(Protocol):
    def iter_filled_cell_positions(self, ws: Worksheet, *, row_min: int, row_max: int, col_min: int, col_max: int) -> Iterator[tuple[int, int]]: ...

    def iter_effective_row_values(
        self,
        ws: Worksheet,
        row: int,
    ) -> Iterator[str]: ...

    def get_effective_cell_value(
        self,
        ws: Worksheet,
        row: int,
        col: int,
        strip: bool = True,
        lower: bool = False,
        remove_whitespace: bool = False,
    ) -> str: ...

    def is_filled(self, x: Any) -> bool: ...

    def get_detailed_merge_info(self, ws: Worksheet, row: int, col: int) -> Any: ...

    def get_checkbox_controls(
        self,
        ws: Worksheet,
        source_path: str | None = None,
    ) -> list[tuple[int, int, bool]]: ...

    def get_sheet_images_base64(self, ws: Worksheet) -> list[str]: ...

    def detect_sheet_stamp_signals(self, ws: Worksheet) -> tuple[bool, bool]: ...


class RuleContext:
    def __init__(
        self,
        ops: ExcelOps | None,
        workbook: Any,
        resource: RuleResource,
        source_path: str | None = None,
        *,
        file_version_id: str = "",
    ) -> None:
        self._ops = DefaultExcelOps() if ops is None else ops
        self._workbook = workbook
        self._resource = resource
        self._source_path = source_path
        self._sheet_cache: dict[str, Worksheet] = {}
        self._section_cache: dict[tuple[int, str], ResolvedSection] = {}
        self._column_cache: dict[tuple[int, str], tuple[dict[str, tuple[int, int]], set[str]]] = {}
        self._field_block_cache: dict[str, ResolvedFieldBlock] = {}
        self._field_cache: dict[tuple[str, str], ResolvedField | None] = {}
        self._group_cache: dict[tuple[int, str, str], tuple[tuple[int, str], ...]] = {}
        self._collect_ng_enabled = False
        self._collect_ng_topn = DEFAULT_NG_COLLECTION_TOPN
        self._collected_ng_messages: list[str] = []
        self._collected_ng_seen: set[str] = set()
        self._execution_count = 0
        self._quantifier: str | None = None
        self._scope_marked_complete = False
        self._scans_finished = 0
        self._scans_partial = 0
        self._scan_counts: list[tuple[int, bool]] = []
        self._witness = False
        self._unresolved: list[str] = []
        self._unresolved_seen: set[str] = set()
        self._cells: list[tuple[str, str, int, int]] = []
        self._cell_index: set[tuple[str, str, int, int]] = set()
        self._cell_excerpts: dict[tuple[str, str, int, int], str] = {}
        self._cells_truncated = False
        self._questions: list[dict[str, Any]] = []
        self._empty_scope: tuple[str, str] | None = None
        self._build_notes: list[tuple[str, str, str]] = []
        self._build_seen: set[tuple[str, str, str]] = set()
        self._file_version_id = str(file_version_id or "").strip()
        self._workbook_versions: dict[int, str] = {}
        self._version_workbooks: dict[str, Any] = {}

    @property
    def resource(self) -> RuleResource:
        return self._resource

    @property
    def ops(self) -> ExcelOps:
        return self._ops

    @property
    def workbook(self) -> Any:
        return self._workbook

    @property
    def source_path(self) -> str | None:
        return self._source_path

    @property
    def collect_ng_enabled(self) -> bool:
        return self._collect_ng_enabled

    @property
    def collect_ng_topn(self) -> int:
        return self._collect_ng_topn

    @property
    def collected_ng_messages(self) -> list[str]:
        return list(self._collected_ng_messages)

    @property
    def execution_count(self) -> int:
        return self._execution_count

    def record_execution(self) -> None:
        self._execution_count += 1

    def bind_workbook(self, workbook: Any, file_version_id: str) -> None:
        """Attribute later Fluent reads of this workbook to one file version."""
        version = str(file_version_id or "").strip()
        if not version or workbook is None:
            return
        self._workbook_versions[id(workbook)] = version
        self._version_workbooks[version] = workbook

    def version_for_worksheet(self, worksheet: Any) -> str:
        workbook = getattr(worksheet, "parent", None)
        if workbook is not None and id(workbook) in self._workbook_versions:
            return self._workbook_versions[id(workbook)]
        if workbook is self._workbook:
            return self._file_version_id
        return ""

    def note_cell(self, sheet: str, row: int, column: int, *, file_version_id: str = "", excerpt: str | None = None) -> None:
        """Remember one cell that exists on a bound workbook. The caller cannot supply the excerpt."""
        del excerpt
        title = str(sheet or "").strip()
        version = str(file_version_id or "").strip() or self._file_version_id
        if not title or type(row) is not int or type(column) is not int or row < 1 or column < 1 or not version:
            return
        workbook = self._version_workbooks.get(version)
        if workbook is None:
            return
        try:
            worksheet = workbook[title]
        except Exception:
            return
        if row > int(getattr(worksheet, "max_row", 0) or 0) or column > int(getattr(worksheet, "max_column", 0) or 0):
            return
        merged = self._ops.get_detailed_merge_info(worksheet, row, column)
        source = worksheet.cell(row=merged.min_row, column=merged.min_col) if merged else worksheet.cell(row=row, column=column)
        if getattr(source, "data_type", None) == "e":
            self.note_unresolved("单元格内容无法读取，不能当成已知值")
            stored = ""
        else:
            value = getattr(source, "value", None)
            stored = "" if value is None else str(value).replace("\n", " ").replace("\r", " ").strip()[:500]
        key = (version, title, row, column)
        if key in self._cell_index:
            return
        version_count = sum(1 for item in self._cells if item[0] == version)
        if version_count >= MAX_CELL_READS:
            for index, item in enumerate(self._cells):
                if item[0] != version:
                    continue
                dropped = self._cells.pop(index)
                self._cell_index.discard(dropped)
                self._cell_excerpts.pop(dropped, None)
                self._cells_truncated = True
                break
        self._cell_index.add(key)
        self._cells.append(key)
        self._cell_excerpts[key] = stored

    def cell_excerpt(self, version: str, sheet: str, row: int, column: int) -> str:
        return self._cell_excerpts.get((version, sheet, row, column), "")

    def cell_reads(self) -> tuple[tuple[str, str, int, int], ...]:
        return tuple(self._cells)

    def cell_reads_truncated(self) -> bool:
        return self._cells_truncated

    def require_all(self) -> None:
        """PASS needs the whole required range and every necessary judgment."""
        self._quantifier = "all"

    def require_one(self) -> None:
        """One valid witness can PASS. That is not a full scan."""
        self._quantifier = "one"

    @property
    def quantifier(self) -> str | None:
        return self._quantifier

    def mark_scope_complete(self) -> None:
        self._scope_marked_complete = True
        self.record_execution()

    def note_scan_finished(self, finished: bool) -> None:
        if finished:
            self._scans_finished += 1
            return
        self._scans_partial += 1
        self.note_unresolved("扫描未完成，未扫描部分不能当成已检查")

    def note_scan(self, *, yielded: int, finished: bool) -> None:
        """Record one row scan. A finished scan of no rows is an empty range."""
        self._scan_counts.append((yielded, finished))
        self.note_scan_finished(finished)

    def finished_scans_yielded_nothing(self) -> bool:
        if not self._scan_counts or any(not finished for _yielded, finished in self._scan_counts):
            return False
        return all(yielded == 0 for yielded, _finished in self._scan_counts)

    def has_scanned_row(self) -> bool:
        """A yielded row means the range was not empty, even without an assertion."""
        return any(yielded > 0 for yielded, _finished in self._scan_counts)

    def finished_scan_scope(self) -> dict[str, int] | None:
        """Rows from one finished scan. Another scan may overlap, so the counts cannot be added."""
        if len(self._scan_counts) != 1 or not self._scan_counts[0][1]:
            return None
        total = self._scan_counts[0][0]
        return {"total": total, "checked": total, "unknown": 0}

    def empty_range_confirmed(self) -> bool:
        """No yielded row and no unfinished scan. No scan at all is still an explicit empty declaration."""
        return not self.has_scanned_row() and all(finished for _yielded, finished in self._scan_counts)

    def scope_is_complete(self) -> bool:
        if self._scans_partial:
            return False
        if self._scope_marked_complete:
            return True
        return self._scans_finished > 0

    def has_partial_scan(self) -> bool:
        return self._scans_partial > 0

    def mark_witness(self) -> None:
        self._witness = True
        self.record_execution()

    def has_witness(self) -> bool:
        return self._witness

    def note_unresolved(self, message: str) -> None:
        text = str(message or "").strip()
        if not text or text in self._unresolved_seen:
            return
        self._unresolved_seen.add(text)
        self._unresolved.append(text)

    def has_unresolved(self) -> bool:
        return bool(self._unresolved)

    def unresolved_summary(self) -> str:
        if not self._unresolved:
            return ""
        total = len(self._unresolved)
        topn = self._collect_ng_topn if self._collect_ng_topn > 0 else DEFAULT_NG_COLLECTION_TOPN
        shown = "；".join(self._unresolved[:topn])
        text = f"未解问题：{shown}"
        if total > topn:
            text += f"（其余{total - topn}条省略）"
        return text

    def questions(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._questions)

    def read_sources(self) -> list[dict[str, Any]]:
        """Cells this check has read so far. A question keeps this set when the result is built."""
        return self._question_sources()

    def record_empty_scope(self, status: str, basis: str) -> None:
        """Keep an explicit empty-range result. This does not count as an assertion."""
        normalized = str(status or "").strip().upper()
        text = str(basis or "").strip()
        if normalized not in {"PASS", "NA", "UNABLE"} or not text:
            raise ValueError("空范围需要明确状态和非空依据")
        self._empty_scope = (normalized, text)

    def empty_scope(self) -> tuple[str, str] | None:
        return self._empty_scope

    def ask(self, question_id: str, question: str, candidates: tuple[tuple[str, str, str], ...] | list[tuple[str, str, str]]) -> None:
        """Record one user interpretation. This does not choose an answer or allow PASS."""
        qid = str(question_id or "").strip()
        text = str(question or "").strip()
        if not qid or not text:
            raise ValueError("问题需要编号和内容")
        parsed: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if len(candidate) != 3:
                raise ValueError("每个候选需要编号、名称和解释")
            cid, label, explanation = (str(part or "").strip() for part in candidate)
            if not cid or not label or not explanation or cid in seen:
                raise ValueError("候选编号、名称和解释都不能为空，且编号不能重复")
            seen.add(cid)
            parsed.append({"id": cid, "label": label, "explanation": explanation})
        if len(parsed) < 2:
            raise ValueError("待确认解释至少需要两个候选")
        current = next((item for item in self._questions if item["id"] == qid), None)
        if current is not None:
            if current["question"] != text or current["candidates"] != parsed:
                raise ValueError("同一问题的含义不一致")
            return
        self._questions.append({"id": qid, "question": text, "candidates": parsed, "sources": self._question_sources()})
        self.note_unresolved(text)

    def _question_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for version, sheet, row, column in self._cells:
            cell = _column_name(column) + str(row)
            sources.append(
                {
                    "document_id": version or self._file_version_id,
                    "locator": {"kind": "xlsx_cell", "sheet": sheet, "cell": cell},
                    "excerpt": self.cell_excerpt(version, sheet, row, column),
                }
            )
        return sources

    def note_build(self, *, kind: str, location: str, basis: str) -> None:
        """Keep a shared build fact. This is not an assertion and does not change status."""
        kind_text = str(kind or "").strip()
        location_text = str(location or "").strip()
        basis_text = str(basis or "").strip()
        if kind_text not in {"conflict", "exclusion"} or not location_text or not basis_text:
            return
        item = (kind_text, location_text, basis_text)
        if item in self._build_seen:
            return
        self._build_seen.add(item)
        self._build_notes.append(item)

    def build_notes(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(self._build_notes)

    def note_search(self, *, found: bool, exhausted: bool, label: str) -> None:
        """A search miss is not confirmed absence unless the range was exhausted."""
        self.record_execution()
        if found or exhausted:
            return
        self.note_unresolved(f"{label}搜索未完成，不能确认不存在")

    def enable_ng_collection(self, topn: int) -> None:
        self._collect_ng_enabled = True
        self._collect_ng_topn = topn if isinstance(topn, int) and topn > 0 else DEFAULT_NG_COLLECTION_TOPN
        self._collected_ng_messages.clear()
        self._collected_ng_seen.clear()
        self._execution_count = 0
        self._quantifier = None
        self._scope_marked_complete = False
        self._scans_finished = 0
        self._scans_partial = 0
        self._scan_counts.clear()
        self._witness = False
        self._unresolved.clear()
        self._unresolved_seen.clear()
        self._cells.clear()
        self._cell_index.clear()
        self._cell_excerpts.clear()
        self._cells_truncated = False
        self._questions.clear()
        self._empty_scope = None

    def record_ng(self, message: str) -> None:
        if not self._collect_ng_enabled:
            return
        text = str(message or "").strip()
        if not text or text in self._collected_ng_seen:
            return
        self._collected_ng_seen.add(text)
        self._collected_ng_messages.append(text)

    def has_collected_ng(self) -> bool:
        return bool(self._collected_ng_messages)

    def build_ng_summary(self, existing: str | None = None) -> str:
        merged_messages: list[str] = []
        seen: set[str] = set()

        def _append(message: str | None) -> None:
            text = str(message or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            merged_messages.append(text)

        _append(existing)
        for item in self._collected_ng_messages:
            _append(item)

        total = len(merged_messages)
        if total == 0:
            return ""

        topn = self._collect_ng_topn if self._collect_ng_topn > 0 else DEFAULT_NG_COLLECTION_TOPN
        numbered = [f"{index}) {message}" for index, message in enumerate(merged_messages[:topn], start=1)]
        summary = f"检测到{total}条不合规：" + "；".join(numbered)
        if total > topn:
            summary += f"（其余{total - topn}条省略）"
        return summary

    def _raise_or_ignore(self, decision: Decision, message: str) -> None:
        if decision == Decision.IGNORE:
            return
        raise GuardStop(decision, message)

    def _fail(self, decision: Decision, message: str) -> NoReturn:
        raise GuardStop(decision, message)

    def sheet(
        self,
        canonical_name: str,
    ) -> Worksheet:
        if canonical_name in self._sheet_cache:
            return self._sheet_cache[canonical_name]

        selected = self._resolve_sheet_candidates(
            canonical_name,
            on_missing=Decision.NG,
        )
        if len(selected) > 1:
            self._fail(
                Decision.NG,
                f"工作表[{canonical_name}]匹配到多个候选: {[sheet.title for sheet in selected]}",
            )

        ws = selected[0]
        self._sheet_cache[canonical_name] = ws
        return ws

    def find_sheets(
        self,
        canonical_name: str,
        *,
        on_missing: Decision = Decision.NG,
    ) -> list[Worksheet]:
        return self._resolve_sheet_candidates(
            canonical_name,
            on_missing=on_missing,
        )

    def section(
        self,
        ws: Worksheet,
        canonical_name: str,
    ) -> ResolvedSection:
        section_spec = self._resource.sections.get(canonical_name)
        if section_spec is None:
            self._fail(Decision.NG, f"未定义数据区规范: {canonical_name}")

        cache_key = (id(ws), canonical_name)
        if cache_key in self._section_cache:
            return self._section_cache[cache_key]

        if not any(
            candidate is ws
            for candidate in self._resolve_sheet_candidates(
                section_spec.sheet_name,
                on_missing=Decision.NG,
            )
        ):
            self._fail(
                Decision.NG,
                f"工作表[{ws.title}]不属于数据区[{canonical_name}]绑定的工作表[{section_spec.sheet_name}]",
            )

        try:
            header_row = resolver.find_header_row(
                self._ops,
                ws,
                keyword_map={anchor: self._resource.columns[anchor].aliases for anchor in section_spec.header_anchors},
                header_hit=section_spec.header_hit,
                max_row=section_spec.header_search_max_row,
                max_col=section_spec.header_search_max_col,
                compact_match=section_spec.header_compact_match,
            )
        except Exception as exc:
            self._fail(
                Decision.ERR,
                f"数据区定位异常[{canonical_name}]: {exc}",
            )

        if header_row is None:
            self._fail(Decision.NG, f"未定位到数据区[{canonical_name}]表头")

        resolved = ResolvedSection(ws=ws, section_name=canonical_name, header_row=header_row)
        self._section_cache[cache_key] = resolved
        return resolved

    def column(
        self,
        section: ResolvedSection,
        canonical_name: str,
    ) -> ResolvedColumn:
        resolved = self._resolve_column(section, canonical_name, on_missing=Decision.NG)
        if resolved is None:
            self._fail(Decision.NG, f"缺少列[{canonical_name}]")
        return resolved

    def optional_column(
        self,
        section: ResolvedSection,
        canonical_name: str,
    ) -> ResolvedColumn | None:
        return self._resolve_column(section, canonical_name, on_missing=Decision.IGNORE)

    def field_block(self, canonical_name: str) -> ResolvedFieldBlock:
        if canonical_name in self._field_block_cache:
            return self._field_block_cache[canonical_name]

        block_spec = self._resource.field_blocks.get(canonical_name)
        if block_spec is None:
            self._fail(Decision.NG, f"未定义字段块规范: {canonical_name}")

        sheets = tuple(
            self._resolve_sheet_candidates(
                block_spec.sheet_name,
                on_missing=Decision.NG,
            )
        )
        resolved = ResolvedFieldBlock(block_name=canonical_name, worksheets=sheets)
        self._field_block_cache[canonical_name] = resolved
        return resolved

    def field(
        self,
        block: ResolvedFieldBlock,
        canonical_name: str,
    ) -> ResolvedField:
        resolved = self._resolve_field(block, canonical_name, on_missing=Decision.NG)
        assert resolved is not None
        return resolved

    def optional_field(
        self,
        block: ResolvedFieldBlock,
        canonical_name: str,
    ) -> ResolvedField | None:
        return self._resolve_field(block, canonical_name, on_missing=Decision.IGNORE)

    def section_group_value(
        self,
        section: ResolvedSection,
        canonical_name: str,
        row_index: int,
    ) -> str | None:
        markers = self.section_group_markers(section, canonical_name)
        current: str | None = None
        for marker_row, marker_value in markers:
            if marker_row > row_index:
                break
            current = marker_value
        return current

    def section_group_markers(
        self,
        section: ResolvedSection,
        canonical_name: str,
    ) -> tuple[tuple[int, str], ...]:
        cache_key = (id(section.ws), section.section_name, canonical_name)
        if cache_key in self._group_cache:
            return self._group_cache[cache_key]

        section_spec = self._resource.sections.get(section.section_name)
        if section_spec is None:
            self._fail(Decision.NG, f"未定义数据区规范: {section.section_name}")

        group_spec = next(
            (group for group in section_spec.groups if group.canonical_name == canonical_name),
            None,
        )
        if group_spec is None:
            self._fail(
                Decision.NG,
                f"数据区[{section.section_name}]未定义逻辑分区[{canonical_name}]",
            )

        scan_columns: list[int] = []
        for source in group_spec.scan_columns:
            if isinstance(source, int):
                scan_columns.append(source)
                continue
            column = self._resolve_column(section, source, on_missing=Decision.NG)
            assert column is not None
            scan_columns.append(column.header_col)

        marker_map = {value.canonical_value: value.aliases for value in group_spec.values}
        conflicts: list[tuple[int, str, tuple[str, ...]]] = []

        try:
            markers = resolver.find_section_group_markers(
                self._ops,
                section.ws,
                scan_columns=tuple(scan_columns),
                marker_map=marker_map,
                conflicts=conflicts,
            )
        except Exception as exc:
            self._fail(
                Decision.ERR,
                f"逻辑分区定位异常[{canonical_name}]: {exc}",
            )

        for row_number, text, values in conflicts:
            joined = "、".join(values)
            self.note_build(
                kind="conflict",
                location=f"{section.ws.title}!{row_number}",
                basis=f"“{text}”同时匹配{joined}，不作为分区标记",
            )

        if not markers:
            self._fail(
                Decision.NG,
                f"未识别到逻辑分区[{canonical_name}]标记",
            )

        self._group_cache[cache_key] = markers
        return markers

    def _resolve_column(
        self,
        section: ResolvedSection,
        canonical_name: str,
        *,
        on_missing: Decision,
    ) -> ResolvedColumn | None:
        column_spec = self._resource.columns.get(canonical_name)
        if column_spec is None:
            self._raise_or_ignore(on_missing, f"未定义列规范: {canonical_name}")
            return None
        if column_spec.section_name != section.section_name:
            self._raise_or_ignore(
                on_missing,
                f"列[{canonical_name}]不属于数据区[{section.section_name}]",
            )
            return None

        try:
            mapping, missing = self._resolve_section_columns(section)
        except Exception as exc:
            self._raise_or_ignore(
                Decision.ERR,
                f"列定位异常[{canonical_name}]: {exc}",
            )
            return None

        if canonical_name in missing or canonical_name not in mapping:
            self._raise_or_ignore(on_missing, f"缺少列[{canonical_name}]")
            return None

        header_row, header_col = mapping[canonical_name]
        direction = self._resource.sections[section.section_name].direction
        data_start = resolver.data_start_row(
            self._ops,
            section.ws,
            header_row,
            header_col,
            direction=direction,
        )
        return ResolvedColumn(
            column_name=canonical_name,
            header_col=header_col,
            data_start_row=data_start,
        )

    def _resolve_field(
        self,
        block: ResolvedFieldBlock,
        canonical_name: str,
        *,
        on_missing: Decision,
    ) -> ResolvedField | None:
        field_spec = self._resource.fields.get(canonical_name)
        if field_spec is None:
            self._raise_or_ignore(on_missing, f"未定义字段规范: {canonical_name}")
            return None
        if field_spec.block_name != block.block_name:
            self._raise_or_ignore(
                on_missing,
                f"字段[{canonical_name}]不属于字段块[{block.block_name}]",
            )
            return None

        cache_key = (block.block_name, canonical_name)
        if cache_key in self._field_cache:
            cached = self._field_cache[cache_key]
            if cached is None:
                self._raise_or_ignore(on_missing, f"缺少字段[{canonical_name}]")
                return None
            return cached

        block_spec = self._resource.field_blocks[block.block_name]
        block_field_specs = tuple(spec for spec in self._resource.fields.values() if spec.block_name == block.block_name)
        blocked_aliases = tuple(alias for spec in block_field_specs for alias in spec.aliases)
        stop_tokens = tuple(alias for spec in block_field_specs if spec.canonical_name != canonical_name for alias in spec.aliases)
        missing_tokens = tuple(field_spec.missing_tokens) + tuple(self._resource.tokens.missing_tokens)
        value_reject_tokens = tuple(field_spec.value_reject_tokens)
        for ws in block.worksheets:
            try:
                label_cell = resolver.find_field_label(
                    self._ops,
                    ws,
                    aliases=field_spec.aliases,
                    max_row=block_spec.search_max_row,
                    max_col=block_spec.search_max_col,
                    match_mode=field_spec.label_match_mode,
                )
            except Exception as exc:
                self._raise_or_ignore(
                    Decision.ERR,
                    f"字段定位异常[{canonical_name}]: {exc}",
                )
                return None

            if label_cell is None:
                continue

            label_row, label_col = label_cell
            value_row: int | None = label_row
            value_col: int | None = label_col
            if field_spec.value_mode == "right":
                try:
                    value_cell = resolver.find_value_right_of_label(
                        self._ops,
                        ws,
                        row=label_row,
                        col=label_col,
                        scan_width=field_spec.scan_width,
                        blocked_aliases=blocked_aliases,
                        missing_tokens=missing_tokens,
                        value_reject_tokens=value_reject_tokens,
                        stop_tokens=stop_tokens,
                        stop_on_next_label=field_spec.stop_on_next_label,
                    )
                except Exception as exc:
                    self._raise_or_ignore(
                        Decision.ERR,
                        f"字段值定位异常[{canonical_name}]: {exc}",
                    )
                    return None
                if value_cell is None:
                    value_row = None
                    value_col = None
                else:
                    value_row, value_col = value_cell
            elif field_spec.value_mode == "left":
                try:
                    value_cell = resolver.find_value_left_of_label(
                        self._ops,
                        ws,
                        row=label_row,
                        col=label_col,
                        scan_width=field_spec.scan_width,
                        blocked_aliases=blocked_aliases,
                        missing_tokens=missing_tokens,
                        value_reject_tokens=value_reject_tokens,
                        stop_tokens=stop_tokens,
                        stop_on_next_label=field_spec.stop_on_next_label,
                    )
                except Exception as exc:
                    self._raise_or_ignore(
                        Decision.ERR,
                        f"字段值定位异常[{canonical_name}]: {exc}",
                    )
                    return None
                if value_cell is None:
                    value_row = None
                    value_col = None
                else:
                    value_row, value_col = value_cell
            elif field_spec.value_mode in {"below", "below_right"}:
                merged = self._ops.get_detailed_merge_info(ws, label_row, label_col)
                anchor_row = merged.max_row if merged else label_row
                anchor_min_col = merged.min_col if merged else label_col
                anchor_max_col = merged.max_col if merged else label_col
                candidate_row = anchor_row + 1
                candidate_col = anchor_min_col if field_spec.value_mode == "below" else (anchor_max_col + 1)
                if candidate_row > ws.max_row or candidate_col > ws.max_column:
                    value_row = None
                    value_col = None
                else:
                    value_row = candidate_row
                    value_col = candidate_col
            elif field_spec.value_mode == "right_band":
                try:
                    value_cell = resolver.find_value_in_right_band(
                        self._ops,
                        ws,
                        row=label_row,
                        col=label_col,
                        scan_width=field_spec.scan_width,
                        blocked_aliases=blocked_aliases,
                        missing_tokens=missing_tokens,
                        value_reject_tokens=value_reject_tokens,
                        stop_tokens=stop_tokens,
                        stop_on_next_label=field_spec.stop_on_next_label,
                    )
                except Exception as exc:
                    self._raise_or_ignore(
                        Decision.ERR,
                        f"字段值定位异常[{canonical_name}]: {exc}",
                    )
                    return None
                if value_cell is None:
                    value_row = None
                    value_col = None
                else:
                    value_row, value_col = value_cell

            resolved = ResolvedField(
                field_name=canonical_name,
                ws=ws,
                label_row=label_row,
                label_col=label_col,
                value_row=value_row,
                value_col=value_col,
            )
            self._field_cache[cache_key] = resolved
            return resolved

        self._field_cache[cache_key] = None
        self._raise_or_ignore(on_missing, f"缺少字段[{canonical_name}]")
        return None

    def _resolve_section_columns(
        self,
        section: ResolvedSection,
    ) -> tuple[dict[str, tuple[int, int]], set[str]]:
        cache_key = (id(section.ws), section.section_name)
        if cache_key in self._column_cache:
            return self._column_cache[cache_key]

        section_spec = self._resource.sections[section.section_name]
        columns_in_section = {name: spec.aliases for name, spec in self._resource.columns.items() if spec.section_name == section.section_name}
        mapping, missing = resolver.find_columns_near_header(
            self._ops,
            section.ws,
            header_row=section.header_row,
            keyword_map=columns_in_section,
            band=section_spec.column_band,
            max_col=section_spec.header_search_max_col,
            compact_match=section_spec.header_compact_match,
        )
        self._column_cache[cache_key] = (mapping, missing)
        return mapping, missing

    def _resolve_sheet_candidates(
        self,
        canonical_name: str,
        *,
        on_missing: Decision = Decision.NG,
    ) -> list[Worksheet]:
        sheet_spec = self._resource.sheets.get(canonical_name)

        if sheet_spec is None:
            self._raise_or_ignore(on_missing, f"未定义工作表规范: {canonical_name}")
            return []

        try:
            selected = self._select_worksheets_by_spec(sheet_spec)
        except Exception as exc:
            self._fail(Decision.ERR, f"工作表定位异常[{canonical_name}]: {exc}")

        if not selected:
            self._raise_or_ignore(on_missing, f"未找到工作表[{canonical_name}]")
            return []
        return list(selected)

    def _select_worksheets_by_spec(self, sheet_spec: SheetModel) -> list[Worksheet]:
        workbook = self._workbook
        if not hasattr(workbook, "sheetnames"):
            return []

        includes = tuple(keyword.lower() for keyword in sheet_spec.include_keywords if str(keyword).strip())
        if not includes:
            includes = (str(sheet_spec.canonical_name).strip().lower(),)
        excludes = [keyword.lower() for keyword in sheet_spec.exclude_keywords]
        match_mode = sheet_spec.match_mode

        def _is_visible(sheet_name: str) -> bool:
            if not sheet_spec.visible_only:
                return True
            return bool(workbook[sheet_name].sheet_state == "visible")

        def _is_include(sheet_name_lower: str) -> bool:
            if not includes:
                return True
            if match_mode == "contains":
                return any(keyword in sheet_name_lower for keyword in includes)
            return any(keyword == sheet_name_lower for keyword in includes)

        visible_candidates: list[Worksheet] = []
        selected: list[Worksheet] = []
        for sheet_name in workbook.sheetnames:
            if not _is_visible(sheet_name):
                continue
            sheet_name_lower = sheet_name.lower()
            if any(keyword in sheet_name_lower for keyword in excludes):
                continue
            ws = workbook[sheet_name]
            visible_candidates.append(ws)
            if not _is_include(sheet_name_lower):
                continue
            selected.append(ws)
        if selected:
            if sheet_spec.default_first and len(selected) > 1:
                return [selected[0]]
            return selected

        if sheet_spec.default_first:
            if visible_candidates:
                return [visible_candidates[0]]

        # Name-based include miss: allow a content-based fallback when a
        # section bound to this canonical sheet can be located on the worksheet.
        content_selected = [ws for ws in visible_candidates if self._matches_bound_section_header(sheet_spec.canonical_name, ws)]
        if content_selected:
            if sheet_spec.default_first and len(content_selected) > 1:
                return [content_selected[0]]
            return content_selected
        return selected

    def _matches_bound_section_header(self, canonical_sheet_name: str, ws: Worksheet) -> bool:
        section_specs = [spec for spec in self._resource.sections.values() if spec.sheet_name == canonical_sheet_name]
        if not section_specs:
            return False

        for section_spec in section_specs:
            try:
                keyword_map = {anchor: self._resource.columns[anchor].aliases for anchor in section_spec.header_anchors}
            except KeyError:
                continue
            try:
                header_row = resolver.find_header_row(
                    self._ops,
                    ws,
                    keyword_map=keyword_map,
                    header_hit=section_spec.header_hit,
                    max_row=section_spec.header_search_max_row,
                    max_col=section_spec.header_search_max_col,
                    compact_match=section_spec.header_compact_match,
                )
            except Exception:
                continue
            if header_row is not None:
                if section_spec.sheet_identity_anchors:
                    identity_map = {anchor: self._resource.columns[anchor].aliases for anchor in section_spec.sheet_identity_anchors}
                    _mapping, missing = resolver.find_columns_near_header(
                        self._ops,
                        ws,
                        header_row=header_row,
                        keyword_map=identity_map,
                        band=section_spec.column_band,
                        max_col=section_spec.header_search_max_col,
                        compact_match=section_spec.header_compact_match,
                    )
                    if missing:
                        continue
                return True
        return False
