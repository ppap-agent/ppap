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

_DOC09_RESOURCE_MODEL = RuleResourceModel(
    sheets=(
        SheetModel(
            canonical_name="PFMEA主表",
            include_keywords=("pfmea",),
            exclude_keywords=("变更", "更新", "评分", "准则", "評分", "準則", "数据库", "rating", "ap"),
            match_mode="contains",
            visible_only=True,
        ),
        SheetModel(
            canonical_name="变更履历表",
            include_keywords=("变更履历", "更新履历"),
            exclude_keywords=(),
            match_mode="contains",
            visible_only=True,
        ),
    ),
    sections=(
        SectionModel(
            canonical_name="变更履历数据区",
            sheet_name="变更履历表",
            header_anchors=("修订日期",),
            row_anchors=("修订日期",),
            direction="down",
            header_hit=1,
            header_search_max_row=120,
            header_search_max_col=40,
            column_band=1,
            row_stop_on_first_blank=False,
            row_max_blank_streak=200,
        ),
        SectionModel(
            canonical_name="PFMEA主数据区",
            sheet_name="PFMEA主表",
            header_anchors=("过程项功能", "失效影响", "严重度", "现行探测控制", "探测度"),
            row_anchors=("过程项功能", "失效影响", "现行探测控制"),
            direction="down",
            header_hit=2,
            header_search_max_row=120,
            header_search_max_col=40,
            header_compact_match=True,
            column_band=2,
            row_stop_on_first_blank=False,
            row_max_blank_streak=200,
        ),
    ),
    columns=(
        ColumnModel(
            canonical_name="修订日期",
            section_name="变更履历数据区",
            aliases=("修订日期",),
            missing_tokens=("", "none", "nan", "na"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="过程项功能",
            section_name="PFMEA主数据区",
            aliases=("过程项功能", "过程项/功能", "1.过程项功能", "1、过程项功能"),
            missing_tokens=("", "none", "nan", "na"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="失效影响",
            section_name="PFMEA主数据区",
            aliases=("失效影响", "1.失效影响FE", "1、失效影响FE"),
            missing_tokens=("", "none", "nan", "na"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="严重度",
            section_name="PFMEA主数据区",
            aliases=("严重度",),
            missing_tokens=("", "none", "nan", "na"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="现行探测控制",
            section_name="PFMEA主数据区",
            aliases=("现行探测控制", "现行探测控制(DC)", "现行探 测控制", "现行探测控制dc"),
            missing_tokens=("", "none", "nan", "na"),
            filled_tokens=(),
        ),
        ColumnModel(
            canonical_name="探测度",
            section_name="PFMEA主数据区",
            aliases=("探测度",),
            missing_tokens=("", "none", "nan", "na"),
            filled_tokens=(),
        ),
    ),
    field_blocks=(
        FieldBlockModel(
            canonical_name="PFMEA头部字段区",
            sheet_name="PFMEA主表",
            search_max_row=60,
            search_max_col=40,
        ),
    ),
    fields=(
        FieldModel(
            canonical_name="公司名称字段",
            block_name="PFMEA头部字段区",
            aliases=("公司名称", "公司名称：", "公司名称:"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="车型项目字段",
            block_name="PFMEA头部字段区",
            aliases=("车型项目", "车型项目：", "车型项目:"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="零件名称字段",
            block_name="PFMEA头部字段区",
            aliases=("零件名称", "零件名称：", "零件名称:"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="零件号字段",
            block_name="PFMEA头部字段区",
            aliases=("零件号", "零件号：", "零件号:"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="制造地址字段",
            block_name="PFMEA头部字段区",
            aliases=("制造地址", "制造地址：", "制造地址:"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="代码字段",
            block_name="PFMEA头部字段区",
            aliases=("代码", "代码：", "代码:"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="PFMEA开始日期字段",
            block_name="PFMEA头部字段区",
            aliases=("PFMEA开始日期", "PFMEA开始日期：", "PFMEA开始日期:", "pfmea开始日期"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="PFMEA修订日期字段",
            block_name="PFMEA头部字段区",
            aliases=("PFMEA修订日期", "PFMEA修订日期：", "PFMEA修订日期:", "pfmea修订日期"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
        FieldModel(
            canonical_name="跨职能团队字段",
            block_name="PFMEA头部字段区",
            aliases=("跨职能团队", "跨职能团队：", "跨职能团队:"),
            value_mode="right",
            scan_width=16,
            missing_tokens=("", "none", "nan", "na"),
        ),
    ),
    tokens=DomainTokensModel(
        missing_tokens=("", "none", "nan", "na"),
        filled_tokens=(),
    ),
)

_DOC09_RESOURCE_VIEW: RuleResource = index_rule_resource(_DOC09_RESOURCE_MODEL)


def get_doc09_resource_view() -> RuleResource:
    return _DOC09_RESOURCE_VIEW
