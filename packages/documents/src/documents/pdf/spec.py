"""Typed configuration spec for PDF table extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureMode(str, Enum):
    BEST_EFFORT = "best_effort"
    STRICT = "strict"


class GroupingMode(str, Enum):
    ALL_PAGES = "all_pages"
    EXPLICIT_RANGES = "explicit_ranges"
    TITLE_SECTIONS = "title_sections"


class UnmatchedPagePolicy(str, Enum):
    ATTACH_PREVIOUS = "attach_previous"
    ERROR = "error"


class RegionMode(str, Enum):
    FULL_PAGE = "full_page"
    MARKERS_Y = "markers_y"
    FIXED_BOX = "fixed_box"


class StitchMode(str, Enum):
    STACK_ROWS = "stack_rows"
    NO_STITCH = "no_stitch"


class ColumnPolicy(str, Enum):
    FIRST_PAGE = "first_page"
    STRICT = "strict"
    PAD_TO_MAX = "pad_to_max"


class CrossCellTextSplitScope(str, Enum):
    HEADER_ONLY = "header_only"
    WHOLE_TABLE = "whole_table"


class CrossCellTextSplitStrategy(str, Enum):
    WORDS_THEN_CHAR = "words_then_char"


class OutputMode(str, Enum):
    SHEET_PER_GROUP = "sheet_per_group"


class SheetNameMode(str, Enum):
    PAGE_RANGE = "page_range"
    GROUP_LABEL = "group_label"


@dataclass(frozen=True)
class TitleSectionRule:
    label: str
    any_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()
    restart_any_of: tuple[str, ...] = ()
    restart_all_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupingSpec:
    mode: GroupingMode = GroupingMode.ALL_PAGES
    explicit_ranges: tuple[tuple[int, int], ...] = ()
    title_markers: tuple[str, ...] = ()
    stop_markers: tuple[str, ...] = ()
    section_rules: tuple[TitleSectionRule, ...] = ()
    unmatched_page_policy: UnmatchedPagePolicy = UnmatchedPagePolicy.ATTACH_PREVIOUS


@dataclass(frozen=True)
class RegionSpec:
    mode: RegionMode = RegionMode.FULL_PAGE
    start_markers: tuple[str, ...] = ()
    end_markers: tuple[str, ...] = ()
    fixed_box: tuple[float | None, float, float | None, float] | None = None
    fallback_to_full_page: bool = True


@dataclass(frozen=True)
class CrossCellTextSplitSpec:
    enabled: bool = False
    scope: CrossCellTextSplitScope = CrossCellTextSplitScope.WHOLE_TABLE
    strategy: CrossCellTextSplitStrategy = CrossCellTextSplitStrategy.WORDS_THEN_CHAR
    require_active_boundary: bool = True
    min_candidate_cells: int = 2
    min_fragment_overlap_ratio: float = 0.15
    keep_original_on_ambiguous: bool = True


@dataclass(frozen=True)
class TextCellAssignSpec:
    x_anchor_ratio: float = 0.5
    y_anchor_ratio: float = 0.2


@dataclass(frozen=True)
class ExtractionSpec:
    line_tolerance: float = 1.0
    merge_tolerance: float = 1.0
    text_tolerance: float = 1.0
    color_filter: dict[str, Any] | None = None
    cross_cell_text_split: CrossCellTextSplitSpec = field(default_factory=CrossCellTextSplitSpec)
    text_cell_assign: TextCellAssignSpec = field(default_factory=TextCellAssignSpec)


@dataclass(frozen=True)
class StitchSpec:
    mode: StitchMode = StitchMode.STACK_ROWS
    drop_repeated_header: bool = True
    column_policy: ColumnPolicy = ColumnPolicy.FIRST_PAGE
    split_on_large_y_gap: bool = True
    large_y_gap_ratio: float = 0.85
    split_multirow_value_by_neighbor_labels: bool = False
    row_split_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputSpec:
    mode: OutputMode = OutputMode.SHEET_PER_GROUP
    sheet_name_prefix: str = "Table"
    sheet_name_mode: SheetNameMode = SheetNameMode.PAGE_RANGE
    preserve_pdf_embedded_images: bool = False
    embedded_image_max_per_sheet: int = 24
    embedded_image_max_width: int = 900
    embedded_image_min_bytes: int = 256


@dataclass(frozen=True)
class PdfSpec:
    grouping: GroupingSpec = field(default_factory=GroupingSpec)
    region: RegionSpec = field(default_factory=RegionSpec)
    extraction: ExtractionSpec = field(default_factory=ExtractionSpec)
    stitch: StitchSpec = field(default_factory=StitchSpec)
    output: OutputSpec = field(default_factory=OutputSpec)
    failure_mode: FailureMode = FailureMode.BEST_EFFORT


DEFAULT_SPEC = PdfSpec(
    extraction=ExtractionSpec(
        text_cell_assign=TextCellAssignSpec(
            x_anchor_ratio=0.5,
            y_anchor_ratio=0.2,
        )
    )
)


def validate_pdf_spec(spec: PdfSpec) -> None:
    _require_enum(spec.grouping.mode, GroupingMode, "grouping.mode")
    _require_enum(spec.grouping.unmatched_page_policy, UnmatchedPagePolicy, "grouping.unmatched_page_policy")
    _require_enum(spec.region.mode, RegionMode, "region.mode")
    _require_enum(spec.stitch.mode, StitchMode, "stitch.mode")
    _require_enum(spec.stitch.column_policy, ColumnPolicy, "stitch.column_policy")
    _require_enum(spec.output.mode, OutputMode, "output.mode")
    _require_enum(spec.output.sheet_name_mode, SheetNameMode, "output.sheet_name_mode")
    _require_enum(spec.failure_mode, FailureMode, "failure_mode")
    _require_enum(spec.extraction.cross_cell_text_split.scope, CrossCellTextSplitScope, "extraction.cross_cell_text_split.scope")
    _require_enum(
        spec.extraction.cross_cell_text_split.strategy,
        CrossCellTextSplitStrategy,
        "extraction.cross_cell_text_split.strategy",
    )

    if not (0.0 <= spec.stitch.large_y_gap_ratio <= 1.0):
        raise ValueError("stitch.large_y_gap_ratio must be between 0.0 and 1.0")
    if spec.extraction.cross_cell_text_split.min_candidate_cells < 2:
        raise ValueError("extraction.cross_cell_text_split.min_candidate_cells must be >= 2")
    if not (0.0 < spec.extraction.cross_cell_text_split.min_fragment_overlap_ratio <= 1.0):
        raise ValueError("extraction.cross_cell_text_split.min_fragment_overlap_ratio must be between 0.0 and 1.0")
    if not (0.0 <= spec.extraction.text_cell_assign.x_anchor_ratio <= 1.0):
        raise ValueError("extraction.text_cell_assign.x_anchor_ratio must be between 0.0 and 1.0")
    if not (0.0 <= spec.extraction.text_cell_assign.y_anchor_ratio <= 1.0):
        raise ValueError("extraction.text_cell_assign.y_anchor_ratio must be between 0.0 and 1.0")
    if spec.output.embedded_image_max_per_sheet <= 0:
        raise ValueError("output.embedded_image_max_per_sheet must be > 0")
    if spec.output.embedded_image_max_width <= 0:
        raise ValueError("output.embedded_image_max_width must be > 0")
    if spec.output.embedded_image_min_bytes < 0:
        raise ValueError("output.embedded_image_min_bytes must be >= 0")

    if spec.grouping.mode == GroupingMode.EXPLICIT_RANGES and not spec.grouping.explicit_ranges:
        raise ValueError("grouping.explicit_ranges is required when grouping.mode=explicit_ranges")

    if spec.grouping.mode == GroupingMode.TITLE_SECTIONS and not spec.grouping.title_markers and not spec.grouping.section_rules:
        raise ValueError("grouping.title_markers or grouping.section_rules is required when grouping.mode=title_sections")

    for index, rule in enumerate(spec.grouping.section_rules, start=1):
        if not isinstance(rule, TitleSectionRule):
            raise ValueError(f"grouping.section_rules[{index}] must be TitleSectionRule")
        if not rule.label.strip():
            raise ValueError(f"grouping.section_rules[{index}].label must be non-empty")
        if not rule.any_of and not rule.all_of:
            raise ValueError(f"grouping.section_rules[{index}] must provide any_of or all_of")
        if rule.restart_any_of and not any(token.strip() for token in rule.restart_any_of):
            raise ValueError(f"grouping.section_rules[{index}].restart_any_of must contain at least one non-empty token")
        if rule.restart_all_of and not any(token.strip() for token in rule.restart_all_of):
            raise ValueError(f"grouping.section_rules[{index}].restart_all_of must contain at least one non-empty token")

    if spec.region.mode == RegionMode.MARKERS_Y:
        if not spec.region.start_markers or not spec.region.end_markers:
            raise ValueError("region.start_markers/end_markers are required when region.mode=markers_y")

    if spec.region.mode == RegionMode.FIXED_BOX and spec.region.fixed_box is None:
        raise ValueError("region.fixed_box is required when region.mode=fixed_box")


def _require_enum(value: object, enum_type: type, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be {enum_type.__name__}, got={value!r}")


__all__ = [
    "ColumnPolicy",
    "CrossCellTextSplitScope",
    "CrossCellTextSplitSpec",
    "CrossCellTextSplitStrategy",
    "DEFAULT_SPEC",
    "ExtractionSpec",
    "FailureMode",
    "GroupingMode",
    "GroupingSpec",
    "OutputMode",
    "OutputSpec",
    "PdfSpec",
    "RegionMode",
    "RegionSpec",
    "SheetNameMode",
    "StitchMode",
    "StitchSpec",
    "TextCellAssignSpec",
    "TitleSectionRule",
    "UnmatchedPagePolicy",
    "validate_pdf_spec",
]
