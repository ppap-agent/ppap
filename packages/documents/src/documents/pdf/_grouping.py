"""Page grouping helpers for PDF extraction."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Protocol

from .spec import GroupingMode, GroupingSpec, TitleSectionRule, UnmatchedPagePolicy


class PageTextExtractor(Protocol):
    def extract_pages_text(self, file_content: bytes) -> list[str]: ...


@dataclass(frozen=True)
class ResolvedPageGroup:
    pages: tuple[int, ...]
    label: str | None = None
    diagnostics: tuple[str, ...] = ()


def group_pages(
    file_content: bytes,
    *,
    page_count: int,
    grouping: GroupingSpec,
    pdf_extractor: PageTextExtractor,
) -> list[ResolvedPageGroup]:
    if page_count <= 0:
        return []

    if grouping.mode == GroupingMode.ALL_PAGES:
        return [ResolvedPageGroup(pages=tuple(range(page_count)))]

    if grouping.mode == GroupingMode.EXPLICIT_RANGES:
        return _group_explicit_ranges(grouping.explicit_ranges, page_count)

    if grouping.mode == GroupingMode.TITLE_SECTIONS:
        page_texts = pdf_extractor.extract_pages_text(file_content)
        if grouping.section_rules:
            return _group_title_sections_with_rules(page_texts, page_count, grouping.section_rules, grouping.unmatched_page_policy)
        return _group_title_sections(page_texts, page_count, grouping.title_markers, grouping.stop_markers)

    return [ResolvedPageGroup(pages=tuple(range(page_count)))]


def _group_explicit_ranges(explicit_ranges: Iterable[tuple[int, int]], page_count: int) -> list[ResolvedPageGroup]:
    groups: list[ResolvedPageGroup] = []
    for start, end in explicit_ranges:
        s = max(0, min(start, page_count - 1))
        e = max(0, min(end, page_count - 1))
        if e < s:
            s, e = e, s
        pages = tuple(range(s, e + 1))
        if pages:
            groups.append(ResolvedPageGroup(pages=pages))
    return groups


def _group_title_sections(
    page_texts: list[str],
    page_count: int,
    title_markers: tuple[str, ...],
    stop_markers: tuple[str, ...],
) -> list[ResolvedPageGroup]:
    groups: list[ResolvedPageGroup] = []
    current: list[int] = []

    for idx in range(page_count):
        text = page_texts[idx] if idx < len(page_texts) else ""
        has_title = any(marker in text for marker in title_markers)
        has_stop = any(marker in text for marker in stop_markers) if stop_markers else False

        if has_title:
            if current:
                groups.append(ResolvedPageGroup(pages=tuple(current)))
            current = [idx]
        elif current:
            current.append(idx)

        if current and has_stop:
            groups.append(ResolvedPageGroup(pages=tuple(current)))
            current = []

    if current:
        groups.append(ResolvedPageGroup(pages=tuple(current)))

    return [group for group in groups if group.pages]


def _group_title_sections_with_rules(
    page_texts: list[str],
    page_count: int,
    section_rules: tuple[TitleSectionRule, ...],
    unmatched_policy: UnmatchedPagePolicy,
) -> list[ResolvedPageGroup]:
    groups: list[ResolvedPageGroup] = []
    normalized_rules = [_normalize_rule(rule) for rule in section_rules]

    current_pages: list[int] = []
    current_label: str | None = None
    current_diagnostics: list[str] = []

    def flush_group() -> None:
        nonlocal current_pages, current_label, current_diagnostics
        if current_pages:
            groups.append(
                ResolvedPageGroup(
                    pages=tuple(current_pages),
                    label=current_label,
                    diagnostics=tuple(current_diagnostics),
                )
            )
        current_pages = []
        current_label = None
        current_diagnostics = []

    for page_index in range(page_count):
        text = page_texts[page_index] if page_index < len(page_texts) else ""
        normalized_text = _normalize_text(text)
        matched_rules = [rule for rule in normalized_rules if _matches_rule(normalized_text, rule)]
        matched_labels = [rule.label for rule in matched_rules]

        if matched_labels:
            selected_label = matched_labels[0]
            if current_pages and current_label == selected_label:
                selected_rule = matched_rules[0]
                if _matches_restart_rule(normalized_text, selected_rule):
                    flush_group()
                    current_pages = [page_index]
                    current_label = selected_label
                    if len(matched_labels) > 1:
                        current_diagnostics.append(f"page={page_index + 1} matched multiple section rules; selected='{selected_label}', candidates={matched_labels}")
                    continue
                current_pages.append(page_index)
                if len(matched_labels) > 1:
                    current_diagnostics.append(f"page={page_index + 1} matched multiple section rules; selected='{selected_label}', candidates={matched_labels}")
                continue

            flush_group()
            current_pages = [page_index]
            current_label = selected_label
            if len(matched_labels) > 1:
                current_diagnostics.append(f"page={page_index + 1} matched multiple section rules; selected='{selected_label}', candidates={matched_labels}")
            continue

        if unmatched_policy == UnmatchedPagePolicy.ERROR:
            raise ValueError(f"grouping unmatched page at page={page_index + 1} with unmatched_page_policy=error")

        if current_pages:
            current_pages.append(page_index)
            continue

        current_pages = [page_index]
        current_label = "Unknown"
        current_diagnostics.append(f"page={page_index + 1} did not match any section rule; started fallback Unknown group")

    flush_group()
    return [group for group in groups if group.pages]


@dataclass(frozen=True)
class _NormalizedRule:
    label: str
    any_of: tuple[str, ...]
    all_of: tuple[str, ...]
    restart_any_of: tuple[str, ...]
    restart_all_of: tuple[str, ...]


def _normalize_rule(rule: TitleSectionRule) -> _NormalizedRule:
    return _NormalizedRule(
        label=rule.label.strip(),
        any_of=tuple(_normalize_text(token) for token in rule.any_of if token.strip()),
        all_of=tuple(_normalize_text(token) for token in rule.all_of if token.strip()),
        restart_any_of=tuple(_normalize_text(token) for token in rule.restart_any_of if token.strip()),
        restart_all_of=tuple(_normalize_text(token) for token in rule.restart_all_of if token.strip()),
    )


def _matches_rule(normalized_text: str, rule: _NormalizedRule) -> bool:
    any_ok = True
    all_ok = True
    if rule.any_of:
        any_ok = any(token in normalized_text for token in rule.any_of)
    if rule.all_of:
        all_ok = all(token in normalized_text for token in rule.all_of)
    return any_ok and all_ok


def _matches_restart_rule(normalized_text: str, rule: _NormalizedRule) -> bool:
    if not rule.restart_any_of and not rule.restart_all_of:
        return False
    any_ok = True
    all_ok = True
    if rule.restart_any_of:
        any_ok = any(token in normalized_text for token in rule.restart_any_of)
    if rule.restart_all_of:
        all_ok = all(token in normalized_text for token in rule.restart_all_of)
    return any_ok and all_ok


_PUNCT_TRANSLATION = str.maketrans(
    {
        ",": " ",
        ".": " ",
        ";": " ",
        ":": " ",
        "!": " ",
        "?": " ",
        "，": " ",
        "。": " ",
        "；": " ",
        "：": " ",
        "！": " ",
        "？": " ",
        "、": " ",
        "（": " ",
        "）": " ",
        "(": " ",
        ")": " ",
        "【": " ",
        "】": " ",
        "[": " ",
        "]": " ",
        "《": " ",
        "》": " ",
        "<": " ",
        ">": " ",
        "-": " ",
        "_": " ",
        "/": " ",
        "\\": " ",
        "|": " ",
        '"': " ",
        "'": " ",
        "“": " ",
        "”": " ",
        "‘": " ",
        "’": " ",
        "·": " ",
    }
)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower().translate(_PUNCT_TRANSLATION)
    return " ".join(normalized.split())
