from __future__ import annotations

from ..contracts import (
    ColumnModel,
    DomainTokensModel,
    RuleResource,
    RuleResourceModel,
    SectionModel,
    SheetModel,
    index_rule_resource,
)

_DOC02_2_RESOURCE_MODEL = RuleResourceModel(
    sheets=(
        SheetModel(
            canonical_name="DPVR工作表",
            include_keywords=("试验报告", "检测报告", "dvp", "dpvr", "test report"),
            exclude_keywords=(),
            match_mode="contains",
            visible_only=False,
        ),
    ),
    sections=(
        SectionModel(
            canonical_name="DPVR主数据区",
            sheet_name="DPVR工作表",
            header_anchors=("序号", "试验报告编号", "试验类型", "实际试验结果"),
            row_anchors=("序号",),
            direction="down",
            header_hit=2,
            header_search_max_row=180,
            header_search_max_col=100,
            header_compact_match=True,
            column_band=2,
            row_stop_on_first_blank=False,
            row_max_blank_streak=120,
            row_stop_markers=("职责说明", "备注", "本页结束"),
        ),
    ),
    columns=(
        ColumnModel(
            canonical_name="序号",
            section_name="DPVR主数据区",
            aliases=("序号", "序列号", "NO", "No", "No.", "no."),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验报告编号",
            section_name="DPVR主数据区",
            aliases=("试验报告编号", "检测报告编号", "报告编号", "文件编号", "编号", "Report No"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="实际试验结果",
            section_name="DPVR主数据区",
            aliases=(
                "实际试验结果",
                "结果判定 Result judgment",
                "结果判定",
                "判定结果",
                "判定结论",
                "检测结论",
                "结论",
                "Conclusion",
                "试验结果",
                "判定",
            ),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验类型",
            section_name="DPVR主数据区",
            aliases=("试验类型", "试验阶段", "测试阶段", "项目阶段"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验前图片",
            section_name="DPVR主数据区",
            aliases=("试验前图片", "测试前照片", "试验前"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验中图片",
            section_name="DPVR主数据区",
            aliases=("试验中图片", "测试中照片", "试验中", "实验中"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验后图片",
            section_name="DPVR主数据区",
            aliases=("试验后图片", "测试后照片", "试验后"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="试验样本描述",
            section_name="DPVR主数据区",
            aliases=("试验样本描述", "样品数量", "样本数量", "样品数", "样本数"),
            missing_tokens=("", "none", "nan"),
        ),
        ColumnModel(
            canonical_name="数量",
            section_name="DPVR主数据区",
            aliases=("数量",),
            missing_tokens=("", "none", "nan"),
        ),
    ),
    tokens=DomainTokensModel(
        missing_tokens=("", "none", "nan", "na", "/", "／"),
        filled_tokens=(),
    ),
)

_DOC02_2_RESOURCE_VIEW: RuleResource = index_rule_resource(_DOC02_2_RESOURCE_MODEL)


def get_doc02_2_resource_view() -> RuleResource:
    return _DOC02_2_RESOURCE_VIEW
