"""Model call records by round (#360): stored in call order, images once, losses counted; the operator view says what is missing."""

import json
import threading
from uuid import uuid4

import pytest
from audit.storage_errors import ObjectExists
from product.model_calls import RoundCallLog, compare, export, inventory, read_records, result_attempt, round_prefix

TENANT, ROUND = str(uuid4()), str(uuid4())
PNG = b"\x89PNG\r\n\x1a\nsynthetic"


class Objects:
    def __init__(self, fail_on: str | None = None) -> None:
        self.data: dict[str, bytes] = {}
        self.types: dict[str, str] = {}
        self.fail_on = fail_on
        self.release = threading.Event()
        self.release.set()

    def put_exclusive(self, key, body, content_type):
        self.release.wait(5)
        if self.fail_on and self.fail_on in key:
            raise OSError("object store unavailable")
        if key in self.data:
            raise ObjectExists()
        self.data[key], self.types[key] = body, content_type

    def get(self, key, *, max_bytes=None):
        return self.data[key]

    def list(self, prefix):
        return sorted(key for key in self.data if key.startswith(prefix))


def direct(call_id, clause="U08:2", answer='{"relation":"supports"}', digest="a" * 64):
    return {
        "format": "ppap-model-call/1",
        "call_id": call_id,
        "kind": "text",
        "task": "rule_text_evidence",
        "purpose": "text",
        "model": "text-m",
        "request": {"task": "rule_text_evidence", "prompt": "原文"},
        "request_sha256": digest,
        "answer": answer,
        "code": "ok",
        "scope": {"target": "file-1", "clause": clause},
    }


def log(objects, **kwargs):
    return RoundCallLog(objects, tenant_id=TENANT, round_id=ROUND, attempt=1, **kwargs)


def test_records_are_stored_by_round_in_call_order_with_images_once():
    objects, summaries = Objects(), []
    calls = log(objects, on_close=summaries.append)
    first, second = uuid4().hex, uuid4().hex
    calls.seen(first)
    # The second call's answer was corrected once: two provider requests.
    calls.seen(second)
    calls.seen(second)
    calls.write(direct(first), {"i" * 64: PNG})
    calls.write(direct(second), {"i" * 64: PNG})
    calls.progress({"phase": "checking", "stage": "review", "target": "file-1"})
    calls.begin()
    calls.write({"kind": "responses", "purpose": "coding", "request": {"input": "look"}, "response": {"output": []}, "code": "ok"}, {}, exchange=True)
    assert calls.close() == {"recorded": 3, "failed": 0, "missing": 0, "errors": []}
    root = round_prefix(TENANT, ROUND)
    assert sorted(objects.data) == [f"{root}/1/000001.json", f"{root}/1/000002.json", f"{root}/1/000003.json", f"{root}/images/{'i' * 64}"]
    assert objects.types[f"{root}/images/{'i' * 64}"] == "image/png"
    stored = json.loads(objects.data[f"{root}/1/000003.json"])
    # A request the gateway recorded itself serves the task the guest last reported.
    assert stored["scope"] == {"stage": "review", "target": "file-1"}
    assert (stored["tenant_id"], stored["round_id"], stored["attempt"], stored["sequence"]) == (TENANT, ROUND, 1, 3)
    assert summaries == [{"recorded": 3, "failed": 0, "missing": 0, "errors": []}]
    assert [json.loads(objects.data[f"{root}/1/00000{n}.json"])["provider_requests"] for n in (1, 2)] == [1, 2]
    records, _images = read_records(objects, TENANT, ROUND)
    # The gateway completed four requests; the records cover them all.
    view = inventory(ROUND, TENANT, [{"attempt": 1, "outcome": "succeeded", "summary": summaries[0], "model_requests": 4}], records)
    assert view["complete"] is True and view["notices"] == [] and view["attempts"][0]["provider_requests"] == 4
    short = inventory(ROUND, TENANT, [{"attempt": 1, "outcome": "succeeded", "summary": summaries[0], "model_requests": 5}], records)
    assert short["complete"] is False and short["notices"] == ["第 1 次执行的记录不全：网关完成了 5 次模型请求，记录只覆盖 4 次。"]


def test_a_record_must_name_a_forwarded_call_once():
    calls = log(Objects())
    with pytest.raises(ValueError, match="unknown_model_call"):
        calls.write(direct(uuid4().hex), {})
    known = uuid4().hex
    calls.seen(known)
    calls.write(direct(known), {})
    with pytest.raises(ValueError, match="unknown_model_call"):
        calls.write(direct(known), {})
    assert calls.close() == {"recorded": 1, "failed": 0, "missing": 0, "errors": []}


