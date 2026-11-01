from __future__ import annotations

from ..contracts import (
    ColumnModel,
    DomainTokensModel,
    FieldBlockModel,
    FieldModel,
    RuleResource,
    RuleResourceModel,
    SectionModel,
    SheetModel,
    index_rule_resource,
)

_DOC02_1_RESOURCE_MODEL = RuleResourceModel(
    sheets=(
        SheetModel(
            canonical_name="DVP&R工作表",
            include_keywords=("dvp&r", "试验项目", "子计划", "工作表", "table"),
            exclude_keywords=(),
            match_mode="contains",
            visible_only=False,
        ),
    ),
    sections=(
        SectionModel(
            canonical_name="DVP&R主数据区",
            sheet_name="DVP&R工作表",
            header_anchors=("序号", "试验项目", "试验要求", "试验类型"),
            row_anchors=("序号",),
            direction="down",
            header_hit=2,
            header_search_max_row=120,
            header_search_max_col=80,
            header_compact_match=True,
            column_band=2,
            row_stop_on_first_blank=False,
            row_max_blank_streak=200,
            row_stop_markers=("职责说明",),
        ),
    ),
    columns=(
        ColumnModel(
            canonical_name="序号",
            section_name="DVP&R主数据区",
            aliases=("序号", "序列号", "NO", "No", "No."),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验项目",
            section_name="DVP&R主数据区",
            aliases=("试验项目",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验要求",
            section_name="DVP&R主数据区",
            aliases=("试验要求",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验标准方法",
            section_name="DVP&R主数据区",
            aliases=("试验标准/方法", "试验标准方法"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验类型",
            section_name="DVP&R主数据区",
            aliases=("试验类型",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="完成节点",
            section_name="DVP&R主数据区",
            aliases=("完成节点",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验地点",
            section_name="DVP&R主数据区",
            aliases=("试验地点",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="数量",
            section_name="DVP&R主数据区",
            aliases=("数量",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验样本描述",
            section_name="DVP&R主数据区",
            aliases=("试验样本描述", "单次样本量", "样品数量", "样本数量"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="状态",
            section_name="DVP&R主数据区",
            aliases=("状态",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="外观属性",
            section_name="DVP&R主数据区",
            aliases=("外观属性",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="计划开始",
            section_name="DVP&R主数据区",
            aliases=("计划开始",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="计划结束",
            section_name="DVP&R主数据区",
            aliases=("计划结束",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验实际开始日期",
            section_name="DVP&R主数据区",
            aliases=("试验实际开始日期",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验实际结束日期",
            section_name="DVP&R主数据区",
            aliases=("试验实际结束日期",),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="实际试验结果",
            section_name="DVP&R主数据区",
            aliases=("实际试验结果", "试验结果"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验报告编号",
            section_name="DVP&R主数据区",
            aliases=("试验报告编号", "报告编号", "文件编号"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验前图片",
            section_name="DVP&R主数据区",
            aliases=("试验前图片", "测试前照片"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验中图片",
            section_name="DVP&R主数据区",
            aliases=("试验中图片", "测试中照片"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验后图片",
            section_name="DVP&R主数据区",
            aliases=("试验后图片", "测试后照片"),
            missing_tokens=("", "none", "nan"),
        ),
    ),
    field_blocks=(
        FieldBlockModel(
            canonical_name="DVP&R头部字段区",
            sheet_name="DVP&R工作表",
            search_max_row=80,
            search_max_col=40,
        ),
    ),
    fields=(
        FieldModel(
            canonical_name="版本号字段",
            block_name="DVP&R头部字段区",
            aliases=("版本号", "版本号：", "版本号:"),
            value_mode="below",
            scan_width=1,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="修订日期字段",
            block_name="DVP&R头部字段区",
            aliases=("修订日期", "修订日期：", "修订日期:"),
            value_mode="below",
            scan_width=1,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="修订部门姓名字段",
            block_name="DVP&R头部字段区",
            aliases=("修订部门/姓名", "修订部门姓名"),
            value_mode="below",
            scan_width=1,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="零件总成名称字段",
            block_name="DVP&R头部字段区",
            aliases=("零件/总成名称",),
            value_mode="right",
            scan_width=24,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="适用车型字段",
            block_name="DVP&R头部字段区",
            aliases=("适用车型",),
            value_mode="right",
            scan_width=24,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="零件总成编号字段",
            block_name="DVP&R头部字段区",
            aliases=("零件/总成编号", "零件总成编号"),
            value_mode="right",
            scan_width=24,
            missing_tokens=("", "none", "nan"),
        ),
        FieldModel(
            canonical_name="供应商名称字段",
            block_name="DVP&R头部字段区",
            aliases=("供应商名称",),
            value_mode="right",
            scan_width=24,
            missing_tokens=("", "none", "nan"),
        ),
    ),
    tokens=DomainTokensModel(
        missing_tokens=("", "none", "nan", "na", "/", "／"),
        filled_tokens=(),
    ),
)

_DOC02_1_RESOURCE_VIEW: RuleResource = index_rule_resource(_DOC02_1_RESOURCE_MODEL)


def get_doc02_1_resource_view() -> RuleResource:
    return _DOC02_1_RESOURCE_VIEW
