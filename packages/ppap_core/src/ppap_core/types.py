from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from pydantic import BaseModel


class TokenUsageInfo(BaseModel):
    """Token使用信息"""

    request_id: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_thinking_tokens: int = 0
    total_tokens: int = 0
    llm_calls_count: int = 0
    llm_calls: List[Dict[str, Any]] = []
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: Optional[float] = None


class PpapDocumentType(Enum):
    """
    Bilingual Enum for the 16 specified PPAP Document Types, plus common related types.
    Stores a tuple: (English Name, Chinese Name)
    """

    # --- Types from the user-provided list ---
    DIMENSION_MEASUREMENT_REPORT = ("Dimension measurement report", "全功能检验报告")
    DVP_R = (
        "Design & Process verification plan and Report",
        "零部件设计验证计划和报告",
    )
    SUB_SUPPLIER_LIST = ("Sub-Supplier List", "材料产品及分供清单")
    CHECKING_FIXTURE_APPROVAL_REPORT = (
        "Checking fixture approval report",
        "检具验收报告",
    )
    TOOLING_APPROVAL_REPORT = ("Tooling approval report", "模具验收报告")
    MSA_REPORT = ("Measurement system analysis (MSA) report", "测量系统分析评价")
    INITIAL_PROCESS_CAPACITY_STUDY = ("Initial process capcity study", "过程能力研究")
    PROCESS_FLOW_CHART = ("Process flow chart", "过程流程图")
    PFMEA = ("Process FMEA", "潜在过程失效模式及后果分析")
    CONTROL_PLAN = ("Control plan", "控制计划")
    PROCESS_SPECIAL_CHARACTERISTIC_LIST = (
        "Process special characteristic list",
        "过程特殊特性清单",
    )
    EQUIPMENT_LIST = ("Equipment list", "设备清单")
    PLANT_CAPACITY_EVALUATION_REPORT = (
        "Plant capacity evaluation report",
        "生产系统产能评估报告",
    )
    XIR_ASSEMBLY_ISSUE_REPORT = ("XIR & Assembly Issue Report", "项目问题清单")
    ANNUAL_TEST_REQUIREMENTS = (
        "Annual Test Requirements for Components",
        "零部件年度试验要求",
    )
    PSW = ("Part submission warrant", "零件提交保证书")
    DPVR = ("Design & Process verification plan and Report", "DV报告_PV报告_试验报告")

    # --- Added for robust ambiguity handling ---
    DFMEA = ("Design FMEA", "设计失效模式及后果分析")

    # --- Default/Fallback Type ---
    UNKNOWN = ("Unknown", "未知")

    def get_type_name(self) -> str:
        return self.value[1]

    def iter_aliases(self) -> tuple[str, ...]:
        en_name, cn_name = self.value
        explicit_aliases = _TYPE_ALIASES.get(self, ())
        ordered_aliases = [cn_name, en_name, self.name, *explicit_aliases]
        unique_aliases: list[str] = []
        seen: set[str] = set()
        for alias in ordered_aliases:
            normalized = _normalize_type_text(alias)
            if not normalized or normalized in seen:
                continue
            unique_aliases.append(alias)
            seen.add(normalized)
        return tuple(unique_aliases)

    @classmethod
    def infer_from_text(cls, value: str) -> "PpapDocumentType":
        normalized = _normalize_type_text(value)
        if not normalized:
            return cls.UNKNOWN

        tokens = set(normalized.split())
        best_match: PpapDocumentType = cls.UNKNOWN
        best_priority = (-1, -1)
        for item in cls:
            if item == cls.UNKNOWN:
                continue
            for alias in item.iter_aliases():
                normalized_alias = _normalize_type_text(alias)
                if not normalized_alias or normalized_alias in _AMBIGUOUS_ALIASES:
                    continue

                if normalized == normalized_alias:
                    priority = (2, len(normalized_alias))
                elif normalized_alias in _TOKEN_ALIASES:
                    if normalized_alias not in tokens:
                        continue
                    priority = (1, len(normalized_alias))
                elif normalized_alias in normalized:
                    priority = (1, len(normalized_alias))
                else:
                    continue

                if priority > best_priority:
                    best_match = item
                    best_priority = priority

        return best_match

    @classmethod
    def from_string(cls, s: str) -> "PpapDocumentType":
        return cls.infer_from_text(s)

    @classmethod
    def rank_from_signals(
        cls,
        filename: str = "",
        content: str = "",
    ) -> list[PpapDocumentTypeMatch]:
        scores: dict[PpapDocumentType, int] = {}
        alias_hits: dict[PpapDocumentType, set[str]] = {}
        keyword_hits: dict[PpapDocumentType, set[str]] = {}

        _score_signal(
            filename,
            filename_like=True,
            scores=scores,
            alias_hits=alias_hits,
            keyword_hits=keyword_hits,
        )
        _score_signal(
            content,
            filename_like=False,
            scores=scores,
            alias_hits=alias_hits,
            keyword_hits=keyword_hits,
        )

        matches = [
            PpapDocumentTypeMatch(
                doc_type=doc_type,
                score=score,
                alias_hits=tuple(sorted(alias_hits.get(doc_type, set()))),
                keyword_hits=tuple(sorted(keyword_hits.get(doc_type, set()))),
            )
            for doc_type, score in scores.items()
            if score > 0
        ]
        matches.sort(
            key=lambda item: (
                item.score,
                len(item.alias_hits),
                len(item.keyword_hits),
                item.doc_type.get_type_name(),
            ),
            reverse=True,
        )
        return matches

    @classmethod
    def infer_from_signals(
        cls,
        filename: str = "",
        content: str = "",
        *,
        minimum_score: int = 1,
        min_margin: int = 0,
    ) -> "PpapDocumentType":
        matches = cls.rank_from_signals(filename=filename, content=content)
        if not matches:
            return cls.UNKNOWN
        best = matches[0]
        if best.score < minimum_score:
            return cls.UNKNOWN
        if len(matches) > 1 and (best.score - matches[1].score) < min_margin:
            return cls.UNKNOWN
        return best.doc_type


