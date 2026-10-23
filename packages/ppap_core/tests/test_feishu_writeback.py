"""Large histories preserve document order and stop on ambiguous writes."""

import json

import httpx
import pytest
from ppap_core.feishu_writeback import FeishuWritebackError, insert_history


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
