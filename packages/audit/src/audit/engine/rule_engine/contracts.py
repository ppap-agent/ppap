from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MatchMode = Literal["exact", "contains"]
Direction = Literal["up", "down"]
FieldValueMode = Literal["self", "right", "left", "below", "below_right", "right_band"]
MarkerSource = int | str


class Decision(str, Enum):
    NG = "NG"
    ERR = "ERR"
    UNABLE = "UNABLE"
    IGNORE = "IGNORE"


class SheetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    match_mode: MatchMode = "contains"
    visible_only: bool = False
    default_first: bool = False


class SectionGroupValueModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_value: str
    aliases: tuple[str, ...]

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("aliases must not be empty")
        seen: set[str] = set()
        deduped: list[str] = []
        for alias in value:
            text = alias.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(text)
        if not deduped:
            raise ValueError("aliases must contain at least one non-empty value")
        return tuple(deduped)


class SectionGroupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str
    scan_columns: tuple[MarkerSource, ...]
    values: tuple[SectionGroupValueModel, ...]

    @field_validator("scan_columns")
    @classmethod
    def _validate_scan_columns(cls, value: tuple[MarkerSource, ...]) -> tuple[MarkerSource, ...]:
        if not value:
            raise ValueError("scan_columns must not be empty")
        deduped: list[MarkerSource] = []
        seen: set[MarkerSource] = set()
        for item in value:
            candidate: MarkerSource
            if isinstance(item, int):
                if item < 1:
                    raise ValueError("scan_columns integer items must be >= 1")
                candidate = item
            else:
                text = item.strip()
                if not text:
                    continue
                candidate = text
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        if not deduped:
            raise ValueError("scan_columns must contain at least one usable source")
        return tuple(deduped)

    @field_validator("values")
    @classmethod
    def _validate_values(cls, value: tuple[SectionGroupValueModel, ...]) -> tuple[SectionGroupValueModel, ...]:
        if not value:
            raise ValueError("values must not be empty")
        seen: set[str] = set()
        for item in value:
            lowered = item.canonical_value.strip().lower()
            if not lowered:
                raise ValueError("group canonical_value must not be empty")
            if lowered in seen:
                raise ValueError(f"duplicate group canonical_value: {item.canonical_value}")
            seen.add(lowered)
        return value


class SectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str
    sheet_name: str
    header_anchors: tuple[str, ...]
    sheet_identity_anchors: tuple[str, ...] = ()
    row_anchors: tuple[str, ...]
    direction: Direction = "down"
    header_hit: int = Field(default=1, ge=1)
    header_search_max_row: int = Field(default=40, ge=1)
    header_search_max_col: int = Field(default=80, ge=1)
    header_compact_match: bool = False
    column_band: int = Field(default=2, ge=0)
    row_expand_merged_anchors: bool = False
    # "any": a row exists when any row anchor is filled, and each physical row is visited.
    row_anchor_match: Literal["first", "any"] = "first"
    row_stop_on_first_blank: bool = True
    row_max_blank_streak: int = Field(default=8, ge=1)
    row_stop_markers: tuple[str, ...] = ()
    groups: tuple[SectionGroupModel, ...] = ()

    @field_validator("header_anchors")
    @classmethod
    def _validate_header_anchors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("header_anchors must not be empty")
        seen: set[str] = set()
        anchors: list[str] = []
        for anchor in value:
            text = anchor.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            anchors.append(text)
        if not anchors:
            raise ValueError("header_anchors must contain at least one non-empty value")
        return tuple(anchors)

    @field_validator("row_anchors")
    @classmethod
    def _validate_row_anchors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("row_anchors must not be empty")
        seen: set[str] = set()
        anchors: list[str] = []
        for anchor in value:
            text = anchor.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            anchors.append(text)
        if not anchors:
            raise ValueError("row_anchors must contain at least one non-empty value")
        return tuple(anchors)

    @field_validator("sheet_identity_anchors")
    @classmethod
    def _validate_sheet_identity_anchors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        anchors: list[str] = []
        for anchor in value:
            text = anchor.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            anchors.append(text)
        return tuple(anchors)

    @field_validator("row_stop_markers")
    @classmethod
    def _validate_row_stop_markers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        markers: list[str] = []
        for marker in value:
            text = marker.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            markers.append(text)
        return tuple(markers)

    @model_validator(mode="after")
    def _validate_header_hit(self) -> "SectionModel":
        if self.header_hit > len(self.header_anchors):
            raise ValueError("header_hit must be <= len(header_anchors)")
        unknown_identity_anchors = set(self.sheet_identity_anchors) - set(self.header_anchors)
        if unknown_identity_anchors:
            raise ValueError(f"sheet_identity_anchors must be included in header_anchors: {sorted(unknown_identity_anchors)}")
        group_names: set[str] = set()
        for group in self.groups:
            lowered = group.canonical_name.strip().lower()
            if not lowered:
                raise ValueError("group canonical_name must not be empty")
            if lowered in group_names:
                raise ValueError(f"duplicate section group canonical_name: {group.canonical_name}")
            group_names.add(lowered)
        return self


class ColumnModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str
    section_name: str
    aliases: tuple[str, ...]
    missing_tokens: tuple[str, ...] = ()
    filled_tokens: tuple[str, ...] = ()

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("aliases must not be empty")
        seen: set[str] = set()
        deduped: list[str] = []
        for alias in value:
            text = alias.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(text)
        if not deduped:
            raise ValueError("aliases must contain at least one non-empty value")
        return tuple(deduped)

    @model_validator(mode="after")
    def _validate_token_conflicts(self) -> "ColumnModel":
        def _norm(tokens: tuple[str, ...]) -> set[str]:
            return {str(token).strip().lower() for token in tokens if str(token).strip()}

        filled = _norm(self.filled_tokens)
        missing = _norm(self.missing_tokens)
        overlap = filled & missing
        if overlap:
            raise ValueError(f"filled_tokens conflicts with missing_tokens: {sorted(overlap)}")
        return self


class FieldBlockModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str
    sheet_name: str
    search_max_row: int = Field(default=80, ge=1)
    search_max_col: int = Field(default=40, ge=1)


class FieldModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str
    block_name: str
    aliases: tuple[str, ...]
    label_match_mode: MatchMode = "exact"
    value_mode: FieldValueMode = "right"
    scan_width: int = Field(default=8, ge=1)
    missing_tokens: tuple[str, ...] = ()
    filled_tokens: tuple[str, ...] = ()
    checked_tokens: tuple[str, ...] = ()
    value_reject_tokens: tuple[str, ...] = ()
    stop_on_next_label: bool = True

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("aliases must not be empty")
        seen: set[str] = set()
        deduped: list[str] = []
        for alias in value:
            text = alias.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(text)
        if not deduped:
            raise ValueError("aliases must contain at least one non-empty value")
        return tuple(deduped)

    @model_validator(mode="after")
    def _validate_token_conflicts(self) -> "FieldModel":
        def _norm(tokens: tuple[str, ...]) -> set[str]:
            return {str(token).strip().lower() for token in tokens if str(token).strip()}

        filled = _norm(self.filled_tokens)
        missing = _norm(self.missing_tokens)
        overlap = filled & missing
        if overlap:
            raise ValueError(f"filled_tokens conflicts with missing_tokens: {sorted(overlap)}")
        return self


class DomainTokensModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    missing_tokens: tuple[str, ...] = ()
    filled_tokens: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_token_conflicts(self) -> "DomainTokensModel":
        filled = {str(token).strip().lower() for token in self.filled_tokens if str(token).strip()}
        missing = {str(token).strip().lower() for token in self.missing_tokens if str(token).strip()}
        overlap = filled & missing
        if overlap:
            raise ValueError(f"domain tokens conflict between filled/missing: {sorted(overlap)}")
        return self


class RuleResourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheets: tuple[SheetModel, ...]
    sections: tuple[SectionModel, ...]
    columns: tuple[ColumnModel, ...]
    field_blocks: tuple[FieldBlockModel, ...] = ()
    fields: tuple[FieldModel, ...] = ()
    tokens: DomainTokensModel = Field(default_factory=DomainTokensModel)

    @model_validator(mode="after")
    def _validate_references(self) -> "RuleResourceModel":
        sheet_names = [sheet.canonical_name for sheet in self.sheets]
        if len(sheet_names) != len(set(sheet_names)):
            raise ValueError("sheet canonical_name must be unique")

        section_names = [section.canonical_name for section in self.sections]
        if len(section_names) != len(set(section_names)):
            raise ValueError("section canonical_name must be unique")

        column_names = [column.canonical_name for column in self.columns]
        if len(column_names) != len(set(column_names)):
            raise ValueError("column canonical_name must be unique")

        field_block_names = [block.canonical_name for block in self.field_blocks]
        if len(field_block_names) != len(set(field_block_names)):
            raise ValueError("field block canonical_name must be unique")

        field_names = [field.canonical_name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("field canonical_name must be unique")

        sheet_set = set(sheet_names)
        for section in self.sections:
            if section.sheet_name not in sheet_set:
                raise ValueError(f"section references missing sheet: {section.sheet_name}")
        for block in self.field_blocks:
            if block.sheet_name not in sheet_set:
                raise ValueError(f"field block references missing sheet: {block.sheet_name}")

        section_set = set(section_names)
        columns_by_name = {column.canonical_name: column for column in self.columns}
        for column in self.columns:
            if column.section_name not in section_set:
                raise ValueError(f"column references missing section: {column.section_name}")

        for section in self.sections:
            for anchor in section.header_anchors:
                header_column = columns_by_name.get(anchor)
                if header_column is None:
                    raise ValueError(f"header anchor references missing column: {anchor}")
                if header_column.section_name != section.canonical_name:
                    raise ValueError(f"header anchor[{anchor}] does not belong to section[{section.canonical_name}]")
            for anchor in section.row_anchors:
                row_column = columns_by_name.get(anchor)
                if row_column is None:
                    raise ValueError(f"row anchor references missing column: {anchor}")
                if row_column.section_name != section.canonical_name:
                    raise ValueError(f"row anchor[{anchor}] does not belong to section[{section.canonical_name}]")
            for group in section.groups:
                for source in group.scan_columns:
                    if isinstance(source, int):
                        continue
                    scan_column = columns_by_name.get(source)
                    if scan_column is None:
                        raise ValueError(f"group scan column references missing column: {source}")
                    if scan_column.section_name != section.canonical_name:
                        raise ValueError(f"group scan column[{source}] does not belong to section[{section.canonical_name}]")

        field_block_set = set(field_block_names)
        for field in self.fields:
            if field.block_name not in field_block_set:
                raise ValueError(f"field references missing block: {field.block_name}")

        return self


@dataclass(frozen=True, slots=True)
class RuleResource:
    sheets: Mapping[str, SheetModel]
    sections: Mapping[str, SectionModel]
    columns: Mapping[str, ColumnModel]
    field_blocks: Mapping[str, FieldBlockModel]
    fields: Mapping[str, FieldModel]
    tokens: DomainTokensModel


def index_rule_resource(model: RuleResourceModel) -> RuleResource:
    sheets = MappingProxyType({sheet.canonical_name: sheet for sheet in model.sheets})

    columns = MappingProxyType({column.canonical_name: column for column in model.columns})

    sections = MappingProxyType({section.canonical_name: section for section in model.sections})

    field_blocks = MappingProxyType({block.canonical_name: block for block in model.field_blocks})

    fields = MappingProxyType({field.canonical_name: field for field in model.fields})

    return RuleResource(
        sheets=sheets,
        sections=sections,
        columns=columns,
        field_blocks=field_blocks,
        fields=fields,
        tokens=model.tokens,
    )