def _normalize_type_text(value: str) -> str:
    cleaned = unicodedata.normalize("NFKC", unquote(str(value or "")))
    cleaned = re.sub(r"[\*\#`]+", " ", cleaned)
    cleaned = cleaned.replace("&", " ")
    cleaned = re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", cleaned)
    cleaned = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", cleaned)
    cleaned = re.sub(r"([A-Za-z])(\d)", r"\1 \2", cleaned)
    cleaned = re.sub(r"(\d)([A-Za-z])", r"\1 \2", cleaned)
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


_TYPE_ALIASES: dict[PpapDocumentType, tuple[str, ...]] = {
    PpapDocumentType.DIMENSION_MEASUREMENT_REPORT: (
        "尺寸检验报告",
        "全尺寸检验报告",
        "尺寸报告",
        "dsir",
    ),
    PpapDocumentType.DVP_R: (
        "dvp&r",
        "dvpr",
        "dvp r",
        "设计验证计划和报告",
    ),
    PpapDocumentType.SUB_SUPPLIER_LIST: (
        "材料分供方",
        "材料分供方清单",
        "分供方清单",
        "分供清单",
        "材料产品及分供方清单",
        "材料、产品及分供方清单",
        "产品分供方清单",
    ),
    PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT: ("检具认可报告",),
    PpapDocumentType.MSA_REPORT: (
        "msa",
        "测量系统分析报告",
    ),
    PpapDocumentType.INITIAL_PROCESS_CAPACITY_STUDY: ("过程能力分析报告",),
    PpapDocumentType.PROCESS_FLOW_CHART: ("流程图",),
    PpapDocumentType.PFMEA: (
        "pfmea",
        "过程失效模式及后果分析",
    ),
    PpapDocumentType.CONTROL_PLAN: (
        "生产控制计划",
        "cp",
    ),
    PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST: (
        "特殊特性清单",
        "特性管理清单",
        "ccsc",
        "ccsc特性管理清单",
        "产品过程ccsc特性管理清单",
    ),
    PpapDocumentType.PLANT_CAPACITY_EVALUATION_REPORT: ("产能评估报告",),
    PpapDocumentType.XIR_ASSEMBLY_ISSUE_REPORT: ("xir",),
    PpapDocumentType.ANNUAL_TEST_REQUIREMENTS: ("年度试验要求",),
    PpapDocumentType.PSW: (
        "psw",
        "warrant",
    ),
    PpapDocumentType.DPVR: (
        "dpvr",
        "dv报告",
        "pv报告",
        "dv报告 pv报告",
    ),
    PpapDocumentType.DFMEA: (
        "dfmea",
        "设计失效模式及后果分析",
    ),
}

_TOKEN_ALIASES = {
    "ccsc",
    "cp",
    "dfmea",
    "dpvr",
    "dsir",
    "dvpr",
    "msa",
    "pfmea",
    "psw",
    "xir",
}

_AMBIGUOUS_ALIASES = {
    "fmea",
}


@dataclass(frozen=True)
class PpapDocumentTypeMatch:
    doc_type: PpapDocumentType
    score: int
    alias_hits: tuple[str, ...] = ()
    keyword_hits: tuple[str, ...] = ()


