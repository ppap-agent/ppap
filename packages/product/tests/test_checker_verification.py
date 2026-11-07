"""Positive disclosure contracts keep sample validation separate from PASS/NG."""

from copy import deepcopy
from html import unescape

import pytest
from product.checker_verification import frozen_checker_verification
from product.feishu_notifications import task_notification_card
from product.feishu_writeback import history_text, report_blocks
from product.store.task_view import file_round_audit, report_eligible, round_read_view


@pytest.mark.parametrize(("status", "not_rerun", "wording"), [("unverified", False, "未经验证"), ("verified", True, "平台已更新，样本未重跑"), ("verified", False, "已通过样本验证")])
def test_frozen_version_qualification_accompanies_all_pass_outputs(status, not_rerun, wording):
    execution = {"mode": "checker_core", "suite_revision_id": "frozen-suite-3", "suite_revision_number": 3, "verification": status, "samples_not_rerun": not_rerun}
    disclosure = frozen_checker_verification(execution)
    assert disclosure["status"] == status and disclosure["samples_not_rerun"] is not_rerun
    assert wording in disclosure["notice"] and "第 3 版（frozen-suite-3）" in disclosure["notice"]
    rule = {"id": "rule-1", "title": "冻结规则", "body": {"kind": "engine", "rules": [{"id": "1", "category": "资料", "rule_to_check": "填写零件号"}]}}
    source = {"role": "target", "file_version_id": "file-1", "original_filename": "原件.xlsx", "rule": rule, "rule_version_id": "rule-1", "checker_verification": disclosure}
    round = {
        "id": "round-1",
        "execution": "completed",
        "result_attempt": 1,
        "inputs": [source],
        "findings": [{"item_key": "file-1:1", "item": "零件号", "status": "PASS", "locator": {"file_version_id": "file-1", "rule_item_id": "1"}}],
    }
    audit = file_round_audit(round, source)
    assert audit["conclusion"] == "PASS" and audit["counts"]["pass_count"] == 1
    assert report_eligible(audit["checks"], False, audit["unmapped_findings"])
    assert audit["checker_verification"] == disclosure
    assert round_read_view(round, {"file-1": {"id": "file-1"}})["files"][0]["audit"]["checker_verification"] == disclosure
    task = {"id": "task-1", "number": "001", "files": [{"selection": "selected", "file": {"id": "file-1", "original_filename": "原件.xlsx"}, "audit": {**audit, "report": {"state": "ready"}}}]}
    card = task_notification_card(task, "completed", "https://example.test")
    text = unescape(card["body"]["elements"][0]["content"])
    assert disclosure["notice"] in text and "符合 1 项" in text
    blocks = report_blocks(round)
    text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
    assert disclosure["notice"] in text and "符合 1" in text
    assert disclosure["notice"] in history_text(round)
    # A later in-memory read or presentation edit cannot mutate the frozen source.
    audit["checker_verification"]["status"] = "verified" if status == "unverified" else "unverified"
    assert source["checker_verification"] == frozen_checker_verification(execution)


def test_zip_card_uses_each_frozen_report_qualification():
    verification = frozen_checker_verification({"mode": "checker_core", "suite_revision_id": "original-suite", "verification": "verified", "samples_not_rerun": True})
    reference = {"task_id": "other-task", "file_version_id": "original-file"}
    package = {
        "state": "ready",
        "reports": [reference],
        "outcomes": [{"reference": reference, "filename": "原件-审核报告.pdf", "checker_verification": deepcopy(verification)}],
        "receipt": {"request_id": "zip-request"},
    }
    card = task_notification_card({"id": "task", "number": "001", "files": []}, "zip_ready", "https://example.test", package)
    text = unescape(card["body"]["elements"][0]["content"])
    assert verification["notice"] in text
    assert "1 份在受理时平台已更新，样本未重跑" in text
