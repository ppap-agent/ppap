from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from openpyxl.worksheet.worksheet import Worksheet

from .contracts import Direction, MatchMode, SectionModel

if TYPE_CHECKING:
    from .context import ExcelOps

_BOUNDARY_CHARS = set(" \t\r\n/\\-_—－:：,，;；()（）[]【】")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    # Normalize whitespace noise first so "完成 PPAP" and "完成   PPAP"
    # share the same token shape before downstream matching.
    return " ".join(str(value).split()).lower()


def _compact_text(value: str) -> str:
    return "".join(value.split())


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _normalize_aliases(aliases: tuple[str, ...]) -> list[str]:
    return [_normalize_text(alias) for alias in aliases if str(alias).strip()]


def _normalize_compact_aliases(aliases: tuple[str, ...]) -> list[str]:
    return [_compact_text(_normalize_text(alias)) for alias in aliases if str(alias).strip()]


def _normalize_compact_tokens(tokens: tuple[str, ...]) -> set[str]:
    return set(_normalize_compact_aliases(tokens))


def normalize_label_text(value: Any) -> str:
    text = str(value or "").replace("\n", "").replace("\r", "").strip().lower()
    for token in (
        " ",
        "\t",
        "\n",
        "\r",
        ":",
        "：",
        "(",
        ")",
        "（",
        "）",
        "-",
        "_",
        "/",
        "\\",
        "&",
        ".",
        ",",
        "，",
        ";",
        "；",
    ):
        text = text.replace(token, "")
    return text


def _read_cell_text(ops: ExcelOps, ws: Worksheet, row: int, col: int) -> str:
    return _normalize_text(
        ops.get_effective_cell_value(
            ws,
            row,
            col,
            strip=True,
            lower=False,
            remove_whitespace=False,
        )
    )


def _read_compact_cell_text(ops: ExcelOps, ws: Worksheet, row: int, col: int) -> str:
    return _compact_text(_read_cell_text(ops, ws, row, col))


def _contains_alias_with_boundary(text: str, alias: str) -> bool:
    if len(alias) < 2:
        return False
    start = 0
    while True:
        idx = text.find(alias, start)
        if idx < 0:
            return False
        left = text[idx - 1] if idx > 0 else ""
        right_idx = idx + len(alias)
        right = text[right_idx] if right_idx < len(text) else ""
        left_ok = idx == 0 or left in _BOUNDARY_CHARS
        right_ok = right_idx == len(text) or right in _BOUNDARY_CHARS
        if left_ok and right_ok:
            return True
        start = idx + 1


def _match_rank(
    text: str,
    aliases: list[str],
    *,
    compact_exact: bool = False,
) -> int:
    if not aliases:
        return 0
    if any(text == alias for alias in aliases):
        return 3
    if compact_exact:
        text_compact = _compact_text(text)
        compact_aliases = [_compact_text(alias) for alias in aliases]
        if text_compact and any(text_compact == alias for alias in compact_aliases):
            return 2
        # When compact matching is enabled, allow alias compact-form to appear
        # inside composite bilingual headers (e.g. "完成 PPAP ...").
        if text_compact and any(len(alias) >= 3 and alias in text_compact for alias in compact_aliases):
            return 1
    if any(_contains_alias_with_boundary(text, alias) for alias in aliases):
        return 2
    text_compact = _compact_text(text)
    if not text_compact:
        return 0
    for alias in aliases:
        # Loose contains is only enabled for non-CJK aliases. This keeps
        # Chinese headers strict while tolerating EN headers with spacing noise.
        if _has_cjk(alias):
            continue
        alias_compact = _compact_text(alias)
        if len(alias_compact) < 2:
            continue
        if alias_compact in text_compact:
            return 1
    return 0


def _is_better_row(
    current: tuple[int, int, int] | None,
    *,
    hits: int,
    score: int,
    row: int,
) -> bool:
    candidate = (hits, score, -row)
    return current is None or candidate > current


def _is_better_column_hit(
    current: tuple[int, int, int, int] | None,
    *,
    rank: int,
    header_row: int,
    row: int,
    col: int,
) -> bool:
    if current is None:
        return True
    prev_rank, prev_dist, prev_row, prev_col = current
    cur_dist = abs(row - header_row)
    return rank > prev_rank or (rank == prev_rank and (cur_dist < prev_dist or (cur_dist == prev_dist and (row < prev_row or (row == prev_row and col < prev_col)))))


def _best_anchor_rank_in_row(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    row: int,
    col_end: int,
    aliases: list[str],
    compact_exact: bool = False,
) -> int:
    best_rank = 0
    for col in range(1, col_end + 1):
        text = _read_cell_text(ops, ws, row, col)
        if not text:
            continue
        rank = _match_rank(text, aliases, compact_exact=compact_exact)
        if rank > best_rank:
            best_rank = rank
            if best_rank == 3:
                return best_rank
    return best_rank


