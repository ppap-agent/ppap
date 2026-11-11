from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from . import resolver
from .context import (
    GuardStop,
    ResolvedColumn,
    ResolvedField,
    ResolvedFieldBlock,
    ResolvedSection,
    RuleContext,
)
from .contracts import Decision


def _text_exact_match(
    actual_raw: Any,
    expected: str,
    *,
    case_insensitive: bool = True,
    compact: bool = False,
) -> bool:
    actual = "" if actual_raw is None else str(actual_raw)
    expected_text = str(expected or "")
    if compact:
        actual = _RecordMixin._compact_text(actual)
        expected_text = _RecordMixin._compact_text(expected_text)
    else:
        actual = actual.strip()
        expected_text = expected_text.strip()
    if case_insensitive:
        return actual.lower() == expected_text.lower()
    return actual == expected_text


@dataclass(frozen=True, slots=True)
class Observation:
    """One read. Zero and False stay known values. Blank is not confirmed absence."""

    state: Literal["known", "blank", "missing", "unresolved"]
    value: Any = None


def classify_cell(value: Any) -> Observation:
    if value is None:
        return Observation("blank", None)
    if isinstance(value, str) and not value.strip():
        return Observation("blank", None)
    return Observation("known", value)


def _value_is_non_empty(value: Any, column_spec: Any, resource_tokens: Any) -> bool:
    lowered = _RecordMixin._normalize_text(value)
    if not lowered:
        return False
    if _RecordMixin._is_in(lowered, column_spec.filled_tokens):
        return True
    if _RecordMixin._is_in(lowered, column_spec.missing_tokens):
        return False
    if _RecordMixin._is_in(lowered, resource_tokens.filled_tokens):
        return True
    if _RecordMixin._is_in(lowered, resource_tokens.missing_tokens):
        return False
    return True


def _anchored_cell(ctx: RuleContext, ws: Any, row_index: int, col_index: int) -> Any:
    merged = ctx.ops.get_detailed_merge_info(ws, row_index, col_index)
    if merged:
        return ws.cell(row=merged.min_row, column=merged.min_col)
    return ws.cell(row=row_index, column=col_index)


def _remember_cell(ctx: RuleContext, ws: Any, cell: Any, *, allow_unresolved: bool = False) -> Any:
    unreadable = getattr(cell, "data_type", None) == "e"
    if unreadable:
        ctx.note_unreadable_cell()
    version = ctx.version_for_worksheet(ws)
    workbook = getattr(ws, "parent", None)
    if workbook is ctx.workbook or version:
        value = getattr(cell, "value", None)
        ctx.note_cell(
            str(getattr(ws, "title", "") or ""),
            int(getattr(cell, "row", 0) or 0),
            int(getattr(cell, "column", 0) or 0),
            file_version_id=version,
            excerpt=None if unreadable else _format_evidence_value(value),
        )
    if unreadable and not allow_unresolved:
        raise GuardStop(Decision.UNABLE, "单元格内容无法读取，不能当成已知值")
    return cell


def _observe_cell(ctx: RuleContext, ws: Any, cell: Any) -> Observation:
    remembered = _remember_cell(ctx, ws, cell, allow_unresolved=True)
    if getattr(remembered, "data_type", None) == "e":
        return Observation("unresolved", getattr(remembered, "value", None))
    return classify_cell(getattr(remembered, "value", None))


def _normalize_marker_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("\n", "").replace("\r", "")


def _format_evidence_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").strip()


def _raise_or_collect_ng(ctx: RuleContext, message: str) -> None:
    if ctx.collect_ng_enabled:
        ctx.record_ng(message)
        return
    raise GuardStop(Decision.NG, message)


