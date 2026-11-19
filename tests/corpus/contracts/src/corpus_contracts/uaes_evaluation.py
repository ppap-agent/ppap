"""UAES 评测集（V3.33 产品资料包）：只读载入，并逐行分析样本运行已产出的结果与评测集的差异。

工作簿是另一位评审给的参考判断，不是真值：真值以 ``uaes-oracle.csv`` 为准，二者冲突的裁决记在
``评测集订正.md``。评测集不提交批次、不调用模型、不录回放，也不作为必须通过的测试。
标准答案列原样返回，不做归一化。存储地址按本资产包版本声明的前缀映射到
``assets/uaes/samples`` 下已有的同内容原件，不把工作簿路径当作磁盘位置。
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from openpyxl import load_workbook

from corpus_contracts.oracle import CORPUS, REPO_ROOT

WORKBOOK = CORPUS / "UAES 评测集.xlsx"
# 固定可信版本：V3.33 产品资料包（2026-11-18）卷二 03_评测集。
WORKBOOK_SHA256 = "73ce28d3f031c8a803c470497bcf2a8e42aa039381e702a11fa054c598362a0e"
SAMPLES = REPO_ROOT / "assets/uaes/samples"
# 工作簿写旧目录 02_受审文件；资料包实际目录为 05_受审文件，零件目录以下结构相同。
SOURCE_PREFIX = "../02_受审文件/"
DEFECT_DIR = "缺陷样本(-test-)"
# 资料包里与仓库已有原件内容相同、但仓库未另存的文件名（A40 的两份 CPK 内容相同），按内容摘要钉住。
RENAMED = {
    "F01RD0YA40_机加工壳体/07 过程能力研究/A40 CPK-机加工.xlsx": (
        "F01RD0YA40_机加工壳体/测试文件/07 过程能力研究/A40-机加工 (2).xlsx",
        "5a043fb788d7d7fd4893de4dda98448437c5d7a43abd503baad586e121f8fcd9",
    ),
}

_KEYS = ("序号", "文件名称", "文件类型", "文件格式", "存储地址")
PRECHECK = ("预检统计表", (*_KEYS,), "标准答案", ("预检结果", "阻断数", "警告数", "问题", "整改动作", "项目", "客户", "车型", "零件号", "零件名称"))
AUDIT = ("审核明细", (*_KEYS, "规则编号", "规则详情（查什么 / 查哪里）"), "标准答案", ("审核结果", "问题等级", "审核依据", "整改建议"))


@dataclass(frozen=True)
class EvaluationRow:
    sheet: str
    excel_row: int
    keys: dict[str, Any]
    answers: dict[str, Any]
    source: Path

    @property
    def is_defect(self) -> bool:
        return DEFECT_DIR in self.source.relative_to(SAMPLES).parts


def resolve_source(address: str) -> Path:
    """工作簿存储地址 → 仓库原件；只接受声明前缀下的零件目录相对路径。"""
    if not isinstance(address, str) or not address.startswith(SOURCE_PREFIX) or "\\" in address:
        raise ValueError(f"存储地址不在评测集声明的前缀下：{address!r}")
    relative = PurePosixPath(address.removeprefix(SOURCE_PREFIX))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts) or len(relative.parts) < 3:
        raise ValueError(f"存储地址不是零件目录下的文件：{address!r}")
    part, *rest = relative.parts
    if str(relative) in RENAMED:
        target, digest = RENAMED[str(relative)]
        path = SAMPLES / target
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"改名原件内容与资料包不同：{address!r}")
    elif rest[0] == DEFECT_DIR:
        path = SAMPLES.joinpath(part, *rest)
    else:
        path = SAMPLES.joinpath(part, "测试文件", *rest)
    if not path.resolve().is_relative_to(SAMPLES.resolve()) or not path.is_file():
        raise ValueError(f"存储地址在仓库样本中找不到：{address!r}")
    return path


def _sheet(workbook: Any, spec: tuple[str, tuple[str, ...], str, tuple[str, ...]]) -> list[EvaluationRow]:
    name, keys, group, answers = spec
    sheet = workbook[name]
    top = [cell.value for cell in sheet[1]]
    sub = [cell.value for cell in sheet[2]]
    expected_top = [*keys, group, *[None] * (len(answers) - 1)]
    expected_sub = [*[None] * len(keys), *answers]
    if top != expected_top or sub != expected_sub:
        raise ValueError(f"{name} 双层表头与评测集约定不同")
    rows = []
    for index, values in enumerate(sheet.iter_rows(min_row=3, values_only=True), start=3):
        if all(value is None for value in values):
            continue
        if len(values) != len(keys) + len(answers):
            raise ValueError(f"{name} 第 {index} 行列数不对")
        key = dict(zip(keys, values[: len(keys)], strict=True))
        rows.append(EvaluationRow(name, index, key, dict(zip(answers, values[len(keys) :], strict=True)), resolve_source(key["存储地址"])))
    return rows


@lru_cache(maxsize=1)
def load_evaluation(path: Path = WORKBOOK) -> tuple[tuple[EvaluationRow, ...], tuple[EvaluationRow, ...]]:
    """(预检统计表, 审核明细) 的数据行；双层表头不计入。"""
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != WORKBOOK_SHA256:
        raise ValueError("评测集工作簿不是已固定的 V3.33 版本")
    workbook = load_workbook(path, read_only=False, data_only=False)
    return tuple(_sheet(workbook, PRECHECK)), tuple(_sheet(workbook, AUDIT))


# 规则执行包 rule_id → 叶子条款号。资料包卷二 01_规则执行包/02_审核规则/UAES 每张规则卡按「文件类型 + 条款序」
# 对应叶子 rules.json 的条款；CI 读不到资料包，只保存这份对应关系，不复制规则原文。
RULE_CLAUSES = Path(__file__).resolve().parents[3] / "uaes-evaluation-rules.csv"


@lru_cache(maxsize=1)
def rule_clauses(path: Path = RULE_CLAUSES) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))
    if not rows or rows[0] != ["规则编号", "条款ID"]:
        raise ValueError("规则对应表表头不对")
    mapping: dict[str, str] = {}
    for rule_id, clause in rows[1:]:
        if not re.fullmatch(r"UAES-\S+", rule_id) or not re.fullmatch(r"U\d{2}:\d+(?:-\d+)?", clause) or rule_id in mapping or clause in mapping.values():
            raise ValueError(f"规则对应表行不对：{rule_id},{clause}")
        mapping[rule_id] = clause
    return mapping


def clause_leaf(clause: str) -> str:
    return "uaes/" + clause.split(":", 1)[0]


# 比对结论
MATCH, DIFFERENT, UNCOMPARABLE = "一致", "不一致", "无法比较"
FREE_TEXT = "自由文字，不逐字判分"
COVER = {"项目": "project", "客户": "customer", "车型": "model", "零件号": "part_number", "零件名称": "part"}
RESULTS = {"通过": "PASS", "不通过": "NG", "不适用": "NA"}
# 卷一 PRD §1.4 A/B/C ↔ 卷二 severity fatal/major/minor（00_双卷索引 咬合 3）。
LEVELS = {"致命": "A", "主要": "B", "次要": "C"}
PENDING = "待裁决"
_ISSUE = re.compile(r"^([BW]\d{2})·", re.MULTILINE)


@dataclass(frozen=True)
class Comparison:
    sheet: str
    excel_row: int
    key: str
    column: str
    expected: Any
    actual: Any
    outcome: str
    reason: str = ""

    def line(self) -> str:
        return f"{self.sheet} 第{self.excel_row}行 {self.key} [{self.column}] 预期={self.expected!r} 实际={self.actual!r} {self.outcome}" + (f"（{self.reason}）" if self.reason else "")


def submitted_name(row: EvaluationRow) -> str:
    """提交文件名就是资料包里的文件名（改名原件按资料包名提交）。"""
    return PurePosixPath(row.keys["存储地址"]).name


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _same(expected: Any, actual: Any) -> bool:
    if _empty(expected) or _empty(actual):
        return _empty(expected) and _empty(actual)
    return str(expected).strip() == str(actual).strip()


def _outcome(expected: Any, actual: Any) -> str:
    return MATCH if _same(expected, actual) else DIFFERENT


def compare_precheck(row: EvaluationRow, actual: dict[str, Any]) -> list[Comparison]:
    """预检统计表一行：F～O 每列一条比对。``actual`` 为该文件的产品预检结果（:func:`precheck_actual`）。"""
    key = f"{row.keys['序号']} {row.keys['文件名称']}"
    answers = row.answers
    out = [Comparison(row.sheet, row.excel_row, key, column, answers[column], actual[column], _outcome(answers[column], actual[column])) for column in ("预检结果", "阻断数", "警告数")]
    expected_codes = sorted(_ISSUE.findall(answers["问题"] or ""))
    if not _empty(answers["问题"]) and not expected_codes:
        out.append(Comparison(row.sheet, row.excel_row, key, "问题", answers["问题"], actual["问题"], UNCOMPARABLE, "标准答案未写问题代码"))
    else:
        out.append(Comparison(row.sheet, row.excel_row, key, "问题", "、".join(expected_codes), "、".join(actual["问题"]), MATCH if expected_codes == actual["问题"] else DIFFERENT, "按问题代码比对"))
    out.append(Comparison(row.sheet, row.excel_row, key, "整改动作", answers["整改动作"], None, UNCOMPARABLE, FREE_TEXT))
    out += [Comparison(row.sheet, row.excel_row, key, column, answers[column], actual[column], _outcome(answers[column], actual[column])) for column in COVER]
    return out


def compare_audit(row: EvaluationRow, actual: dict[str, Any] | None) -> list[Comparison]:
    """审核明细一行：H～K。``actual`` 为该条款的产品结果（status/severity），None 时 ``reason`` 说明为何没有结果。"""
    key = f"{row.keys['序号']} {row.keys['文件名称']} {row.keys['规则编号']}"
    answers = row.answers
    status = (actual or {}).get("status")
    reason = (actual or {}).get("reason", "")
    out = []
    if answers["审核结果"] == PENDING:
        out.append(Comparison(row.sheet, row.excel_row, key, "审核结果", PENDING, status, UNCOMPARABLE, "标准答案待裁决"))
    elif answers["审核结果"] not in RESULTS:
        out.append(Comparison(row.sheet, row.excel_row, key, "审核结果", answers["审核结果"], status, UNCOMPARABLE, "标准答案取值不在 通过/不通过/不适用/待裁决 内"))
    else:
        out.append(Comparison(row.sheet, row.excel_row, key, "审核结果", answers["审核结果"], status, MATCH if RESULTS[answers["审核结果"]] == status else DIFFERENT, reason))
    level = (actual or {}).get("severity") if status == "NG" else None
    if answers["问题等级"] == PENDING:
        out.append(Comparison(row.sheet, row.excel_row, key, "问题等级", PENDING, level, UNCOMPARABLE, "标准答案待裁决"))
    elif not _empty(answers["问题等级"]) and answers["问题等级"] not in LEVELS:
        out.append(Comparison(row.sheet, row.excel_row, key, "问题等级", answers["问题等级"], level, UNCOMPARABLE, "标准答案取值不在 致命/主要/次要 内"))
    else:
        expected = LEVELS.get(answers["问题等级"]) if not _empty(answers["问题等级"]) else None
        out.append(Comparison(row.sheet, row.excel_row, key, "问题等级", answers["问题等级"], level, MATCH if expected == level else DIFFERENT, "致命=A、主要=B、次要=C；产品取条款默认严重程度"))
    out += [Comparison(row.sheet, row.excel_row, key, column, answers[column], None, UNCOMPARABLE, FREE_TEXT) for column in ("审核依据", "整改建议")]
    return out


def defect_pairs() -> list[tuple[EvaluationRow, EvaluationRow]]:
    """每份缺陷样本与同零件原件目录里的正常件：去掉文件名里「-test-…」后同名（MSA 缺陷样本本就同名）。"""
    precheck, _ = load_evaluation()
    pairs = []
    for defect in (row for row in precheck if row.is_defect):
        name = PurePosixPath(submitted_name(defect))
        normal_name = re.sub(r"\s*-\s*test-.*$", "", name.stem) + name.suffix
        part = defect.source.relative_to(SAMPLES).parts[0]
        normal = [row for row in precheck if not row.is_defect and row.source.relative_to(SAMPLES).parts[0] == part and submitted_name(row) == normal_name]
        if len(normal) != 1:
            raise ValueError(f"缺陷样本找不到唯一的正常件：{name}")
        pairs.append((defect, normal[0]))
    return pairs


def defect_findings(defect: EvaluationRow, normal: EvaluationRow, comparisons: list[Comparison]) -> tuple[list[Comparison], list[Comparison]]:
    """(漏检, 误判)：只看预检结果与审核结果两列。缺陷件与标注不同为漏检，正常件与标注不同为误判。"""
    precheck, audit = load_evaluation()
    addresses = {(row.sheet, row.excel_row): row.keys["存储地址"] for row in (*precheck, *audit)}
    differing = [item for item in comparisons if item.column in {"预检结果", "审核结果"} and item.outcome == DIFFERENT]
    missed = [item for item in differing if addresses[(item.sheet, item.excel_row)] == defect.keys["存储地址"]]
    false = [item for item in differing if addresses[(item.sheet, item.excel_row)] == normal.keys["存储地址"]]
    return missed, false


def sample_results(directory: Path) -> tuple[dict[tuple[Path, str], dict[str, Any]], dict[Path, dict[str, Any]]]:
    """样本运行写进报告目录的结果：(审核 {(原件, 条款): 结果}, 预检 {原件: 结果})。原件为仓库内绝对路径。"""
    audit: dict[tuple[Path, str], dict[str, Any]] = {}
    precheck: dict[Path, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("kind") == "finding":
                key = ((REPO_ROOT / row["source"]).resolve(), row["clause"])
                # 同一原件在多个组合里审核时，结论不同就不能代表这份文件。
                if key in audit and audit[key]["status"] != row["status"]:
                    audit[key] = {"status": None, "reason": "同一原件在不同组合中的结论不同"}
                else:
                    audit.setdefault(key, {"status": row["status"], "severity": row.get("severity"), "reason": row.get("reason", "")})
            elif row.get("kind") == "precheck":
                precheck[(REPO_ROOT / row["source"]).resolve()] = row["actual"]
    return audit, precheck


def bench(directory: Path) -> list[Comparison]:
    """逐行列出样本运行已产出的结果与评测集的差异；不运行任何东西。评测集是另一份人工判断，不一致不等于程序错，
    已核实的订正见 ``评测集订正.md``。"""
    audit_results, precheck_results = sample_results(directory)
    precheck, audit = load_evaluation()
    out: list[Comparison] = []
    for row in precheck:
        actual = precheck_results.get(row.source.resolve())
        if actual is None:
            key = f"{row.keys['序号']} {row.keys['文件名称']}"
            out.append(Comparison(row.sheet, row.excel_row, key, "预检结果", row.answers["预检结果"], None, UNCOMPARABLE, "样本运行没有产出这份文件的预检结果"))
        else:
            out += compare_precheck(row, actual)
    mapping = rule_clauses()
    for row in audit:
        actual = audit_results.get((row.source.resolve(), mapping[row.keys["规则编号"]]))
        if actual is None:
            key = f"{row.keys['序号']} {row.keys['文件名称']} {row.keys['规则编号']}"
            out.append(Comparison(row.sheet, row.excel_row, key, "审核结果", row.answers["审核结果"], None, UNCOMPARABLE, "样本运行没有产出这份文件这一条款的结果"))
        else:
            out += compare_audit(row, actual)
    return out


def main(argv: list[str] | None = None) -> int:
    """``python -m corpus_contracts.uaes_evaluation <报告目录>``：打印逐行比对与汇总。"""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("用法：python -m corpus_contracts.uaes_evaluation <样本运行的报告目录>", file=sys.stderr)
        return 2
    comparisons = bench(Path(args[0]))
    for sheet, column in (("预检统计表", "预检结果"), ("审核明细", "审核结果")):
        scored = [item for item in comparisons if item.sheet == sheet and item.column == column]
        counts = {outcome: sum(item.outcome == outcome for item in scored) for outcome in (MATCH, DIFFERENT, UNCOMPARABLE)}
        print(f"{sheet} {column}：" + "，".join(f"{name} {count}" for name, count in counts.items()))
    for item in comparisons:
        if item.outcome == DIFFERENT:
            print(item.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
