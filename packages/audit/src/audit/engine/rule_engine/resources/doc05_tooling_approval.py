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

_DOC05_RESOURCE_MODEL = RuleResourceModel(
    sheets=(
        SheetModel(
            canonical_name="模具验收报告",
            include_keywords=("模具验收文件", "模具验收报告"),
            exclude_keywords=("变更",),
            match_mode="exact",
            visible_only=True,
        ),
    ),
    sections=(
        SectionModel(
            canonical_name="模具验收主数据区",
            sheet_name="模具验收报告",
            header_anchors=("工艺类型", "一级分总成零件名称", "模具制造供应商", "模具存放地点"),
            row_anchors=("工艺类型",),
            direction="down",
            header_hit=3,
            header_search_max_row=120,
            header_search_max_col=80,
            column_band=2,
            row_stop_on_first_blank=True,
            row_max_blank_streak=6,
            row_stop_markers=("编制edit", "编制"),
        ),
    ),
    columns=(
        ColumnModel(
            canonical_name="工艺类型",
            section_name="模具验收主数据区",
            aliases=("工艺类型",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="一级分总成零件名称",
            section_name="模具验收主数据区",
            aliases=("一级分总成零件名称",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="分总成组件或分序模具名称",
            section_name="模具验收主数据区",
            aliases=("分总成组件或分序模具名称",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具制造供应商",
            section_name="模具验收主数据区",
            aliases=("模具制造供应商",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具存放地点",
            section_name="模具验收主数据区",
            aliases=("模具存放地点",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="零件质量评估",
            section_name="模具验收主数据区",
            aliases=("零件质量评估",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具分供方及实物VS数据&标准验收",
            section_name="模具验收主数据区",
            aliases=("模具分供方及实物VS 数据&标准验收", "模具分供方及实物VS数据&标准验收"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具量产性能评估",
            section_name="模具验收主数据区",
            aliases=("模具量产性能评估",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具维保评估",
            section_name="模具验收主数据区",
            aliases=("模具维保评估",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="零件外观符合要求",
            section_name="模具验收主数据区",
            aliases=("零件外观符合要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="零件尺寸符合要求",
            section_name="模具验收主数据区",
            aliases=("零件尺寸符合要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="零件性能符合要求",
            section_name="模具验收主数据区",
            aliases=("零件性能符合要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具分供方是否符合IR报告要求",
            section_name="模具验收主数据区",
            aliases=("模具分供方是否符合IR报告要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具实物符合设计数据",
            section_name="模具验收主数据区",
            aliases=("模具实物符合设计数据",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具是否存在重大加工缺陷如违规烧焊等",
            section_name="模具验收主数据区",
            aliases=("模具是否存在重大加工缺陷如违规烧焊等",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="实物钢材选型符合模具标准",
            section_name="模具验收主数据区",
            aliases=("实物钢材选型符合模具标准",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="实物标准件符合模具标准",
            section_name="模具验收主数据区",
            aliases=("实物标准件(包含注塑模具热流道，冲压模具氮气弹簧等)符合模具标准",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具生产节拍符合IR要求",
            section_name="模具验收主数据区",
            aliases=("模具生产节拍符合IR要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具生产良率符合IR要求",
            section_name="模具验收主数据区",
            aliases=("模具生产良率符合IR要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具性能符合要求生产平顺无异常",
            section_name="模具验收主数据区",
            aliases=("模具性能符合要求，生产平顺无异常", "模具性能符合要求生产平顺无异常"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具总体产能符合IR要求",
            section_name="模具验收主数据区",
            aliases=("模具总体产能符合IR要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具维保手册齐全",
            section_name="模具验收主数据区",
            aliases=("模具维保手册齐全",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具数据保存完整",
            section_name="模具验收主数据区",
            aliases=("模具数据保存完整",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具备件准备充分",
            section_name="模具验收主数据区",
            aliases=("模具备件准备充分",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="模具设计尺寸",
            section_name="模具验收主数据区",
            aliases=("模具设计尺寸",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="实物照片",
            section_name="模具验收主数据区",
            aliases=("实物照片",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="实物确认",
            section_name="模具验收主数据区",
            aliases=("实物确认(有/无)", "实物确认"),
            missing_tokens=("", "none", "nan"),
        ),
    ),
    field_blocks=(
        FieldBlockModel(
            canonical_name="模具验收头部字段区",
            sheet_name="模具验收报告",
            search_max_row=120,
            search_max_col=40,
        ),
    ),
    fields=(
        FieldModel(
            canonical_name="零件供应商名称字段",
            block_name="模具验收头部字段区",
            aliases=("零件供应商名称(suppliername)", "零件供应商名称"),
            value_mode="right",
            scan_width=4,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="提交日期字段",
            block_name="模具验收头部字段区",
            aliases=("提交日期(submitdate)", "提交日期"),
            value_mode="right",
            scan_width=3,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="编制字段",
            block_name="模具验收头部字段区",
            aliases=("编制edit：", "编制edit:", "编制edit", "编制"),
            value_mode="right",
            scan_width=8,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="审核字段",
            block_name="模具验收头部字段区",
            aliases=("审核check：", "审核check:", "审核check", "审核"),
            value_mode="right",
            scan_width=8,
            missing_tokens=("", "none", "nan"),
        ),
    ),
)

_DOC05_RESOURCE_VIEW: RuleResource = index_rule_resource(_DOC05_RESOURCE_MODEL)


def get_doc05_resource_view() -> RuleResource:
    return _DOC05_RESOURCE_VIEW