@dataclass(frozen=True, slots=True)
class _RecordMixin:
    ctx: RuleContext
    ws: Any
    columns: Mapping[str, ResolvedColumn]

    def _column_spec(self, column_name: str) -> Any:
        return self.ctx.resource.columns[column_name]

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()

    @staticmethod
    def _compact_text(value: str) -> str:
        return "".join(value.split())

    @staticmethod
    @lru_cache(maxsize=512)
    def _token_set(tokens: tuple[str, ...]) -> frozenset[str]:
        return frozenset(str(token).strip().lower() for token in tokens if str(token).strip())

    @staticmethod
    def _is_in(value: str, tokens: tuple[str, ...]) -> bool:
        return value in _RecordMixin._token_set(tokens)

    def value(self, column_name: str, compact: bool = False) -> Any:
        self.ctx.record_execution()
        raw = self.cell(column_name).value
        if not compact:
            return raw
        if raw is None:
            return ""
        return self._compact_text(str(raw))

    def has_data(self, column_name: str) -> bool:
        return self.ctx.ops.is_filled(self.value(column_name))

    def is_present(self, column_name: str) -> bool:
        spec = self._column_spec(column_name)
        return _value_is_non_empty(self.value(column_name), spec, self.ctx.resource.tokens)

    def exact(
        self,
        column_name: str,
        expected: str,
        *,
        case_insensitive: bool = True,
        compact: bool = False,
    ) -> bool:
        return _text_exact_match(
            self.value(column_name),
            expected,
            case_insensitive=case_insensitive,
            compact=compact,
        )

    def count_present(self, *column_names: str) -> int:
        return sum(1 for name in column_names if self.is_present(name))

    def missing(self, *column_names: str) -> list[str]:
        return [name for name in column_names if not self.is_present(name)]

    def cell(self, column_name: str) -> Any:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Row(_RecordMixin):
    row_index: int
    section: ResolvedSection

    @property
    def sheet_title(self) -> str:
        return str(self.ws.title)

    def expect(self, *column_names: str, context: str = "") -> Row:
        if missing := self.missing(*column_names):
            ctx_str = f"‘{context}’对应行" if context else "该行"
            _raise_or_collect_ng(
                self.ctx,
                f"工作表 {self.sheet_title} 行号{self.row_index} {ctx_str}存在空值，空值列: {missing}",
            )
        return self

    def expect_equal(
        self,
        column_name: str,
        expected: str,
        *,
        case_insensitive: bool = True,
        compact: bool = False,
        context: str = "",
    ) -> Row:
        if not self.exact(column_name, expected, case_insensitive=case_insensitive, compact=compact):
            ctx_str = f"（{context}）" if context else ""
            actual_value = _format_evidence_value(self.value(column_name))
            _raise_or_collect_ng(
                self.ctx,
                f"工作表 {self.sheet_title} 行号{self.row_index}{ctx_str}列[{column_name}]命中值[{actual_value}]不等于期望值[{expected}]",
            )
        return self

    def expect_condition(self, column_name: str, predicate: Callable[[Any], bool], *, context: str = "") -> Row:
        value = self.value(column_name)
        if not predicate(value):
            condition_text = context or getattr(predicate, "__name__", "") or "断言条件"
            ctx_str = f"（{condition_text}）" if condition_text else ""
            value_text = _format_evidence_value(value)
            _raise_or_collect_ng(
                self.ctx,
                f"工作表 {self.sheet_title} 行号{self.row_index}{ctx_str}列[{column_name}]命中值[{value_text}]未满足规则条件[{condition_text}]",
            )
        return self

    def observation(self, column_name: str) -> Observation:
        self.ctx.record_execution()
        column = self._resolved_column(column_name, strict=False)
        if column is None:
            return Observation("missing", None)
        return _observe_cell(self.ctx, self.ws, _anchored_cell(self.ctx, self.ws, self.row_index, column.header_col))

    def cell(self, column_name: str) -> Any:
        column = self.columns[column_name]
        return _remember_cell(self.ctx, self.ws, _anchored_cell(self.ctx, self.ws, self.row_index, column.header_col))

    def _resolved_column(self, column_name: str, *, strict: bool = True) -> ResolvedColumn | None:
        column = self.columns.get(column_name)
        if column is not None:
            return column
        if strict:
            return self.ctx.column(self.section, column_name)
        return self.ctx.optional_column(self.section, column_name)

    def anchor(self, column_name: str, *, strict: bool = True) -> Cell | None:
        column = self._resolved_column(column_name, strict=strict)
        if column is None:
            return None
        return Cell(self.ctx, self.ws, self.row_index, column.header_col)

    def find_right(
        self,
        column_name: str,
        *,
        max_offset: int = 8,
        use_merged_anchor: bool = True,
        strict: bool = True,
    ) -> Cell | None:
        anchor = self.anchor(column_name, strict=strict)
        if anchor is None:
            return None
        return anchor.find_right(
            max_offset=max_offset,
            use_merged_anchor=use_merged_anchor,
        )

    def scan_nearby(
        self,
        column_name: str,
        *,
        row_span: int = 2,
        col_span: int = 8,
        strict: bool = True,
    ) -> list[Cell]:
        anchor = self.anchor(column_name, strict=strict)
        if anchor is None:
            return []
        return anchor.scan_nearby(
            row_span=row_span,
            col_span=col_span,
        )

    def is_header(self) -> bool:
        return self.row_index == self.section.header_row

    def group(self, group_name: str) -> str | None:
        return self.ctx.section_group_value(self.section, group_name, self.row_index)

    def values_between(
        self,
        start_column_name: str,
        stop_before_column_name: str,
        *,
        present_only: bool = True,
        compact: bool = False,
        strict: bool = True,
    ) -> list[Any]:
        start_column = self._resolved_column(start_column_name, strict=strict)
        stop_column = self._resolved_column(stop_before_column_name, strict=strict)
        if start_column is None or stop_column is None:
            return []
        if stop_column.header_col <= start_column.header_col:
            raise GuardStop(
                Decision.NG,
                (f"列带边界无效[{start_column_name}->{stop_before_column_name}] in 数据区[{self.section.section_name}]"),
            )

        seen_cells: set[tuple[int, int]] = set()
        values: list[Any] = []
        spec_by_col: dict[int, Any] = {column.header_col: self.ctx.resource.columns[column.column_name] for column in self.columns.values()}

        def _spec_for_col(col_index: int) -> Any | None:
            spec = spec_by_col.get(col_index)
            if spec is not None:
                return spec
            for column_name, column_spec in self.ctx.resource.columns.items():
                if column_spec.section_name != self.section.section_name:
                    continue
                resolved = self.ctx.optional_column(self.section, column_name)
                if resolved is None or resolved.header_col != col_index:
                    continue
                resolved_spec = self.ctx.resource.columns[resolved.column_name]
                spec_by_col[col_index] = resolved_spec
                return resolved_spec
            return None

        for col_index in range(start_column.header_col, stop_column.header_col):
            cell = _anchored_cell(self.ctx, self.ws, self.row_index, col_index)
            if cell.row == self.row_index and cell.column < start_column.header_col:
                continue
            cell_key = (cell.row, cell.column)
            if cell_key in seen_cells:
                continue
            seen_cells.add(cell_key)
            # Column-band reads have the same evidence and uncertainty
            # obligations as named-column reads.
            _remember_cell(self.ctx, self.ws, cell)
            raw_value = cell.value
            if present_only:
                spec = _spec_for_col(col_index)
                if spec is None:
                    if not self.ctx.ops.is_filled(raw_value):
                        continue
                elif not _value_is_non_empty(raw_value, spec, self.ctx.resource.tokens):
                    continue
            elif not self.ctx.ops.is_filled(raw_value):
                continue
            if not compact:
                values.append(raw_value)
                continue
            if raw_value is None:
                values.append("")
                continue
            values.append(self._compact_text(str(raw_value)))
        return values


