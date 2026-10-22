"""Provider receipts do not turn an uncertain delivery into a retry permit."""

import json

import httpx
import pytest
from ppap_core.report_sharing import ReportDirectoryHttp, ReportSharingError, list_report_targets, own_report_target, resolve_report_target, send_report_message, upload_report_message_file


def test_message_processing_and_lost_receipts_remain_unknown():
    answers = [
        httpx.Response(400, json={"code": 230049, "msg": "sending"}),
        httpx.Response(200, json={"code": 0, "data": {}}),
        httpx.Response(503, json={"code": 230002}),
    ]
    for answer in answers:
        with httpx.Client(transport=httpx.MockTransport(lambda _request: answer)) as http:
            result = send_report_message(http, "test-token", {"id": "ou_reader", "kind": "user"}, "attempt-one", "file", {"file_key": "frozen-file"})
            assert result == {"phase": "unknown", "reason": "发送结果尚未确认，请勿重新发送。", "message_id": None}


def test_only_a_verified_message_id_proves_sent_and_exact_rejection_permits_retry():
    def provider(request):
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.path == "/open-apis/im/v1/messages"
        assert request.url.params["receive_id_type"] == "chat_id"
        assert json.loads(request.content) == {"receive_id": "oc_group", "msg_type": "file", "content": '{"file_key": "frozen-file"}', "uuid": "attempt-one"}
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_ack"}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        assert send_report_message(http, "test-token", {"id": "oc_group", "kind": "group"}, "attempt-one", "file", {"file_key": "frozen-file"}) == {
            "phase": "sent",
            "reason": None,
            "message_id": "om_ack",
        }
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"code": 230006}))) as http:
        assert send_report_message(http, "test-token", {"id": "ou_reader", "kind": "user"}, "attempt-one", "file", {})["phase"] == "failed"


def test_groups_require_explicit_internal_organization_and_active_users_require_full_status():
    data = {"name": "内部同名群", "tenant_key": "tenant-one", "external": False, "chat_mode": "group"}

    def provider(request):
        assert request.headers["authorization"] == "Bearer tenant-token"
        return httpx.Response(200, json={"code": 0, "data": data})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        target = {"id": "oc_group", "kind": "group"}
        assert resolve_report_target(http, "tenant-token", "tenant-one", target)["eligibility"] == "eligible"
        del data["external"]
        assert resolve_report_target(http, "tenant-token", "tenant-one", target)["eligibility"] == "unknown"
        data["external"] = True
        assert resolve_report_target(http, "tenant-token", "tenant-one", target)["eligibility"] == "blocked"
        data.update(external=False, tenant_key="tenant-two")
        assert resolve_report_target(http, "tenant-token", "tenant-one", target)["eligibility"] == "blocked"
        data.clear()
        data["user"] = {"open_id": "ou_reader", "name": "审核人", "status": {"is_activated": True, "is_resigned": False, "is_frozen": False, "is_exited": False, "is_unjoin": False}}
        target = {"id": "ou_reader", "kind": "user"}
        assert resolve_report_target(http, "tenant-token", "tenant-one", target)["eligibility"] == "eligible"
        del data["user"]["status"]["is_resigned"]
        assert resolve_report_target(http, "tenant-token", "tenant-one", target)["eligibility"] == "unknown"


