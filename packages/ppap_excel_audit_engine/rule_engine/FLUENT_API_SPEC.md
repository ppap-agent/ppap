# Fluent API Spec

This document captures the public, callable surface defined in `fluent.py`.

## Primary API (Current)

### `Table`

- `Table(ctx: RuleContext)`
- `sheet(sheet_name: str) -> Sheet`
- `field_block(block_name: str) -> FieldBlock`
- `find_sheets(sheet_name: str, *, on_missing: Decision = Decision.NG) -> list[Sheet]`

### `Sheet`

- `title -> str`
- `section(section_name: str) -> Section`
- `cell(row: int, col: int) -> Cell`
- `find_label(include_groups: tuple[tuple[str, ...], ...], *, row_max: int, col_max: int, exclude_tokens: tuple[str, ...] = ()) -> Cell | None`
- `checkbox_controls() -> list[tuple[int, int, bool]]`
- `images_base64() -> list[str]`
- `stamp_signals() -> tuple[bool, bool]`
- `has_seal() -> bool`
- `scan_non_empty(*, row_min: int, row_max: int, col_min: int, col_max: int) -> list[Cell]`

### `Cell`

- `row_index -> int`
- `col_index -> int`
- `value(compact: bool = False) -> Any`
- `number_format() -> str`
- `has_data() -> bool`
- `exact(expected: str, *, case_insensitive: bool = True, compact: bool = False) -> bool`
- `find_right(*, max_offset: int = 8, use_merged_anchor: bool = True) -> Cell | None`
- `scan_nearby(*, row_span: int = 2, col_span: int = 8) -> list[Cell]`

### `Section`

- `sheet_title -> str`
- `column(column_name: str) -> ResolvedColumn`
- `header_cell(column_name: str) -> Cell`
- `find_header_right(column_name: str, *, max_offset: int = 8, use_merged_anchor: bool = True) -> Cell | None`
- `columns(*required: str, optional: tuple[str, ...] = ()) -> dict[str, ResolvedColumn]`
- `first_value(column_name: str, *, lower: bool = False, compact: bool = True) -> str`
- `rows_from(columns: Mapping[str, ResolvedColumn]) -> Iterator[Row]`
- `rows(*required: str, optional: tuple[str, ...] = ()) -> Iterator[Row]`

### `Row`

- `sheet_title -> str`
- `is_header() -> bool`
- `cell(column_name: str) -> Any`
- `anchor(column_name: str, *, strict: bool = True) -> Cell | None`
- `find_right(column_name: str, *, max_offset: int = 8, use_merged_anchor: bool = True, strict: bool = True) -> Cell | None`
- `scan_nearby(column_name: str, *, row_span: int = 2, col_span: int = 8, strict: bool = True) -> list[Cell]`
- `value(column_name: str, compact: bool = False) -> Any`
- `has_data(column_name: str) -> bool`
- `is_present(column_name: str) -> bool`
- `exact(column_name: str, expected: str, *, case_insensitive: bool = True, compact: bool = False) -> bool`
- `count_present(*column_names: str) -> int`
- `missing(*column_names: str) -> list[str]`
- `expect(*column_names: str, context: str = "") -> Row`
- `expect_equal(column_name: str, expected: str, *, case_insensitive: bool = True, compact: bool = False, context: str = "") -> Row`
- `expect_condition(column_name: str, predicate: Callable[[Any], bool], *, context: str = "") -> Row`
- `group(group_name: str) -> str | None`
- `values_between(start_column_name: str, stop_before_column_name: str, *, present_only: bool = True, compact: bool = False, strict: bool = True) -> list[Any]`

### `FieldBlock`

- `field(field_name: str) -> Field`
- `value(field_name: str, compact: bool = False) -> Any`
- `exists(field_name: str) -> bool`
- `has_data(field_name: str) -> bool`
- `is_present(field_name: str) -> bool`
- `exact(field_name: str, expected: str, *, case_insensitive: bool = True, compact: bool = False) -> bool`
- `checked_count(field_name: str) -> int`
- `missing_fields(*field_names: str) -> list[str]`
- `expect(*field_names: str, context: str = "") -> FieldBlock`
- `expect_equal(field_name: str, expected: str, *, case_insensitive: bool = True, compact: bool = False, context: str = "") -> FieldBlock`
- `expect_condition(field_name: str, predicate: Callable[[Any], bool], *, context: str = "") -> FieldBlock`

### `Field`

- `exists() -> bool`
- `value(compact: bool = False) -> Any`
- `has_data() -> bool`
- `has_business_value() -> bool`
- `checked_count() -> int`
- `exact(expected: str, *, case_insensitive: bool = True, compact: bool = False) -> bool`

## Behavior Notes

- `value(..., compact=False)` returns the raw resolved value.
- `value(..., compact=True)` returns compacted text:
  - `None -> ""`
  - otherwise `''.join(str(value).split())`
  - no lower-casing is applied.
- Business presence uses token precedence:
  - explicit column/field `filled_tokens` / `missing_tokens`
  - then resource-level tokens
  - then default non-empty behavior.
- Row/field value reads honor merged-cell anchors.
- A section used for content-based sheet fallback may declare `sheet_identity_anchors`; all declared identity columns must resolve near the selected header before that worksheet can represent the canonical sheet. Name-matched worksheets keep the normal fail-closed section and column checks.
- Field labels use exact matching unless the resource declares `label_match_mode="contains"` for embedded narrative labels.
- Field `value_mode="right_band"` scans the columns immediately right of the label. For a vertical merged label it stays inside the merged row span; for a non-merged label it also checks the next row.
- Row stop markers scan every loaded effective worksheet value in the row, including merged anchors and distant populated columns, while ignoring `None`/style-only cells and the worksheet declared dimension.
- `values_between(..., present_only=True)` applies per-column spec semantics by position.
- `values_between(..., present_only=False)` applies physical non-empty semantics.
- `values_between(..., compact=True)` compacts each returned item using `''.join(str(value).split())` (`None -> ""`).

## Errors and Guarding

- Missing required sheet/section/column/field raises `GuardStop` according to context decision policy.
- `Section.rows_from(...)` raises `GuardStop(NG, ...)` when:
  - no business columns are provided
  - no anchor column can be resolved.
- `Sheet.images_base64()` and `Sheet.stamp_signals()` are fail-closed: worksheet image parsing or detector runtime failure returns empty/no-signal results and does not raise business `GuardStop`.
- `Sheet.images_base64()` / `Sheet.stamp_signals()` / `Sheet.has_seal()` are the stable image/seal API for agents. Agents must not access worksheet internals such as `ws._images` directly.
- Strict behavior defaults to fail-closed:
  - `Row.anchor/find_right/scan_nearby/values_between` with `strict=True` raise `GuardStop(NG, ...)` on missing required columns.
  - the same methods with `strict=False` return empty containers (`None`/`[]`) instead of raising business stop.
- `values_between(...)` raises `GuardStop(NG, ...)` for invalid start/stop boundaries (`stop <= start`).