@dataclass(frozen=True, slots=True)
class Field:
    ctx: RuleContext
    resolved: ResolvedField | None
    field_name: str

    def _field_spec(self) -> Any:
        return self.ctx.resource.fields[self.field_name]

    def exists(self) -> bool:
        self.ctx.record_execution()
        return self.resolved is not None

    def observation(self) -> Observation:
        self.ctx.record_execution()
        if self.resolved is None or self.resolved.value_row is None or self.resolved.value_col is None:
            return Observation("missing", None)
        return _observe_cell(
            self.ctx,
            self.resolved.ws,
            _anchored_cell(self.ctx, self.resolved.ws, self.resolved.value_row, self.resolved.value_col),
        )

    def value(self, compact: bool = False) -> Any:
        self.ctx.record_execution()
        if self.resolved is None:
            return "" if compact else None
        if self.resolved.value_row is None or self.resolved.value_col is None:
            return "" if compact else None
        raw = _remember_cell(
            self.ctx,
            self.resolved.ws,
            _anchored_cell(
                self.ctx,
                self.resolved.ws,
                self.resolved.value_row,
                self.resolved.value_col,
            ),
        ).value
        if not compact:
            return raw
        if raw is None:
            return ""
        return _RecordMixin._compact_text(str(raw))

    def has_data(self) -> bool:
        if self.resolved is None:
            return False
        return self.ctx.ops.is_filled(self.value())

    def has_business_value(self) -> bool:
        if self.resolved is None:
            return False
        spec = self._field_spec()
        return _value_is_non_empty(self.value(), spec, self.ctx.resource.tokens)

    def checked_count(self) -> int:
        if self.resolved is None:
            return 0
        value = self.value()
        text = "" if value is None else str(value)
        tokens = {token for token in self._field_spec().checked_tokens if str(token).strip()}
        if not tokens:
            return 0
        return sum(text.count(token) for token in tokens)

    def exact(self, expected: str, *, case_insensitive: bool = True, compact: bool = False) -> bool:
        return _text_exact_match(
            self.value(),
            expected,
            case_insensitive=case_insensitive,
            compact=compact,
        )


