"""UAES 评测集（V3.33 产品资料包金标）的只读载入。

工作簿是此批交付指定的金标，与 ``uaes-oracle.csv`` 是两套评价来源，互不覆盖。
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
from typing import Any, Literal, cast

from openpyxl import load_workbook

from corpus_contracts.oracle import CORPUS, REPO_ROOT, customer_rules, suite_sources

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


@dataclass(frozen=True)
class Batch:
    """一个零件目录（含缺陷样本）作为一批提交，与资料包按零件出总成报告一致。"""

    part: str
    files: tuple[EvaluationRow, ...]

    @property
    def id(self) -> str:
        return f"uaes-evaluation/{self.part}"

    @property
    def has_pdf(self) -> bool:
        return any(submitted_name(row).lower().endswith(".pdf") for row in self.files)


def submitted_name(row: EvaluationRow) -> str:
    """提交文件名就是资料包里的文件名（改名原件按资料包名提交）。"""
    return PurePosixPath(row.keys["存储地址"]).name


def file_id(row: EvaluationRow) -> str:
    return f"f{row.keys['序号']:02d}"


def batches() -> tuple[Batch, ...]:
    precheck, _ = load_evaluation()
    parts: dict[str, list[EvaluationRow]] = {}
    for row in precheck:
        parts.setdefault(PurePosixPath(row.keys["存储地址"].removeprefix(SOURCE_PREFIX)).parts[0], []).append(row)
    return tuple(Batch(part, tuple(rows)) for part, rows in parts.items())


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


def precheck_actual(precheck: dict[str, Any], metadata: dict[str, Any], file_version_id: str) -> dict[str, Any]:
    """产品预检结果里一个文件的 F～O 对应值（整改动作为自由文字，不取）。"""
    blocks = [item for item in precheck["blocks"] if item["file_version_id"] == file_version_id]
    warnings = [item for item in precheck["warnings"] if item["file_version_id"] == file_version_id]
    level = next(row["level"] for row in precheck["files"] if row["file_version_id"] == file_version_id)
    verdict = "不通过" if level == "block" else "通过" if level in {"ok", "warn"} else f"未预检（{level}）"
    return {
        "预检结果": verdict,
        "阻断数": len(blocks),
        "警告数": len(warnings),
        "问题": sorted(item["code"] for item in [*blocks, *warnings]),
        **{column: metadata.get(field) for column, field in COVER.items()},
    }


@dataclass(frozen=True)
class BatchResult:
    precheck: dict[str, Any]
    metadata: dict[str, dict[str, Any]]
    suites: dict[str, str]
    clauses: dict[tuple[str, str], dict[str, Any]]
    audit_error: str = ""


def run_batch(batch: Batch) -> BatchResult:
    """按产品路径提交一批：文件检查 → 上传识别 → 预检（含内容级）→ 已匹配文件全部审核，参考件按识别类型参与。"""
    from agent.execution.audit_runtime import run_audit
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
    from audit.standard_suites import library_revision
    from product.precheck import precheck_task
    from product.precheck_content import entry_key, precheck_content
    from worker.intake.file_analysis import analyze_file
    from worker.intake.file_validation import validate_file

    rules = customer_rules("UAES")
    rows: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    for item in batch.files:
        identity, name, data = file_id(item), submitted_name(item), item.source.read_bytes()
        blobs[identity] = data
        validation = validate_file(name, data)
        match: dict[str, Any] | None = None
        if validation["state"] == "valid":
            result = analyze_file(name, data, rules, customer_preference="UAES", scoped=True)
            state = result.get("matching_status")
            state = state if state in {"matched", "reference", "unmatched", "failed"} else "failed"
            match = {
                "state": state,
                "metadata": result.get("metadata") or {},
                "document_type": result.get("document_type"),
                "gaps": result.get("notices") or (["rule_matching_failed"] if state == "failed" else []),
                "matched_rule_id": result.get("matched_rule_id"),
            }
        rows.append(
            {"file": {"id": identity, "file_id": identity, "original_filename": name, "sha256": hashlib.sha256(data).hexdigest()}, "selection": "selected", "validation": validation, "match": match}
        )
    task: dict[str, Any] = {"customer_preference": "UAES", "files": rows}
    content: dict[str, list[dict]] = {}
    for row in rows:
        match = row["match"] or {}
        if match.get("state") != "matched":
            continue
        customer = (match.get("metadata") or {}).get("customer") or "UAES"
        key = entry_key(customer, match.get("document_type"))
        if key is None:
            continue
        try:
            content[row["file"]["id"]] = precheck_content(blobs[row["file"]["id"]], row["file"]["original_filename"], key, customer=customer)
        except Exception:  # noqa: BLE001 - as the product: an unreadable file stays at the file-level result
            continue
    precheck = precheck_task(task, content, read=lambda row: blobs[row["file"]["id"]])
    metadata = {row["file"]["id"]: dict((row["match"] or {}).get("metadata") or {}) for row in rows}

    sources = suite_sources()
    suites: dict[str, str] = {}
    inputs: list[FrozenInput] = []
    targets: list[FrozenTarget] = []
    for row in rows:
        match = row["match"] or {}
        identity, meta = row["file"]["id"], metadata[row["file"]["id"]]
        suite = sources.get(f"uaes/{match.get('matched_rule_id')}") if match.get("state") == "matched" else None
        if match.get("state") == "matched" and suite is not None:
            suites[identity] = suite.source_path
            role, document_type = "target", suite.document_type
            targets.append(
                FrozenTarget(
                    identity,
                    "sha256:" + suite.revision_digest,
                    suite.suite_key,
                    suite.revision_digest,
                    json.loads(suite.files["rules.json"]),
                    RuleBinding(suite.suite_key, checker_digest(suite.program_files), suite.entrypoints),
                )
            )
        elif match.get("state") == "reference":
            role, document_type = "reference", match.get("document_type") or ""
        else:
            # 未匹配的文件不进入审核。
            continue
        inputs.append(
            FrozenInput(
                identity,
                row["file"]["sha256"],
                cast(Literal["target", "reference"], role),
                row["file"]["original_filename"],
                document_type=document_type,
                customer=meta.get("customer"),
                model=meta.get("model"),
                part=meta.get("part"),
            )
        )
    clauses: dict[tuple[str, str], dict[str, Any]] = {}
    if not targets:
        return BatchResult(precheck, metadata, suites, clauses)
    snapshot = ExecutionSnapshot("corpus", "uaes-evaluation-runner", tuple(inputs), tuple(targets), "corpus-source", library_revision(), {}, round_id=batch.id)
    by_digest = {"sha256:" + suite.revision_digest: suite for suite in sources.values()}
    programs = {target.binding.checker_digest: by_digest[target.suite_revision_id].program_files for target in targets}
    try:
        result = run_audit(snapshot, {item.file_version_id: blobs[item.file_version_id] for item in inputs}, programs)
    except Exception as exc:  # noqa: BLE001 - recorded as the reason every audit row of this batch has no result
        return BatchResult(precheck, metadata, suites, clauses, f"审核未完成：{type(exc).__name__}")
    severity = {(target.file_version_id, str(rule["id"])): rule.get("severity") for target in targets for rule in target.rules["rules"]}
    for finding in result["findings"]:
        clause = (finding["file_version_id"], str(finding["clause_id"]))
        if clause in clauses:
            clauses[clause] = {"status": None, "reason": "同一条款有多个结果"}
            continue
        clauses[clause] = {"status": finding.get("status"), "severity": severity.get(clause), "reason": str(finding.get("suggestions") or "")[:200]}
    return BatchResult(precheck, metadata, suites, clauses)


def compare_batch(batch: Batch, result: BatchResult) -> list[Comparison]:
    """一批的全部比对：预检逐文件、审核逐条款。"""
    out: list[Comparison] = []
    addresses = {row.keys["存储地址"]: file_id(row) for row in batch.files}
    for row in batch.files:
        out += compare_precheck(row, precheck_actual(result.precheck, result.metadata[file_id(row)], file_id(row)))
    _, audit = load_evaluation()
    mapping = rule_clauses()
    for row in audit:
        identity = addresses.get(row.keys["存储地址"])
        if identity is None:
            continue
        clause = mapping[row.keys["规则编号"]]
        actual = result.clauses.get((identity, clause))
        if actual is None:
            suite = result.suites.get(identity)
            if result.audit_error:
                reason = result.audit_error
            elif suite is None:
                reason = "产品未审核此文件（上传识别未匹配到 UAES 规则）"
            elif suite != clause_leaf(clause):
                reason = f"产品按 {suite.split('/')[-1]} 审核，未执行 {clause}"
            else:
                reason = f"缺少 {clause} 的结果"
            actual = {"status": None, "reason": reason}
        out += compare_audit(row, actual)
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


__all__ = [
    "AUDIT",
    "DIFFERENT",
    "MATCH",
    "PRECHECK",
    "RENAMED",
    "RULE_CLAUSES",
    "SAMPLES",
    "SOURCE_PREFIX",
    "UNCOMPARABLE",
    "WORKBOOK",
    "Batch",
    "BatchResult",
    "Comparison",
    "EvaluationRow",
    "batches",
    "clause_leaf",
    "compare_audit",
    "compare_batch",
    "compare_precheck",
    "defect_findings",
    "defect_pairs",
    "load_evaluation",
    "precheck_actual",
    "resolve_source",
    "rule_clauses",
    "run_batch",
    "submitted_name",
]