def test_directory_search_binds_the_user_token_and_rechecks_each_returned_user():
    from types import SimpleNamespace

    principal = SimpleNamespace(tenant_key="tenant-one", subject="ou_self", display_name="自己")
    calls = []

    def provider(request):
        calls.append(request)
        if request.url.path.endswith("/search/v1/user"):
            assert request.headers["authorization"] == "Bearer user-token"
            assert dict(request.url.params) == {"page_size": "20", "query": "张华枫", "page_token": "next-one"}
            return httpx.Response(200, json={"code": 0, "data": {"users": [{"open_id": "ou_reader", "name": "同名用户"}], "has_more": True, "page_token": "next-two"}})
        assert request.url.path.endswith("/contact/v3/users/ou_reader")
        assert request.headers["authorization"] == "Bearer tenant-token"
        # A search hit alone cannot replace the required current status fields.
        return httpx.Response(200, json={"code": 0, "data": {"user": {"open_id": "ou_reader", "name": "同名用户"}}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        page = list_report_targets(http, "user-token", "tenant-token", principal, {}, "user", "张华枫", "next-one")
        assert page["organization_id"] == "tenant-one"
        assert page["next_page_token"] == "next-two"
        assert page["targets"][0]["eligibility"] == "unknown"
        self_page = list_report_targets(http, "user-token", "", principal, {"name": "已核对的自己", "open_id": "ou_self", "tenant_key": "tenant-one"}, "user", "", None)
        assert self_page["targets"][0]["id"] == "ou_self"
        assert len(calls) == 2


def test_self_target_uses_verified_current_identity_not_an_unrelated_directory_grant():
    from types import SimpleNamespace

    principal = SimpleNamespace(tenant_key="tenant-one", subject="ou_self", display_name="自己")
    profile = {"open_id": "ou_self", "tenant_key": "tenant-one", "name": "已核验本人"}
    assert own_report_target(principal, profile) == {
        "id": "ou_self",
        "kind": "user",
        "display_name": "已核验本人",
        "organization_id": "tenant-one",
        "eligibility": "eligible",
        "reason": None,
    }
    for changed in ({"open_id": "ou_other"}, {"tenant_key": "tenant-other"}, {"tenant_key": None}):
        with pytest.raises(ReportSharingError, match="share_identity_mismatch"):
            own_report_target(principal, {**profile, **changed})


def test_message_upload_uses_im_file_keys_and_rejects_oversize_before_any_upload():
    calls = []

    def provider(request):
        calls.append(request)
        assert request.url.path == "/open-apis/im/v1/files"
        assert b"%PDF-frozen" in request.content
        return httpx.Response(200, json={"code": 0, "data": {"file_key": "im_frozen"}})

    artifact = {"filename": "审核报告.pdf", "content_type": "application/pdf"}
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        assert upload_report_message_file(http, "tenant-token", artifact, b"%PDF-frozen") == "im_frozen"
        with pytest.raises(ReportSharingError, match="share_file_too_large"):
            upload_report_message_file(http, "tenant-token", artifact, b"x" * (30 * 1024 * 1024 + 1))
        assert len(calls) == 1


@pytest.mark.parametrize("query", ["", "验收"])
def test_group_directory_uses_the_bot_membership_not_the_user_chat_scope(query):
    from types import SimpleNamespace

    principal = SimpleNamespace(tenant_key="tenant-one", subject="ou_self", display_name="自己")

    def provider(request):
        assert request.headers["authorization"] == "Bearer tenant-token"
        if request.url.path in {"/open-apis/im/v1/chats", "/open-apis/im/v1/chats/search"}:
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"chat_id": "oc_test"}], "has_more": False}})
        assert request.url.path == "/open-apis/im/v1/chats/oc_test"
        return httpx.Response(200, json={"code": 0, "data": {"name": "隔离验收群", "external": False, "tenant_key": "tenant-one", "chat_mode": "group"}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        page = list_report_targets(http, "user-token", "tenant-token", principal, {}, "group", query, None)
        assert page["targets"][0]["eligibility"] == "eligible"


def test_ungranted_group_directory_is_distinct_from_a_temporary_provider_failure():
    from types import SimpleNamespace

    principal = SimpleNamespace(tenant_key="tenant-one", subject="ou_self", display_name="自己")
    for status, code, expected in [(400, 99991672, "share_directory_permission_missing"), (503, 99991672, "share_directory_unavailable"), (400, 99991663, "share_directory_unavailable")]:
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"code": code, "msg": "private provider diagnostic"}))) as http:
            with pytest.raises(ReportSharingError, match="^" + expected + "$"):
                list_report_targets(http, "user-token", "tenant-token", principal, {}, "group", "", None)


@pytest.mark.parametrize("content_type,filename", [("application/pdf", "审核报告.pdf"), ("application/zip", "审核报告.zip")])
def test_missing_attachment_permission_is_not_an_unconfirmed_message_delivery(content_type, filename):
    for status, expected in [(400, "share_upload_permission_missing"), (503, "share_upload_unconfirmed")]:
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"code": 99991672, "msg": "private provider diagnostic"}))) as http:
            with pytest.raises(ReportSharingError, match="^" + expected + "$"):
                upload_report_message_file(http, "tenant-token", {"filename": filename, "content_type": content_type}, b"frozen-report-content")


