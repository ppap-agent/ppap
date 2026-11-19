"""UAES 样本按零件目录整批运行（含缺陷样本），产出预检与审核结果。

真值比对在 ``test_cases.py``（人工真值）；这里只确认整批跑通，并在设置 ``PPAP_CORPUS_REPORT_DIR`` 时把结果写进报告，
供与评测集做差异分析：``python -m corpus_contracts.uaes_evaluation <报告目录>``。
"""

import json
import os
from pathlib import Path

from corpus_contracts.uaes_samples import Batch, batches, run_batch

BATCHES = batches()


def pytest_generate_tests(metafunc):
    # 参数名 audit_case 让同一零件目录的批次共用一份回放记录（conftest 按 audit_case.id 命名）。
    if "audit_case" in metafunc.fixturenames:
        ftype = metafunc.config.getoption("--ftype")
        selected = [batch for batch in BATCHES if not ftype or batch.has_pdf == (ftype == "pdf")]
        metafunc.parametrize("audit_case", selected, ids=[batch.label for batch in selected])


def test_sample_batch_runs_through_the_product_path(audit_case: Batch, model_replay):
    report = run_batch(audit_case)
    assert {row["source"] for row in report if row["kind"] == "precheck"} == {str(path.relative_to(Path(__file__).resolve().parents[2])) for path in audit_case.files}
    directory = os.environ.get("PPAP_CORPUS_REPORT_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        with (path / f"samples-{os.getpid()}.jsonl").open("a", encoding="utf-8") as output:
            output.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in report)