def find_header_row(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    keyword_map: Mapping[str, tuple[str, ...]],
    header_hit: int,
    max_row: int,
    max_col: int,
    compact_match: bool = False,
) -> int | None:
    row_end = min(ws.max_row, max(1, int(max_row)))
    col_end = min(ws.max_column, max(1, int(max_col)))
    normalized_keyword_map = {name: _normalize_aliases(aliases) for name, aliases in keyword_map.items()}
    hit_threshold = min(header_hit, len(normalized_keyword_map))

    best_row: int | None = None
    best_score: tuple[int, int, int] | None = None
    for row in range(1, row_end + 1):
        row_hits = 0
        row_score = 0
        for aliases in normalized_keyword_map.values():
            rank = _best_anchor_rank_in_row(
                ops,
                ws,
                row=row,
                col_end=col_end,
                aliases=aliases,
                compact_exact=compact_match,
            )
            if rank > 0:
                row_hits += 1
                row_score += rank
        if row_hits < hit_threshold:
            continue
        if _is_better_row(best_score, hits=row_hits, score=row_score, row=row):
            best_score = (row_hits, row_score, -row)
            best_row = row
    return best_row


def find_columns_near_header(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    header_row: int,
    keyword_map: Mapping[str, tuple[str, ...]],
    band: int,
    max_col: int,
    compact_match: bool = False,
) -> tuple[dict[str, tuple[int, int]], set[str]]:
    row_start = max(1, header_row - max(0, band))
    row_end = min(ws.max_row, header_row + max(0, band))
    col_end = min(ws.max_column, max(1, int(max_col)))
    normalized_aliases = {name: _normalize_aliases(aliases) for name, aliases in keyword_map.items()}

    best_hits: dict[str, tuple[int, int, int, int]] = {}
    for row in range(row_start, row_end + 1):
        for col in range(1, col_end + 1):
            text = _read_cell_text(ops, ws, row, col)
            if not text:
                continue
            for name, aliases in normalized_aliases.items():
                if not aliases:
                    continue
                rank = _match_rank(text, aliases, compact_exact=compact_match)
                if rank == 0:
                    continue
                previous = best_hits.get(name)
                if _is_better_column_hit(
                    previous,
                    rank=rank,
                    header_row=header_row,
                    row=row,
                    col=col,
                ):
                    best_hits[name] = (rank, abs(row - header_row), row, col)

    mapping: dict[str, tuple[int, int]] = {}
    missing: set[str] = set()
    for name in keyword_map:
        hit = best_hits.get(name)
        if hit is None:
            missing.add(name)
            continue
        mapping[name] = (hit[2], hit[3])
    return mapping, missing


def find_field_label(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    aliases: tuple[str, ...],
    max_row: int,
    max_col: int,
    match_mode: MatchMode = "exact",
) -> tuple[int, int] | None:
    normalized_aliases = [alias.rstrip(":：") for alias in _normalize_compact_aliases(aliases)]
    normalized_aliases = [alias for alias in normalized_aliases if alias]
    if not normalized_aliases:
        return None

    row_end = min(ws.max_row, max(1, int(max_row)))
    col_end = min(ws.max_column, max(1, int(max_col)))
    contains_fallback: tuple[int, int] | None = None
    for row in range(1, row_end + 1):
        for col in range(1, col_end + 1):
            text = _read_compact_cell_text(ops, ws, row, col).rstrip(":：")
            if not text:
                continue
            if text in normalized_aliases:
                return row, col
            if match_mode == "contains" and any(alias in text for alias in normalized_aliases):
                if contains_fallback is None:
                    contains_fallback = (row, col)
    return contains_fallback


def find_label_cell(
    ops: ExcelOps,
    ws: Worksheet,
    include_groups: tuple[tuple[str, ...], ...],
    *,
    row_max: int,
    col_max: int,
    exclude_tokens: tuple[str, ...] = (),
) -> tuple[int, int] | None:
    if not include_groups:
        return None

    normalized_groups: list[tuple[str, ...]] = []
    for group in include_groups:
        normalized = tuple(normalize_label_text(token) for token in group if normalize_label_text(token))
        if normalized:
            normalized_groups.append(normalized)
    if not normalized_groups:
        return None

    normalized_excludes = tuple(normalize_label_text(token) for token in exclude_tokens if normalize_label_text(token))
    row_limit = min(ws.max_row, row_max)
    col_limit = min(ws.max_column, col_max)

    for row in range(1, row_limit + 1):
        row_text_parts: list[str] = []
        for col in range(1, col_limit + 1):
            cell_value = ops.get_effective_cell_value(
                ws,
                row,
                col,
                strip=False,
                lower=False,
                remove_whitespace=False,
            )
            normalized_cell = normalize_label_text(cell_value)
            if normalized_cell:
                row_text_parts.append(normalized_cell)
            if not normalized_cell:
                continue
            if any(token in normalized_cell for token in normalized_excludes):
                continue
            if any(all(anchor in normalized_cell for anchor in group) for group in normalized_groups):
                return (row, col)

        row_text = "".join(row_text_parts)
        if not row_text:
            continue
        if any(token in row_text for token in normalized_excludes):
            continue
        if any(all(anchor in row_text for anchor in group) for group in normalized_groups):
            for col in range(1, col_limit + 1):
                value = ops.get_effective_cell_value(
                    ws,
                    row,
                    col,
                    strip=True,
                    lower=False,
                    remove_whitespace=False,
                )
                if _compact_text(_normalize_text(value)) not in {"", "none", "nan"}:
                    return (row, col)
    return None


