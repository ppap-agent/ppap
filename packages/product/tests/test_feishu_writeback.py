"""Large histories preserve document order and stop on ambiguous writes."""

import json

import httpx
import pytest
from product.feishu_writeback import FeishuWritebackError, insert_history


def test_large_history_precedes_existing_text_in_original_order():
    document = [{"block_type": 2, "text": {"elements": [{"text_run": {"content": "existing"}}]}}]
    sizes = []

    def provider(request):
        payload = json.loads(request.content)
        children = payload["children"]
        assert 1 <= len(children) <= 50
        sizes.append(len(children))
        document[payload["index"] : payload["index"]] = children
        return httpx.Response(200, json={"code": 0})

    lines = [f"history line {i}" for i in range(101)]
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        insert_history(http, "user-token", "doc-token", "\n".join(lines))
    assert sizes == [50, 50, 1]
    assert [block["text"]["elements"][0]["text_run"]["content"] for block in document] == [*lines, "existing"]


@pytest.mark.parametrize("timeout", [False, True])
def test_partial_history_failure_stops_without_replay_or_later_batches(timeout):
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 2:
            if timeout:
                raise httpx.ReadTimeout("reply lost", request=request)
            return httpx.Response(400, json={"code": 99991672})
        return httpx.Response(200, json={"code": 0})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, pytest.raises(FeishuWritebackError, match="^feishu_writeback_failed$"):
        insert_history(http, "user-token", "doc-token", "\n".join(f"line {i}" for i in range(101)))
    assert [call["index"] for call in calls] == [0, 50]


def _core_round(status="PASS"):
    return {
        "executor": "ppap-checker-core",
        "execution": "completed",
        "inputs": [
            {
                "role": "target",
                "file_version_id": "target-v",
                "original_filename": "plan.xlsx",
                "rule_version_id": "rule-v",
                "rule": {"body": {"rules": [{"id": "R1", "rule_to_check": "编号必须存在"}]}},
            }
        ],
        "findings": [
            {
                "item": "编号必须存在",
                "classification": "身份",
                "priority": "P0",
                "status": status,
                "suggestions": "已核对原件",
                "locator": {
                    "file_version_id": "target-v",
                    "semantic": {
                        "verification_scope": "checker_execution",
                        "rule_version_id": "rule-v",
                        "clause_id": "R1",
                        "rule_text": "编号必须存在",
                        "checks_performed": 1,
                        "sources": [{"file_version_id": "target-v", "filename": "plan.xlsx", "role": "target", "locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"}, "excerpt": "P-1"}],
                    },
                },
            }
        ],
    }


def test_core_assertions_allow_writeback_without_fabricating_scan_scope():
    from product.feishu_writeback import history_text, report_blocks, writeback_state

    body = _core_round()
    assert writeback_state(body) == "ready"
    assert "审核结果：PASS" in history_text(body)
    text = json.dumps(report_blocks(body), ensure_ascii=False)
    assert "Sheet1!A1" in text
    assert "条款核验记录" not in text
    assert "scope" not in body["findings"][0]["locator"]["semantic"]


@pytest.mark.parametrize("performed", [None, 0, -1, True, "1", 1.5])
def test_core_pass_without_real_positive_assertions_cannot_be_written(performed):
    from product.feishu_writeback import writeback_state

    body = _core_round()
    body["findings"][0]["locator"]["semantic"]["checks_performed"] = performed
    assert writeback_state(body) == "round_incomplete"


def test_core_explicit_empty_scope_is_distinct_from_unproven_zero_assertions():
    from product.feishu_writeback import writeback_state

    body = _core_round()
    semantic = body["findings"][0]["locator"]["semantic"]
    semantic.update(checks_performed=0, sources=[], empty_scope_basis="已核对原件，该条件没有适用行。")
    assert writeback_state(body) == "ready"
    semantic["empty_scope_basis"] = " "
    assert writeback_state(body) == "round_incomplete"


@pytest.mark.parametrize("fault", ["missing_sources", "foreign_file", "wrong_filename", "wrong_role", "bad_locator", "rule_version", "clause", "rule_text", "missing_rules", "invalid_scope"])
def test_core_writeback_retains_source_and_rule_authority(fault):
    from product.feishu_writeback import writeback_state

    body = _core_round()
    semantic = body["findings"][0]["locator"]["semantic"]
    source = semantic["sources"][0]
    if fault == "missing_sources":
        semantic["sources"] = []
    elif fault == "foreign_file":
        source["file_version_id"] = "foreign-version"
    elif fault == "wrong_filename":
        source["filename"] = "other.xlsx"
    elif fault == "wrong_role":
        source["role"] = "reference"
    elif fault == "bad_locator":
        source["locator"]["cell"] = "A0"
    elif fault == "rule_version":
        semantic["rule_version_id"] = "other-rule"
    elif fault == "clause":
        semantic["clause_id"] = "R2"
    elif fault == "rule_text":
        semantic["rule_text"] = "未确认的替代规则"
    elif fault == "missing_rules":
        body["inputs"][0]["rule"] = {}
    else:
        semantic["scope"] = {"total": 1, "checked": 2, "unknown": 0}
    assert writeback_state(body) == "round_incomplete"


@pytest.mark.parametrize("status", ["NG", "UNABLE", "NA"])
def test_core_nonpass_with_unknown_check_count_stays_nonpass(status):
    from product.feishu_writeback import history_text, writeback_state

    body = _core_round(status)
    del body["findings"][0]["locator"]["semantic"]["checks_performed"]
    assert writeback_state(body) == "ready"
    assert f"审核结果：{status}" in history_text(body)
    assert "审核结果：PASS" not in history_text(body)


def test_legacy_semantic_output_still_requires_its_scan_scope():
    from product.feishu_writeback import writeback_state

    body = _core_round()
    semantic = body["findings"][0]["locator"]["semantic"]
    del semantic["verification_scope"]
    del body["executor"]
    assert writeback_state(body) == "round_incomplete"
    semantic["scope"] = {"total": 1, "checked": 1, "unknown": 0}
    assert writeback_state(body) == "ready"


@pytest.mark.parametrize("missing", ["semantic", "verification_scope"])
def test_core_result_cannot_fall_back_to_historical_unscoped_finding(missing):
    from product.feishu_writeback import writeback_state

    body = _core_round()
    locator = body["findings"][0]["locator"]
    if missing == "semantic":
        del locator["semantic"]
    else:
        del locator["semantic"]["verification_scope"]
        locator["semantic"]["scope"] = {"total": 1, "checked": 1, "unknown": 0}
    assert writeback_state(body) == "round_incomplete"