@dataclass(frozen=True, slots=True)
class FieldBlock:
    ctx: RuleContext
    resolved: ResolvedFieldBlock

    def field(self, field_name: str) -> Field:
        return Field(
            self.ctx,
            self.ctx.optional_field(self.resolved, field_name),
            field_name,
        )

    def value(self, field_name: str, compact: bool = False) -> Any:
        return self.field(field_name).value(compact=compact)

    def exists(self, field_name: str) -> bool:
        return self.field(field_name).exists()

    def has_data(self, field_name: str) -> bool:
        return self.field(field_name).has_data()

    def is_present(self, field_name: str) -> bool:
        return self.field(field_name).has_business_value()

    def exact(self, field_name: str, expected: str, *, case_insensitive: bool = True, compact: bool = False) -> bool:
        return self.field(field_name).exact(expected, case_insensitive=case_insensitive, compact=compact)

    def checked_count(self, field_name: str) -> int:
        return self.field(field_name).checked_count()

    def missing_fields(self, *field_names: str) -> list[str]:
        missing: list[str] = []
        for name in field_names:
            field = self.field(name)
            if not field.exists() or not field.has_business_value():
                missing.append(name)
        return missing

    def expect(self, *field_names: str, context: str = "") -> FieldBlock:
        if missing := self.missing_fields(*field_names):
            ctx_str = f"‘{context}’" if context else "表头/字段区"
            _raise_or_collect_ng(
                self.ctx,
                f"{ctx_str}存在缺失或空值的关键字段: {missing}",
            )
        return self

    def expect_equal(
        self,
        field_name: str,
        expected: str,
        *,
        case_insensitive: bool = True,
        compact: bool = False,
        context: str = "",
    ) -> FieldBlock:
        if not self.exact(field_name, expected, case_insensitive=case_insensitive, compact=compact):
            ctx_str = f"（{context}）" if context else ""
            actual_value = _format_evidence_value(self.value(field_name))
            _raise_or_collect_ng(
                self.ctx,
                f"字段区{ctx_str}字段[{field_name}]命中值[{actual_value}]不等于期望值[{expected}]",
            )
        return self

    def expect_condition(self, field_name: str, predicate: Callable[[Any], bool], *, context: str = "") -> FieldBlock:
        value = self.value(field_name)
        if not predicate(value):
            condition_text = context or getattr(predicate, "__name__", "") or "断言条件"
            ctx_str = f"（{condition_text}）" if condition_text else ""
            value_text = _format_evidence_value(value)
            _raise_or_collect_ng(
                self.ctx,
                f"字段区{ctx_str}字段[{field_name}]命中值[{value_text}]未满足规则条件[{condition_text}]",
            )
        return self