def _find_value_horizontally(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    row: int,
    start_col: int,
    end_col: int,
    step: int,
    blocked_aliases: tuple[str, ...],
    missing_tokens: tuple[str, ...] = (),
    value_reject_tokens: tuple[str, ...] = (),
    stop_tokens: tuple[str, ...] = (),
    stop_on_next_label: bool = True,
) -> tuple[int, int] | None:
    blocked = _normalize_compact_tokens(blocked_aliases)
    missing = _normalize_compact_tokens(missing_tokens)
    rejects = _normalize_compact_tokens(value_reject_tokens)
    stops = _normalize_compact_tokens(stop_tokens)
    if step == 0:
        raise ValueError("step must not be zero")
    range_end = end_col + (1 if step > 0 else -1)
    for candidate_col in range(start_col, range_end, step):
        text = _read_compact_cell_text(ops, ws, row, candidate_col)
        if not text:
            continue
        if stop_on_next_label and text in stops:
            break
        if text in blocked:
            continue
        if text in missing:
            continue
        if text in rejects:
            continue
        return row, candidate_col
    return None


def find_value_right_of_label(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    row: int,
    col: int,
    scan_width: int,
    blocked_aliases: tuple[str, ...],
    missing_tokens: tuple[str, ...] = (),
    value_reject_tokens: tuple[str, ...] = (),
    stop_tokens: tuple[str, ...] = (),
    stop_on_next_label: bool = True,
) -> tuple[int, int] | None:
    merged = ops.get_detailed_merge_info(ws, row, col)
    start_col = merged.max_col + 1 if merged else col + 1
    end_col = min(ws.max_column, start_col + max(1, int(scan_width)) - 1)
    return _find_value_horizontally(
        ops,
        ws,
        row=row,
        start_col=start_col,
        end_col=end_col,
        step=1,
        blocked_aliases=blocked_aliases,
        missing_tokens=missing_tokens,
        value_reject_tokens=value_reject_tokens,
        stop_tokens=stop_tokens,
        stop_on_next_label=stop_on_next_label,
    )


def find_value_left_of_label(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    row: int,
    col: int,
    scan_width: int,
    blocked_aliases: tuple[str, ...],
    missing_tokens: tuple[str, ...] = (),
    value_reject_tokens: tuple[str, ...] = (),
    stop_tokens: tuple[str, ...] = (),
    stop_on_next_label: bool = True,
) -> tuple[int, int] | None:
    merged = ops.get_detailed_merge_info(ws, row, col)
    end_col = (merged.min_col - 1) if merged else (col - 1)
    start_col = max(1, end_col - max(1, int(scan_width)) + 1)
    if end_col < 1:
        return None
    return _find_value_horizontally(
        ops,
        ws,
        row=row,
        start_col=end_col,
        end_col=start_col,
        step=-1,
        blocked_aliases=blocked_aliases,
        missing_tokens=missing_tokens,
        value_reject_tokens=value_reject_tokens,
        stop_tokens=stop_tokens,
        stop_on_next_label=stop_on_next_label,
    )


def find_value_in_right_band(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    row: int,
    col: int,
    scan_width: int,
    blocked_aliases: tuple[str, ...],
    missing_tokens: tuple[str, ...] = (),
    value_reject_tokens: tuple[str, ...] = (),
    stop_tokens: tuple[str, ...] = (),
    stop_on_next_label: bool = True,
) -> tuple[int, int] | None:
    """Find the first business value in the band immediately right of a label.

    A vertical merged label is restricted to its own row span, so a missing
    value cannot leak into the next task. A non-merged label also checks the
    next row to preserve the established below-right layout.
    """

    merged = ops.get_detailed_merge_info(ws, row, col)
    row_start = merged.min_row if merged else row
    row_end = min(ws.max_row, merged.max_row if merged else row + 1)
    start_col = (merged.max_col + 1) if merged else (col + 1)
    end_col = min(ws.max_column, start_col + max(1, int(scan_width)) - 1)
    if start_col > ws.max_column:
        return None

    for candidate_row in range(row_start, row_end + 1):
        candidate = _find_value_horizontally(
            ops,
            ws,
            row=candidate_row,
            start_col=start_col,
            end_col=end_col,
            step=1,
            blocked_aliases=blocked_aliases,
            missing_tokens=missing_tokens,
            value_reject_tokens=value_reject_tokens,
            stop_tokens=stop_tokens,
            stop_on_next_label=stop_on_next_label,
        )
        if candidate is not None:
            return candidate
    return None


