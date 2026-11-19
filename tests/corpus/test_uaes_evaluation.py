"""UAES 评测集（V3.33 金标）逐行比对：AC-31 预检 58 行、审核 186 行；AC-32 缺陷样本与正常件配对。

每个零件目录（含缺陷样本）按产品路径整批提交一次；标准答案原样比对，不改金标、不改程序。
"""

import json
import os
from functools import lru_cache
from pathlib import Path

import pytest
from corpus_contracts.uaes_evaluation import DIFFERENT, UNCOMPARABLE, BatchResult, Comparison, batches, compare_batch, defect_findings, defect_pairs, run_batch, submitted_name

BATCHES = {batch.part: batch for batch in batches()}


def _selected(metafunc, parts):
    ftype = metafunc.config.getoption("--ftype")
    return [BATCHES[part] for part in parts if not ftype or BATCHES[part].has_pdf == (ftype == "pdf")]


def pytest_generate_tests(metafunc):
    # 参数名 audit_case 让同一零件目录的用例共用一份回放记录（conftest 按 audit_case.id 命名）。
    if "defect" in metafunc.fixturenames:
        pairs = [(BATCHES[_part(defect)], (defect, normal)) for defect, normal in defect_pairs()]
        selected = {batch.part for batch in _selected(metafunc, {batch.part for batch, _ in pairs})}
        pairs = [item for item in pairs if item[0].part in selected]
        metafunc.parametrize(("audit_case", "defect"), pairs, ids=[submitted_name(defect) for _, (defect, _) in pairs])
    elif "audit_case" in metafunc.fixturenames:
        selected = _selected(metafunc, BATCHES)
        metafunc.parametrize("audit_case", selected, ids=[batch.part for batch in selected])


def _part(row) -> str:
    return row.keys["存储地址"].split("/")[2]


@lru_cache(maxsize=None)
def _run(part: str) -> tuple[BatchResult, tuple[Comparison, ...]]:
    batch = BATCHES[part]
    result = run_batch(batch)
    return result, tuple(compare_batch(batch, result))


def _execute(batch, model_replay) -> tuple[Comparison, ...]:
    _, comparisons = _run(batch.part)
    if model_replay is not None:
        if model_replay.misses:
            pytest.fail("回放记录缺失：" + ", ".join(sorted(set(model_replay.misses))))
        # 一批完整执行后即保存模型回答：金标比对的差异是评测结果，不应让下次换一次回答。
        model_replay.finish(keep=True)
    return comparisons


def _write_report(rows: list[Comparison]) -> None:
    directory = os.environ.get("PPAP_CORPUS_REPORT_DIR")
    if not directory:
        return
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    with (path / f"uaes-evaluation-{os.getpid()}.jsonl").open("a", encoding="utf-8") as output:
        output.writelines(json.dumps({"kind": "uaes_evaluation", **row.__dict__}, ensure_ascii=False, default=str) + "\n" for row in rows)


@pytest.mark.usefixtures("ocr_transport_diagnostics")
def test_evaluation_rows_match_the_standard_answers(audit_case, model_replay):
    """AC-31：逐行逐列比对；不一致即失败，无法比较项附原因列出但不判分。"""
    comparisons = _execute(audit_case, model_replay)
    _write_report(list(comparisons))
    different = [item for item in comparisons if item.outcome == DIFFERENT]
    uncomparable = [item for item in comparisons if item.outcome == UNCOMPARABLE and item.column in {"审核结果", "问题"}]
    if different:
        pytest.fail(
            f"{audit_case.part}：{len(different)} 处与标准答案不一致\n" + "\n".join(item.line() for item in different) + "\n无法判分的结论项：\n" + "\n".join(item.line() for item in uncomparable)
        )


@pytest.mark.usefixtures("ocr_transport_diagnostics")
def test_defect_sample_is_detected_and_its_normal_file_is_not_flagged(audit_case, defect, model_replay):
    """AC-32：缺陷件按标注被检出（预检结果/审核结果），配对正常件不误判。"""
    comparisons = _execute(audit_case, model_replay)
    missed, false = defect_findings(*defect, list(comparisons))
    if missed or false:
        pytest.fail("漏检：\n" + "\n".join(item.line() for item in missed) + "\n误判：\n" + "\n".join(item.line() for item in false))
