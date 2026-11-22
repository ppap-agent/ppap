"""V3.33 UAES 评测集：载入（表头、行数、原件定位、标准答案原样）与样本运行结果的差异分析。"""

import json

import pytest
from corpus_contracts.uaes_evaluation import RENAMED, SAMPLES, SOURCE_PREFIX, WORKBOOK, load_evaluation, resolve_source
from openpyxl import load_workbook

RENAMED_SIBLING = "F01RD0YA40_机加工壳体/07 过程能力研究/A40-机加工 (2).xlsx"


def test_two_header_rows_are_not_data():
    precheck, audit = load_evaluation()
    assert (len(precheck), len(audit)) == (58, 186)
    assert [row.excel_row for row in precheck] == list(range(3, 61))
    assert [row.excel_row for row in audit] == list(range(3, 189))
    assert [row.keys["序号"] for row in precheck] == list(range(1, 59))


def test_every_reference_locates_one_repository_original():
    precheck, audit = load_evaluation()
    rows = [*precheck, *audit]
    assert len(rows) == 244
    by_address: dict[str, set] = {}
    for row in rows:
        assert row.source.resolve().is_relative_to(SAMPLES.resolve()) and row.source.is_file()
        by_address.setdefault(row.keys["存储地址"], set()).add(row.source)
    # 每个地址只对应一个原件。资料包 58 份原件里，A40 的两份 CPK 内容相同，
    # 仓库只存一份（不重复放文件），只有这一对地址共用原件。
    assert all(len(sources) == 1 for sources in by_address.values())
    assert len(by_address) == 58
    shared: dict = {}
    for address, (source,) in by_address.items():
        shared.setdefault(source, []).append(address.removeprefix(SOURCE_PREFIX))
    assert [sorted(addresses) for addresses in shared.values() if len(addresses) > 1] == [sorted([*RENAMED, RENAMED_SIBLING])]
    sources = set(shared)
    assert len(sources) == 57
    # 预检逐份列出全部原件。
    assert {row.source for row in precheck} == sources


def test_ten_defect_samples_are_the_test_files():
    precheck, _ = load_evaluation()
    defects = {row.source for row in precheck if row.is_defect}
    assert len(defects) == 10
    assert all("test-" in str(path.relative_to(SAMPLES)) for path in defects)
    assert not any("test-" in str(row.source.relative_to(SAMPLES)) for row in precheck if not row.is_defect)


def test_standard_answers_are_kept_verbatim():
    precheck, audit = load_evaluation()
    workbook = load_workbook(WORKBOOK)
    for rows, sheet_name, first in ((precheck, "预检统计表", 6), (audit, "审核明细", 8)):
        sheet = workbook[sheet_name]
        for row in rows:
            raw = [cell.value for cell in sheet[row.excel_row]][first - 1 :]
            assert list(row.answers.values()) == raw


@pytest.mark.parametrize(
    "address",
    [
        "../05_受审文件/F01R10Y527_节气门壳体/06 测量系统分析评价/574-527-MSA (1).pdf",
        "../02_受审文件/../corpus/uaes-oracle.csv",
        "../02_受审文件/F01R10Y527_节气门壳体/../../corpus/uaes-oracle.csv",
        "/etc/passwd",
        "../02_受审文件/F01R10Y527_节气门壳体/06 测量系统分析评价/不存在.pdf",
    ],
)
def test_addresses_outside_the_declared_layout_are_rejected(address):
    with pytest.raises(ValueError):
        resolve_source(address)


