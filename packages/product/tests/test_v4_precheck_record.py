"""Export displays the frozen scope and actual uncertainty, without new audit claims."""

from copy import deepcopy

import pytest
from product.prechecks import render_precheck_record


def receipt():
    return {
        "id": "snapshot-old",
        "created_at": "2026-11-03T12:00:00Z",
        "status": "succeeded",
        "input": {
            "task_id": "task",
            "input_revision": 1,
            "customer_preference": "",
            "effective_customer": "UAES",
            "analysis_mode": "deep",
            "files": [
                {
                    "file": {"id": "version-old", "original_filename": "<script>evil</script>.png"},
                    "role": "target",
                    "rule": {"id": "rule-old", "title": "生产件样品照片", "body": {"rule_profile": "v4", "preset_key": "U13"}},
                    "rule_execution": {"checker": {"profile": "v4", "class": "UaesPhotoAgent"}},
                }
            ],
        },
        "result": {"files": [{"file_version_id": "version-old", "facts": []}]},
    }


@pytest.mark.parametrize(
    ("status", "label", "code"),
    [
        ("unknown", "未核实", "S05"),
        ("not_checked", "未执行", None),
        ("error", "执行失败", None),
        ("handoff", "转正式审核判断", "S03"),
        ("unknown", "未核实", "S04-C"),
        ("unknown", "未核实", "S04-S"),
    ],
)
def test_export_preserves_actual_status_for_images_unavailable_sources_and_failures(status, label, code):
    saved = receipt()
    saved["result"]["files"][0]["facts"] = [
        {
            "source_id": "U13:S1",
            "status": status,
            "code": code,
            "message": "原始照片内容尚不能核实。",
            "next_step": "核对本轮原件。",
            "evidence": {"sources": [{"locator": '{"file_version_id":"version-old","page":1}', "excerpt": "<img onerror=evil>"}]},
        }
    ]
    before = deepcopy(saved)
    record = render_precheck_record(saved)
    assert saved == before
    assert record == render_precheck_record(saved)
    text = record.decode()
    for expected in [label, "UAES", "客户预置规则", "U13", "rule-old", "version-old", "UaesPhotoAgent", "原始照片内容尚不能核实。"]:
        assert expected in text
    assert "<script>" not in text and "<img onerror" not in text
    assert "&lt;script&gt;" in text and "&lt;img onerror" in text
    assert "不是正式审核合格结论" in text
    assert "access_token=" not in text


def test_custom_appendix_and_generic_fallback_do_not_become_customer_certification():
    saved = receipt()
    source = saved["input"]["files"][0]
    source["rule"] = {"id": "custom-frozen", "title": "企业规则", "body": {"rule_profile": "v3", "appendix": {"scope": "仅孔径", "text": "额外核对孔径"}}}
    source["rule_execution"]["checker"] = {"profile": "v3", "class": "GenericAgent"}
    text = render_precheck_record(saved).decode()
    for expected in ["用户自定义规则", "custom-frozen", "GenericAgent", "仅孔径", "额外核对孔径", "尚无已执行的内容预检结果", "不代替正式 Deep 自定义规则审核结论"]:
        assert expected in text
    assert "客户预置规则" not in text


@pytest.mark.parametrize(("status", "message"), [("failed", "本次预检执行失败"), ("cancelled", "本次预检已取消"), ("unknown", "本次预检执行状态尚未核实")])
def test_whole_execution_failure_is_visible_without_exposing_raw_transport_errors(status, message):
    saved = receipt()
    completed = render_precheck_record(saved)
    saved.update(status=status, result={"error": "private-service-token"})
    exported = render_precheck_record(saved)
    assert exported != completed and message in exported.decode()
    assert "private-service-token" not in exported.decode()