@dataclass(frozen=True, slots=True)
class Section:
    ctx: RuleContext
    resolved: ResolvedSection

    @property
    def sheet_title(self) -> str:
        return str(self.resolved.ws.title)

    def column(self, column_name: str) -> ResolvedColumn:
        return self.ctx.column(self.resolved, column_name)

    def header_cell(self, column_name: str) -> Cell:
        column = self.column(column_name)
        return Cell(self.ctx, self.resolved.ws, self.resolved.header_row, column.header_col)

    def find_header_right(
        self,
        column_name: str,
        *,
        max_offset: int = 8,
        use_merged_anchor: bool = True,
    ) -> Cell | None:
        return self.header_cell(column_name).find_right(
            max_offset=max_offset,
            use_merged_anchor=use_merged_anchor,
        )

    def columns(
        self,
        *required: str,
        optional: tuple[str, ...] = (),
    ) -> dict[str, ResolvedColumn]:
        resolved: dict[str, ResolvedColumn] = {}

        for name in required:
            resolved[name] = self.column(name)

        for name in optional:
            column = self.ctx.optional_column(self.resolved, name)
            if column is not None:
                resolved[name] = column

        return resolved

    def first_value(
        self,
        column_name: str,
        *,
        lower: bool = False,
        compact: bool = True,
    ) -> str:
        column_spec = self.ctx.resource.columns[column_name]
        alias_tokens = {_RecordMixin._compact_text(str(alias)).lower() for alias in column_spec.aliases if str(alias).strip()}
        columns = self.columns(column_name)
        for row in self.rows_from(columns):
            if not row.is_present(column_name):
                continue
            value = row.value(column_name, compact=compact)
            text = "" if value is None else str(value)
            if _RecordMixin._compact_text(text).lower() in alias_tokens:
                continue
            return text.lower() if lower else text
        return ""

    def rows_from(
        self,
        columns: Mapping[str, ResolvedColumn],
    ) -> Iterator[Row]:
        if not columns:
            raise GuardStop(
                Decision.NG,
                f"数据区[{self.resolved.section_name}]遍历行时至少需要一个业务列",
            )

        def _anchor_filled(row_index: int, anchor: ResolvedColumn) -> bool:
            cell = _remember_cell(self.ctx, self.resolved.ws, _anchored_cell(self.ctx, self.resolved.ws, row_index, anchor.header_col))
            spec = self.ctx.resource.columns[anchor.column_name]
            return _value_is_non_empty(cell.value, spec, self.ctx.resource.tokens)

        section_spec = self.ctx.resource.sections[self.resolved.section_name]
        anchor_col = next((columns[name] for name in section_spec.row_anchors if name in columns), None)
        if anchor_col is None:
            for name in section_spec.row_anchors:
                anchor_col = self.ctx.optional_column(self.resolved, name)
                if anchor_col is not None:
                    break
        if anchor_col is None:
            raise GuardStop(
                Decision.NG,
                f"数据区[{self.resolved.section_name}]未命中任何行锚点列: {list(section_spec.row_anchors)}",
            )

        anchor_cols = [anchor_col]
        if section_spec.row_anchor_match == "any":
            for name in section_spec.row_anchors:
                if name == anchor_col.column_name:
                    continue
                extra = columns[name] if name in columns else self.ctx.optional_column(self.resolved, name)
                if extra is not None:
                    anchor_cols.append(extra)

        scan_columns = dict(columns)
        for column in anchor_cols:
            scan_columns.setdefault(column.column_name, column)
        direction = section_spec.direction
        normalized_stop_markers = tuple(marker.strip().lower() for marker in section_spec.row_stop_markers if marker.strip())
        compact_alias_tokens_by_column: dict[str, tuple[str, ...]] = {}
        for column_name, resolved_column in scan_columns.items():
            column_spec = self.ctx.resource.columns[resolved_column.column_name]
            tokens = {_RecordMixin._compact_text(str(alias)).lower() for alias in column_spec.aliases if str(alias).strip()}
            canonical_compact = _RecordMixin._compact_text(str(column_name)).lower()
            if canonical_compact:
                tokens.add(canonical_compact)
            compact_alias_tokens_by_column[column_name] = tuple(sorted(tokens))

        anchor_tokens = compact_alias_tokens_by_column.get(anchor_col.column_name, ())

        def _row_is_repeated_header(row_index: int) -> bool:
            if not anchor_tokens:
                return False
            anchor_text = _RecordMixin._compact_text(
                _normalize_marker_text(
                    self.ctx.ops.get_effective_cell_value(
                        self.resolved.ws,
                        row_index,
                        anchor_col.header_col,
                        strip=True,
                        lower=False,
                        remove_whitespace=False,
                    )
                )
            ).lower()
            if not anchor_text:
                return False
            if not any(token and (anchor_text == token or token in anchor_text) for token in anchor_tokens):
                return False

            hit_count = 0
            for column_name, resolved_column in scan_columns.items():
                tokens = compact_alias_tokens_by_column.get(column_name, ())
                if not tokens:
                    continue
                cell_text = _RecordMixin._compact_text(
                    _normalize_marker_text(
                        self.ctx.ops.get_effective_cell_value(
                            self.resolved.ws,
                            row_index,
                            resolved_column.header_col,
                            strip=True,
                            lower=False,
                            remove_whitespace=False,
                        )
                    )
                ).lower()
                if not cell_text:
                    continue
                if any(token and (cell_text == token or token in cell_text) for token in tokens):
                    hit_count += 1
            return hit_count >= min(2, len(scan_columns))

        def _row_hits_stop_marker(row_index: int) -> bool:
            if not normalized_stop_markers:
                return False
            for value in self.ctx.ops.iter_effective_row_values(self.resolved.ws, row_index):
                cell_text = _normalize_marker_text(value)
                if not cell_text:
                    continue
                if any(marker in cell_text for marker in normalized_stop_markers):
                    return True
            return False

        start_row = max(column.data_start_row for column in scan_columns.values()) if direction == "down" else min(column.data_start_row for column in scan_columns.values())
        finished = False
        yielded = 0
        try:
            for row_index in resolver.iter_anchor_rows(
                self.ctx.ops,
                section_spec,
                self.resolved.ws,
                anchor_col=anchor_col.header_col,
                start_row=start_row,
                anchor_is_filled=lambda row_idx: any(_anchor_filled(row_idx, column) for column in anchor_cols),
                stop_row_predicate=(_row_hits_stop_marker if normalized_stop_markers else None),
            ):
                if _row_is_repeated_header(row_index):
                    continue
                yielded += 1
                yield Row(self.ctx, self.resolved.ws, columns, row_index, self.resolved)
            finished = True
        finally:
            self.ctx.note_scan(yielded=yielded, finished=finished)

    def rows(
        self,
        *required: str | Mapping[str, ResolvedColumn],
        optional: tuple[str, ...] = (),
    ) -> Iterator[Row]:
        if any(isinstance(item, Mapping) for item in required):
            raise TypeError("rows(...) expects only column names; use rows_from(columns)")

        required_names = tuple(str(name) for name in required)
        return self.rows_from(self.columns(*required_names, optional=optional))


