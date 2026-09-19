from __future__ import annotations

from ..contracts import (
    ColumnModel,
    FieldBlockModel,
    FieldModel,
    RuleResource,
    RuleResourceModel,
    SectionModel,
    SheetModel,
    index_rule_resource,
)

_DOC07_RESOURCE_MODEL = RuleResourceModel(
    sheets=(
        SheetModel(
            canonical_name="CCSC主表",
            include_keywords=("产品过程CCSC特性管理清单",),
            exclude_keywords=("模板",),
            match_mode="contains",
            visible_only=False,
        ),
        SheetModel(
            canonical_name="I-MR控制图",
            include_keywords=("I-MR单值移动控制图",),
            exclude_keywords=(),
            match_mode="contains",
            visible_only=False,
        ),
        SheetModel(
            canonical_name="Xbar-R控制图",
            include_keywords=("Xbar-R控制图",),
            exclude_keywords=(),
            match_mode="contains",
            visible_only=False,
        ),
        SheetModel(
            canonical_name="Xbar-S控制图",
            include_keywords=("Xbar-S控制图",),
            exclude_keywords=(),
            match_mode="contains",
            visible_only=False,
        ),
    ),
    sections=(
        SectionModel(
            canonical_name="CCSC数据区",
            sheet_name="CCSC主表",
            header_anchors=("工序流程编号", "P阶段Cpk结果", "P阶段结果判定"),
            row_anchors=("工序流程编号",),
            direction="down",
            header_hit=1,
            header_search_max_row=80,
            header_search_max_col=80,
            header_compact_match=True,
            column_band=2,
            row_stop_on_first_blank=True,
            row_max_blank_streak=8,
        ),
    ),
    columns=(
        ColumnModel(
            canonical_name="工序流程编号",
            section_name="CCSC数据区",
            aliases=("工序/流程编号", "工序流程编号"),
            missing_tokens=("", "none", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="P阶段Cpk结果",
            section_name="CCSC数据区",
            aliases=(
                "P阶段Cpk结果",
                "P阶段CPK结果",
                "P阶段\nCpk结果",
                "P阶段\nCPK结果",
            ),
            missing_tokens=("", "none", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="P阶段结果判定",
            section_name="CCSC数据区",
            aliases=("P阶段结果判定", "P阶段\n结果判定"),
            missing_tokens=("", "none", "nan"),
            filled_tokens=(),
        ),
    ),
    field_blocks=(
        FieldBlockModel(
            canonical_name="I-MR短期能力区",
            sheet_name="I-MR控制图",
            search_max_row=80,
            search_max_col=40,
        ),
        FieldBlockModel(
            canonical_name="Xbar-R短期能力区",
            sheet_name="Xbar-R控制图",
            search_max_row=80,
            search_max_col=40,
        ),
        FieldBlockModel(
            canonical_name="Xbar-S短期能力区",
            sheet_name="Xbar-S控制图",
            search_max_row=80,
            search_max_col=40,
        ),
    ),
    fields=(
        FieldModel(
            canonical_name="I-MR短期过程能力",
            block_name="I-MR短期能力区",
            aliases=("短期过程能力",),
            value_mode="self",
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="I-MR-Cpk",
            block_name="I-MR短期能力区",
            aliases=("Cpk", "cpk"),
            value_mode="right",
            scan_width=6,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="Xbar-R短期过程能力",
            block_name="Xbar-R短期能力区",
            aliases=("短期过程能力",),
            value_mode="self",
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="Xbar-R-Cpk",
            block_name="Xbar-R短期能力区",
            aliases=("Cpk", "cpk"),
            value_mode="right",
            scan_width=6,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="Xbar-S短期过程能力",
            block_name="Xbar-S短期能力区",
            aliases=("短期过程能力",),
            value_mode="self",
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="Xbar-S-Cpk",
            block_name="Xbar-S短期能力区",
            aliases=("Cpk", "cpk"),
            value_mode="right",
            scan_width=6,
            missing_tokens=("", "none", "nan"),
        ),
    ),
)

_DOC07_RESOURCE_VIEW: RuleResource = index_rule_resource(_DOC07_RESOURCE_MODEL)


def get_doc07_resource_view() -> RuleResource:
    return _DOC07_RESOURCE_VIEW