def test_rule_ids_map_to_existing_leaf_clauses():
    from corpus_contracts.oracle import suite_sources
    from corpus_contracts.uaes_evaluation import rule_clauses

    mapping = rule_clauses()
    _, audit = load_evaluation()
    assert {row.keys["规则编号"] for row in audit} <= set(mapping)
    # 规则执行包 UAES 52 条与 19 个叶子的条款一一对应（U08 没有第 5 条）。
    leaves = {path: suite for path, suite in suite_sources().items() if path.startswith("uaes/")}
    declared = {str(rule["id"]) for suite in leaves.values() for rule in json.loads(suite.files["rules.json"])["rules"]}
    assert sorted(mapping.values()) == sorted(declared) and len(mapping) == 52
    leaf = {clause: "uaes/" + clause.split(":", 1)[0] for clause in mapping.values()}
    assert all(leaf[clause] in leaves for clause in mapping.values())
    # 同一份文件的审核行只落在一个叶子上。
    leaves_by_file: dict[str, set] = {}
    for row in audit:
        leaves_by_file.setdefault(row.keys["存储地址"], set()).add(leaf[mapping[row.keys["规则编号"]]])
    assert all(len(found) == 1 for found in leaves_by_file.values())


def test_comparison_keeps_answers_and_states_what_cannot_be_scored():
    from corpus_contracts.uaes_evaluation import DIFFERENT, MATCH, UNCOMPARABLE, compare_audit, compare_precheck

    precheck, audit = load_evaluation()
    row = next(item for item in precheck if item.excel_row == 31)  # A40-气动量仪-Cgk：不通过 1 0 B02
    actual = {"预检结果": "不通过", "阻断数": 1, "警告数": 0, "问题": ["B02"], "项目": None, "客户": None, "车型": None, "零件号": "F01RD0YA40", "零件名称": "节气门"}
    outcomes = {item.column: item.outcome for item in compare_precheck(row, actual)}
    assert outcomes == {**dict.fromkeys(("预检结果", "阻断数", "警告数", "问题", "项目", "客户", "车型", "零件号", "零件名称"), MATCH), "整改动作": UNCOMPARABLE}
    assert {item.column: item.outcome for item in compare_precheck(row, {**actual, "问题": ["W02"], "零件号": "F01RD0YA4001"})}["零件号"] == DIFFERENT
    pending = next(item for item in audit if item.answers["审核结果"] == "待裁决")
    assert {item.outcome for item in compare_audit(pending, {"status": "PASS"})} == {UNCOMPARABLE}
    fatal = next(item for item in audit if item.excel_row == 5)  # 过程流程图.xls 受控章：不通过 致命
    scored = {item.column: item.outcome for item in compare_audit(fatal, {"status": "NG", "severity": "A"})}
    assert scored == {"审核结果": MATCH, "问题等级": MATCH, "审核依据": UNCOMPARABLE, "整改建议": UNCOMPARABLE}
    assert {item.column: item.outcome for item in compare_audit(fatal, {"status": "UNABLE"})}["审核结果"] == DIFFERENT
    assert compare_audit(fatal, None)[0].outcome == DIFFERENT


def test_differences_cover_only_what_the_sample_runs_produced(tmp_path):
    from corpus_contracts.oracle import REPO_ROOT
    from corpus_contracts.uaes_evaluation import DIFFERENT, MATCH, UNCOMPARABLE, bench

    _, audit = load_evaluation()
    fatal = next(item for item in audit if item.excel_row == 5)  # 过程流程图.xls 受控章：不通过 致命
    source = str(fatal.source.relative_to(REPO_ROOT))
    report = tmp_path / "rules-1.jsonl"
    report.write_text(json.dumps({"kind": "finding", "source": source, "clause": "U16:3", "status": "NG", "severity": "A", "reason": "没有受控章"}, ensure_ascii=False) + "\n", encoding="utf-8")
    scored = {(item.excel_row, item.column): item.outcome for item in bench(tmp_path) if item.sheet == "审核明细"}
    assert scored[(5, "审核结果")] == MATCH and scored[(5, "问题等级")] == MATCH
    # 样本运行没有产出的行无法比较，不算不一致。
    assert scored[(3, "审核结果")] == UNCOMPARABLE
    assert DIFFERENT not in set(scored.values())