def test_basic_contact_identity_without_name_or_status_never_becomes_an_eligible_recipient():
    for user in [{"open_id": "ou_reader", "union_id": "on_reader"}, {"open_id": "ou_reader", "name": "组织成员"}]:
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"code": 0, "data": {"user": user}}))) as http:
            target = resolve_report_target(http, "tenant-token", "tenant-one", {"id": "ou_reader", "kind": "user"})
            assert target["eligibility"] == "unknown"
            assert target["reason"] == "未取得完整的成员姓名或账号状态，请检查通讯录字段权限与数据范围。"


@pytest.mark.parametrize("kind", ["user", "group"])
def test_directory_budget_keeps_verified_targets_and_never_accepts_late_or_unchecked_targets(monkeypatch, kind):
    from types import SimpleNamespace

    clock = [100.0]
    monkeypatch.setattr("ppap_core.report_sharing.monotonic", lambda: clock[0])
    principal = SimpleNamespace(tenant_key="tenant-one", subject="ou_self", display_name="自己")
    ids = [("ou_" if kind == "user" else "oc_") + str(index) for index in range(20)]
    calls = []

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            clock[0] += 5
            key, rows_key = ("open_id", "users") if kind == "user" else ("chat_id", "items")
            data = {rows_key: [{key: value} for value in ids], "has_more": True, "page_token": "next-page"}
        else:
            clock[0] += 10 if len(calls) == 2 else 20
            identity = request.url.path.rsplit("/", 1)[1]
            data = (
                {"user": {"open_id": identity, "name": "组织成员", "status": {"is_activated": True, "is_resigned": False, "is_frozen": False, "is_exited": False, "is_unjoin": False}}}
                if kind == "user"
                else {"name": "内部群", "external": False, "tenant_key": "tenant-one", "chat_mode": "group"}
            )
        return httpx.Response(200, json={"code": 0, "data": data})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        budget = ReportDirectoryHttp(http)
        page = list_report_targets(budget, "user-token", "tenant-token", principal, {}, kind, "查询", None)
    assert len(calls) == 3
    assert [request.extensions["timeout"]["read"] for request in calls] == [20, 20, 15]
    assert budget.timed_out is True
    assert [target["id"] for target in page["targets"]] == ids
    assert page["next_page_token"] == "next-page"
    assert page["targets"][0]["eligibility"] == "eligible"
    assert all(target["eligibility"] == "unknown" and "超时" in target["reason"] for target in page["targets"][1:])


def test_expired_directory_budget_never_starts_a_request(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("ppap_core.report_sharing.monotonic", lambda: clock[0])
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))) as http:
        budget = ReportDirectoryHttp(http)
        clock[0] = 30.0
        for method in (budget.get, budget.post):
            with pytest.raises(ReportSharingError, match="^share_directory_timeout$"):
                method("https://open.feishu.cn/open-apis/test", timeout=20)
    assert calls == []


def test_directory_timeouts_share_the_remaining_budget_without_retrying_details(monkeypatch):
    from types import SimpleNamespace

    clock = [0.0]
    monkeypatch.setattr("ppap_core.report_sharing.monotonic", lambda: clock[0])
    calls = []
    principal = SimpleNamespace(tenant_key="tenant-one", subject="ou_self", display_name="自己")

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"code": 0, "data": {"users": [{"open_id": "ou_" + str(index)} for index in range(20)], "has_more": False}})
        clock[0] += request.extensions["timeout"]["read"]
        raise httpx.ReadTimeout("private provider details", request=request)

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        page = list_report_targets(ReportDirectoryHttp(http), "user-token", "tenant-token", principal, {}, "user", "查询", None)
    assert len(calls) == 3
    assert [request.extensions["timeout"]["read"] for request in calls] == [20, 20, 10]
    assert all(target["eligibility"] == "unknown" for target in page["targets"])
    assert all("超时" in target["reason"] for target in page["targets"][1:])
    assert "private provider details" not in json.dumps(page)
