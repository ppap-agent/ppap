"""V3.33 UAES 评测集载入：表头、行数、原件定位与标准答案原样。"""

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