@dataclass(frozen=True, slots=True)
class Sheet:
    ctx: RuleContext
    ws: Any

    @property
    def title(self) -> str:
        return str(self.ws.title)

    def section(self, section_name: str) -> Section:
        section = self.ctx.section(self.ws, section_name)
        return Section(self.ctx, section)

    def cell(self, row: int, col: int) -> Cell:
        return Cell(self.ctx, self.ws, row, col)

    def find_label(
        self,
        include_groups: tuple[tuple[str, ...], ...],
        *,
        row_max: int,
        col_max: int,
        exclude_tokens: tuple[str, ...] = (),
    ) -> Cell | None:
        row_col = resolver.find_label_cell(
            self.ctx.ops,
            self.ws,
            include_groups,
            row_max=row_max,
            col_max=col_max,
            exclude_tokens=exclude_tokens,
        )
        if row_col is None:
            return None
        row, col = row_col
        return Cell(self.ctx, self.ws, row, col)

    def checkbox_controls(self) -> list[tuple[int, int, bool]]:
        return self.ctx.ops.get_checkbox_controls(
            self.ws,
            source_path=self.ctx.source_path,
        )

    def images_base64(self) -> list[str]:
        return self.ctx.ops.get_sheet_images_base64(self.ws)

    def stamp_signals(self) -> tuple[bool, bool]:
        return self.ctx.ops.detect_sheet_stamp_signals(self.ws)

    def has_seal(self) -> bool:
        has_seal, _has_signature_or_stamp = self.stamp_signals()
        return has_seal

    def scan_non_empty(
        self,
        *,
        row_min: int,
        row_max: int,
        col_min: int,
        col_max: int,
    ) -> list[Cell]:
        bounded_row_min = max(1, row_min)
        bounded_row_max = min(self.ws.max_row, row_max)
        bounded_col_min = max(1, col_min)
        bounded_col_max = min(self.ws.max_column, col_max)
        if bounded_row_min > bounded_row_max or bounded_col_min > bounded_col_max:
            return []

        return [
            Cell(self.ctx, self.ws, row, col)
            for row, col in self.ctx.ops.iter_filled_cell_positions(self.ws, row_min=bounded_row_min, row_max=bounded_row_max, col_min=bounded_col_min, col_max=bounded_col_max)
        ]