_CONTENT_KEYWORDS: dict[PpapDocumentType, tuple[str, ...]] = {
    PpapDocumentType.DIMENSION_MEASUREMENT_REPORT: (
        "尺寸检验",
        "全功能检验",
        "测量结果",
        "测点号",
        "上公差",
        "下公差",
        "坐标x",
        "坐标y",
        "坐标z",
        "gap",
        "flush",
        "centering",
        "range",
    ),
    PpapDocumentType.DVP_R: (
        "设计验证",
        "验证计划",
        "验证报告",
        "试验项目",
    ),
    PpapDocumentType.SUB_SUPPLIER_LIST: (
        "分供方",
        "供应商",
        "供方名称",
    ),
    PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT: (
        "检具",
        "检具验收",
        "检具认可",
        "检测基准",
    ),
    PpapDocumentType.TOOLING_APPROVAL_REPORT: (
        "模具",
        "模具验收",
        "模具认可",
    ),
    PpapDocumentType.MSA_REPORT: (
        "测量系统分析",
        "量具重复性",
        "量具再现性",
        "偏倚",
        "线性",
        "稳定性",
        "gauge r&r",
    ),
    PpapDocumentType.INITIAL_PROCESS_CAPACITY_STUDY: (
        "过程能力",
        "cpk",
        "ppk",
        "能力指数",
    ),
    PpapDocumentType.PROCESS_FLOW_CHART: (
        "过程流程图",
        "流程步骤",
        "工序名称",
        "操作描述",
        "输入",
        "输出",
        "制造",
        "运输",
        "存储",
        "检验",
        "返工",
        "报废",
        "iqc",
        "fqc",
        "oqc",
    ),
    PpapDocumentType.PFMEA: (
        "潜在过程失效模式",
        "失效模式",
        "失效影响",
        "严重度",
        "频度",
        "探测度",
        "rpn",
    ),
    PpapDocumentType.CONTROL_PLAN: (
        "控制计划",
        "生产控制计划",
        "控制方法",
        "反应计划",
        "样件",
        "试生产",
        "生产",
        "产品特性",
        "过程特性",
        "频率",
        "频次",
    ),
    PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST: (
        "特殊特性",
        "特性管理清单",
        "特性分类",
        "特性描述",
        "特性值",
        "控制方法",
        "防错方案",
        "样本数",
    ),
    PpapDocumentType.EQUIPMENT_LIST: (
        "设备清单",
        "设备名称",
        "设备编号",
    ),
    PpapDocumentType.PLANT_CAPACITY_EVALUATION_REPORT: (
        "产能评估",
        "生产系统产能",
        "产能需求",
    ),
    PpapDocumentType.XIR_ASSEMBLY_ISSUE_REPORT: (
        "项目问题清单",
        "assembly issue",
        "问题描述",
    ),
    PpapDocumentType.ANNUAL_TEST_REQUIREMENTS: (
        "年度试验要求",
        "试验要求",
        "试验项目",
    ),
    PpapDocumentType.PSW: (
        "提交保证书",
        "part submission warrant",
        "psw",
    ),
    PpapDocumentType.DPVR: (
        "dv报告",
        "pv报告",
        "试验报告",
    ),
    PpapDocumentType.DFMEA: (
        "设计失效模式",
        "潜在设计失效模式",
        "设计fmea",
    ),
}


def _match_alias(normalized_value: str, tokens: set[str], alias: str) -> bool:
    normalized_alias = _normalize_type_text(alias)
    if not normalized_alias or normalized_alias in _AMBIGUOUS_ALIASES:
        return False
    if normalized_value == normalized_alias:
        return True
    if normalized_alias in _TOKEN_ALIASES:
        return normalized_alias in tokens
    return normalized_alias in normalized_value


def _score_signal(
    value: str,
    *,
    filename_like: bool,
    scores: dict[PpapDocumentType, int],
    alias_hits: dict[PpapDocumentType, set[str]],
    keyword_hits: dict[PpapDocumentType, set[str]],
) -> None:
    normalized = _normalize_type_text(value)
    if not normalized:
        return

    tokens = set(normalized.split())
    alias_exact_weight = 24 if filename_like else 8
    alias_match_weight = 16 if filename_like else 6
    alias_token_weight = 12 if filename_like else 4
    keyword_weight = 6 if filename_like else 3

    for doc_type in PpapDocumentType:
        if doc_type == PpapDocumentType.UNKNOWN:
            continue

        for alias in doc_type.iter_aliases():
            normalized_alias = _normalize_type_text(alias)
            if not normalized_alias or normalized_alias in _AMBIGUOUS_ALIASES:
                continue
            if not _match_alias(normalized, tokens, alias):
                continue

            if normalized == normalized_alias:
                weight = alias_exact_weight
            elif normalized_alias in _TOKEN_ALIASES:
                weight = alias_token_weight
            else:
                weight = alias_match_weight

            scores[doc_type] = scores.get(doc_type, 0) + weight
            alias_hits.setdefault(doc_type, set()).add(alias)

        for keyword in _CONTENT_KEYWORDS.get(doc_type, ()):
            normalized_keyword = _normalize_type_text(keyword)
            if not normalized_keyword:
                continue
            if normalized_keyword in _TOKEN_ALIASES:
                matched = normalized_keyword in tokens
            else:
                matched = normalized_keyword in normalized
            if not matched:
                continue
            scores[doc_type] = scores.get(doc_type, 0) + keyword_weight
            keyword_hits.setdefault(doc_type, set()).add(keyword)
