from __future__ import annotations

from ..contracts import (
    ColumnModel,
    RuleResource,
    RuleResourceModel,
    SectionModel,
    SheetModel,
    index_rule_resource,
)

_DOC12_RESOURCE_MODEL = RuleResourceModel(
    sheets=(
        SheetModel(
            canonical_name="主表",
            include_keywords=("验收清单", "开发计划"),
            exclude_keywords=("变更", "更新", "指南"),
            match_mode="contains",
            visible_only=False,
        ),
    ),
    sections=(
        SectionModel(
            canonical_name="主数据区",
            sheet_name="主表",
            header_anchors=("产线/工序", "设备开发", "设备验收", "验收结果", "整改措施", "整改计划"),
            row_anchors=("设备",),
            direction="down",
            header_hit=2,
            header_search_max_row=120,
            header_search_max_col=120,
            column_band=3,
            row_stop_on_first_blank=False,
            row_max_blank_streak=20,
            row_stop_markers=("编制/日期",),
        ),
    ),
    columns=(
        ColumnModel(
            canonical_name="产线/工序",
            section_name="主数据区",
            aliases=("产线/工序",),
            missing_tokens=("", "none", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="设备开发",
            section_name="主数据区",
            aliases=("设备开发",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="设备",
            section_name="主数据区",
            aliases=("设备",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="供应商",
            section_name="主数据区",
            aliases=("供应商",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="设备型号",
            section_name="主数据区",
            aliases=("设备型号",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="利旧设备数量",
            section_name="主数据区",
            aliases=("利旧设备数量",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="新采购设备数量",
            section_name="主数据区",
            aliases=("新采购设备数量",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="设备通用情况",
            section_name="主数据区",
            aliases=("设备通用情况",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="计划到位时间",
            section_name="主数据区",
            aliases=("计划到位时间",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="设备验收",
            section_name="主数据区",
            aliases=("设备验收",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="验收结果",
            section_name="主数据区",
            aliases=("验收结果",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="整改措施",
            section_name="主数据区",
            aliases=("整改措施",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="整改计划",
            section_name="主数据区",
            aliases=("整改计划",),
            missing_tokens=("", "none", "na", "nan"),
            filled_tokens=(),
        ),
    ),
)

_DOC12_RESOURCE_VIEW: RuleResource = index_rule_resource(_DOC12_RESOURCE_MODEL)


def get_doc12_resource_view() -> RuleResource:
    return _DOC12_RESOURCE_VIEW