@dataclass(frozen=True, slots=True)
class Cell:
    ctx: RuleContext
    ws: Any
    row_index: int
    col_index: int

    def _raw_value(self) -> Any:
        return _remember_cell(
            self.ctx,
            self.ws,
            _anchored_cell(
                self.ctx,
                self.ws,
                self.row_index,
                self.col_index,
            ),
        ).value

    def value(self, compact: bool = False) -> Any:
        self.ctx.record_execution()
        raw = self._raw_value()
        if not compact:
            return raw
        if raw is None:
            return ""
        return _RecordMixin._compact_text(str(raw))

    def number_format(self) -> str:
        anchored = _anchored_cell(
            self.ctx,
            self.ws,
            self.row_index,
            self.col_index,
        )
        return str(getattr(anchored, "number_format", "") or "")

    def has_data(self) -> bool:
        return self.ctx.ops.is_filled(self._raw_value())

    def exact(self, expected: str, *, case_insensitive: bool = True, compact: bool = False) -> bool:
        self.ctx.record_execution()
        return _text_exact_match(
            self._raw_value(),
            expected,
            case_insensitive=case_insensitive,
            compact=compact,
        )

    def find_right(
        self,
        *,
        max_offset: int = 8,
        use_merged_anchor: bool = True,
    ) -> Cell | None:
        start_col = self.col_index + 1
        if use_merged_anchor:
            merged = self.ctx.ops.get_detailed_merge_info(
                self.ws,
                self.row_index,
                self.col_index,
            )
            if merged:
                start_col = merged.max_col + 1

        for offset in range(max_offset + 1):
            col = start_col + offset
            if col > self.ws.max_column:
                break
            candidate = Cell(self.ctx, self.ws, self.row_index, col)
            if candidate.has_data():
                return candidate
        return None

    def scan_nearby(
        self,
        *,
        row_span: int = 2,
        col_span: int = 8,
    ) -> list[Cell]:
        row_end = min(self.ws.max_row, self.row_index + row_span)
        col_end = min(self.ws.max_column, self.col_index + col_span)
        col_start = max(1, self.col_index - 1)
        results: list[Cell] = []
        for row in range(self.row_index, row_end + 1):
            for col in range(col_start, col_end + 1):
                if row == self.row_index and col <= self.col_index:
                    continue
                candidate = Cell(self.ctx, self.ws, row, col)
                if candidate.has_data():
                    results.append(candidate)
        return results


class Table:
    def __init__(self, ctx: RuleContext) -> None:
        self._ctx = ctx

    def sheet(self, sheet_name: str) -> Sheet:
        ws = self._ctx.sheet(sheet_name)
        return Sheet(self._ctx, ws)

    def field_block(self, block_name: str) -> FieldBlock:
        block = self._ctx.field_block(block_name)
        return FieldBlock(self._ctx, block)

    def find_sheets(
        self,
        sheet_name: str,
        *,
        on_missing: Decision = Decision.NG,
    ) -> list[Sheet]:
        resolved = self._ctx.find_sheets(
            sheet_name,
            on_missing=on_missing,
        )
        return [Sheet(self._ctx, ws) for ws in resolved]