def find_section_group_markers(
    ops: ExcelOps,
    ws: Worksheet,
    *,
    scan_columns: tuple[int, ...],
    marker_map: Mapping[str, tuple[str, ...]],
    conflicts: list[tuple[int, str, tuple[str, ...]]] | None = None,
) -> tuple[tuple[int, str], ...]:
    normalized_map = {canonical_value: _normalize_compact_aliases(aliases) for canonical_value, aliases in marker_map.items()}
    markers: list[tuple[int, str]] = []

    def _resolve_marker_value(cell_text: str) -> tuple[str | None, tuple[str, ...]]:
        best_value: str | None = None
        best_rank = 0
        best_alias_len = 0
        ambiguous = False
        tied: list[str] = []
        for canonical_value, aliases in normalized_map.items():
            value_rank = 0
            value_alias_len = 0
            for alias in aliases:
                rank = _match_rank(cell_text, [alias], compact_exact=True)
                if rank <= 0:
                    continue
                alias_len = len(alias)
                if rank > value_rank or (rank == value_rank and alias_len > value_alias_len):
                    value_rank = rank
                    value_alias_len = alias_len
            if value_rank <= 0:
                continue
            if (value_rank, value_alias_len) > (best_rank, best_alias_len):
                best_value = canonical_value
                best_rank = value_rank
                best_alias_len = value_alias_len
                tied = [canonical_value]
                ambiguous = False
            elif (value_rank, value_alias_len) == (best_rank, best_alias_len):
                ambiguous = True
                if best_value and best_value not in tied:
                    tied.append(best_value)
                if canonical_value not in tied:
                    tied.append(canonical_value)
        if ambiguous:
            return None, tuple(tied)
        return best_value, ()

    for row in range(1, ws.max_row + 1):
        matched_value: str | None = None
        for col in scan_columns:
            text = _read_compact_cell_text(ops, ws, row, col)
            if not text:
                continue
            matched_value, tied = _resolve_marker_value(text)
            if tied and conflicts is not None:
                conflicts.append((row, text, tied))
            if matched_value is not None:
                break
        if matched_value is not None:
            markers.append((row, matched_value))

    return tuple(markers)


def iter_anchor_rows(
    ops: ExcelOps,
    section_spec: SectionModel,
    ws: Worksheet,
    *,
    anchor_col: int,
    start_row: int,
    anchor_is_filled: Callable[[int], bool] | None = None,
    stop_row_predicate: Callable[[int], bool] | None = None,
) -> Iterator[int]:
    direction = section_spec.direction
    if direction not in {"down", "up"}:
        raise ValueError(f"Unsupported row direction: {direction}")

    row = max(1, min(ws.max_row, start_row))
    blank_streak = 0
    seen_data = False
    stop_on_blank = section_spec.row_stop_on_first_blank
    blank_limit = section_spec.row_max_blank_streak

    while 1 <= row <= ws.max_row:
        if stop_row_predicate is not None and stop_row_predicate(row):
            break

        if anchor_is_filled is not None:
            is_filled = anchor_is_filled(row)
        else:
            anchor_value = ws.cell(row=row, column=anchor_col).value
            is_filled = ops.is_filled(anchor_value)

        if is_filled:
            seen_data = True
            blank_streak = 0
            yield row
            expand = section_spec.row_expand_merged_anchors or section_spec.row_anchor_match == "any"
            merged = None if expand else ops.get_detailed_merge_info(ws, row, anchor_col)
            if direction == "down":
                row = (merged.max_row + 1) if merged else (row + 1)
            else:
                row = (merged.min_row - 1) if merged else (row - 1)
            continue

        if stop_on_blank and seen_data:
            break

        if seen_data:
            blank_streak += 1
            if blank_streak >= blank_limit:
                break
        row = row + 1 if direction == "down" else row - 1


def data_start_row(
    ops: ExcelOps,
    ws: Worksheet,
    header_row: int,
    header_col: int,
    *,
    direction: Direction = "down",
) -> int:
    merged = ops.get_detailed_merge_info(ws, header_row, header_col)
    if direction == "down":
        return (int(merged.max_row) + 1) if merged else (header_row + 1)
    if direction == "up":
        return (int(merged.min_row) - 1) if merged else (header_row - 1)
    raise ValueError(f"Unsupported row direction: {direction}")