def test_lost_records_are_counted_and_the_round_is_marked_incomplete():
    objects = Objects(fail_on="/1/000001.json")
    calls = log(objects)
    for call_id in (first := uuid4().hex, second := uuid4().hex, uuid4().hex):
        calls.seen(call_id)
    calls.write(direct(first), {})
    calls.write(direct(second), {})
    # The third call's record never arrived.
    summary = calls.close()
    assert summary == {"recorded": 1, "failed": 1, "missing": 1, "errors": ["OSError"]}
    records, _images = read_records(objects, TENANT, ROUND)
    view = inventory(ROUND, TENANT, [{"attempt": 1, "outcome": "succeeded", "summary": summary, "model_requests": None}], records)
    assert view["complete"] is False and view["attempts"][0]["gaps"] == [1]
    assert view["notices"] == ["第 1 次执行的记录不全：1 条没有存下；1 次调用没有收到记录；缺第 1 条。"]
    assert [call["sequence"] for call in view["calls"]] == [2]


def test_a_full_queue_or_a_slow_store_never_holds_up_a_call():
    objects = Objects()
    objects.release.clear()
    calls = log(objects, max_queued_bytes=1024)
    first, second = uuid4().hex, uuid4().hex
    calls.seen(first)
    calls.seen(second)
    calls.write(direct(first), {})
    with pytest.raises(OSError):
        calls.write(direct(second), {"i" * 64: b"x" * 2048})
    summary = calls.close(timeout=0.2)
    objects.release.set()
    assert summary["failed"] == 1 and summary["errors"] == ["model_call_log_full"] and summary["unfinished"] == 1


def test_an_attempt_without_counts_is_unknown_not_complete():
    view = inventory(ROUND, TENANT, [{"attempt": 1, "outcome": "failed", "summary": None, "model_requests": None}], [])
    assert view["complete"] is None and "记录是否完整未知" in view["notices"][0]
    assert inventory(ROUND, TENANT, [], [])["complete"] is None


def test_compare_lists_each_check_item_and_where_the_rounds_differ():
    left = [{**direct("1" * 32), "attempt": 1, "sequence": 1}, {**direct("2" * 32, clause="U08:3", digest="b" * 64), "attempt": 1, "sequence": 2}]
    right = [{**direct("3" * 32, answer='{"relation":"contradicts"}'), "attempt": 2, "sequence": 1}, {**direct("4" * 32, clause="U08:3", digest="c" * 64), "attempt": 2, "sequence": 2}]
    items = {item["item"]: item for item in compare(left, right)}
    assert items["U08:2"]["finding"] == "两轮给模型的输入相同，模型的回答不同"
    assert (items["U08:2"]["same_input"], items["U08:2"]["same_output"]) == (True, False)
    assert [call["answer"] for call in items["U08:2"]["left"] + items["U08:2"]["right"]] == [{"relation": "supports"}, {"relation": "contradicts"}]
    assert items["U08:3"]["finding"] == "两轮给模型的输入不同"
    assert [item["item"] for item in compare(left, right, clause="U08:3")] == ["U08:3"]
    assert result_attempt([{"attempt": 1, "outcome": "failed"}, {"attempt": 2, "outcome": "succeeded"}], left) == 2


def test_export_writes_records_images_and_a_replay_file(tmp_path):
    objects = Objects()
    calls = log(objects)
    call_id = uuid4().hex
    calls.seen(call_id)
    calls.write(direct(call_id), {"i" * 64: PNG})
    calls.close()
    records, images = read_records(objects, TENANT, ROUND)
    assert export(objects, TENANT, ROUND, records, images, tmp_path) == {"directory": str(tmp_path), "records": 1, "images": 1, "replay_entries": 1}
    assert (tmp_path / "images" / ("i" * 64)).read_bytes() == PNG
    assert json.loads((tmp_path / "1" / "000001.json").read_text())["call_id"] == call_id
    (entry,) = json.loads((tmp_path / "replay.json").read_text())
    assert entry == {name: records[0][name] for name in ("kind", "model", "request", "request_sha256", "answer")}


def test_the_operator_command_lists_and_compares_rounds(monkeypatch, capsys):
    import product.model_calls as model_calls
    import product.storage

    objects, other = Objects(), str(uuid4())
    for round_id, answer in ((ROUND, '{"relation":"supports"}'), (other, '{"relation":"contradicts"}')):
        calls = RoundCallLog(objects, tenant_id=TENANT, round_id=round_id, attempt=1)
        call_id = uuid4().hex
        calls.seen(call_id)
        calls.write(direct(call_id, answer=answer), {})
        calls.close()
    summary = {"recorded": 1, "failed": 0, "missing": 0, "errors": []}
    monkeypatch.setattr(model_calls, "read_round", lambda _settings, round_id: (TENANT, [{"attempt": 1, "outcome": "succeeded", "summary": summary, "model_requests": 1}]))
    monkeypatch.setattr(product.storage, "S3Objects", lambda _settings: objects)

    assert model_calls.main([ROUND]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["complete"] is True and [call["clause"] for call in listed["calls"]] == ["U08:2"]
    assert model_calls.main([ROUND, "--compare", other, "--clause", "U08:2"]) == 0
    compared = json.loads(capsys.readouterr().out)
    assert compared["left"] == {"round_id": ROUND, "attempt": 1, "complete": True}
    assert [item["finding"] for item in compared["items"]] == ["两轮给模型的输入相同，模型的回答不同"]
