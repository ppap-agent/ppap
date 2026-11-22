"""Rule customer choices use only the current tenant's published versions."""

import uuid
from types import SimpleNamespace

from api import app, create_app
from api.rule_routes import customer_catalog
from fastapi.testclient import TestClient
from product.identity import SESSION_COOKIE
from product.settings import Settings


def test_customer_catalog_requires_login_and_is_not_cached():
    response = TestClient(app).get("/audit-customers")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"


def test_customer_catalog_lists_registered_customers_and_unregistered_names_as_recorded(tmp_path):
    def rule(identifier, customers, *, predecessor=None, kind="engine", document_type="控制计划", created="2026-10-20"):
        return {"id": identifier, "title": "规则", "customers": customers, "predecessor_id": predecessor, "kind": kind, "type": document_type, "created_at": created}

    rules = {
        "first": [
            rule("old", ["旧客户"], created="2026-10-19"),
            rule("new", ["Customer A"], predecessor="old"),
            rule("duplicate", [" customer a ", "Customer A", "", None]),
            rule("all-customers", []),
            rule("invalid-customers", "非列表"),
            rule("unknown-type", ["不应出现"], document_type="unknown"),
            rule("other-kind", ["不应出现"], kind="other"),
            rule("look-alike", ["UAES（联电）"]),
            rule("li-plan", ["理想汽车"]),
            rule("li-plan-again", ["理想"]),
            rule("li-flow", ["理想"], document_type="过程流程图"),
        ],
        "second": [rule("separate", ["其他企业客户"])],
    }

    def store(tenant):
        return SimpleNamespace(list_rules=lambda tenant_id: rules[tenant_id], settings=Settings(template_root=str(tmp_path)))

    first = customer_catalog(store("first"), "first")
    # Rules name a customer by its key; any other name (a former full name included) is kept as recorded, unregistered.
    assert first["unregistered"] == ["Customer A", "UAES（联电）", "customer a", "理想汽车"]
    assert [(row["customer_id"], row["name"], row["key"]) for row in first["customers"]] == [("uaes", "联电科技/UAES", "UAES"), ("li", "理想/Li Auto", "理想"), ("nio", "蔚来/Nio", "蔚来")]
    li = first["customers"][1]["coverage"]
    # Only the key counts for 理想; one type with two rules counts once; no template files here.
    assert (li["version"], len(li["types"]), li["rule_types"], li["template_types"]) == ("V3.33", 17, ["T09", "T11"], [])
    assert {"code": "T11", "rule_type": "控制计划"} in li["types"]
    # One name per type code, counterparts included.
    assert (first["type_names"]["T11"], first["type_names"]["E11"], first["type_names"]["E12"]) == ("控制计划", "顾客工程批准", "采购件质量计划（PGQP1）")
    assert customer_catalog(store("second"), "second")["unregistered"] == ["其他企业客户"]


def test_catalog_uses_current_tenant_published_rules_not_rule_files_or_history(auth_store_factory):
    tenants = ["catalog-test-" + uuid.uuid4().hex for _ in range(2)]
    store = auth_store_factory(Settings(feishu_app_id="catalog-test", platform_operators=",".join(tenant + ":catalog-user" for tenant in tenants)), reset=False)
    clients, principals = [], []
    for tenant in tenants:
        principal = store.upsert_feishu_identity(tenant, "catalog-user")
        client = TestClient(create_app(store.settings, auth_store=store))
        client.cookies.set(SESSION_COOKIE, store.create_session(principal))
        clients.append(client)
        principals.append(principal)
    first, second = clients

    def custom_choices(result):
        return result["unregistered"]

    try:
        empty = first.get("/audit-customers").json()
        assert [row["customer_id"] for row in empty["customers"]] == ["uaes", "li", "nio"]
        # Installed presets give every registered customer its full rule coverage.
        assert {row["customer_id"]: (len(row["coverage"]["rule_types"]), len(row["coverage"]["types"])) for row in empty["customers"]} == {"uaes": (19, 19), "li": (17, 17), "nio": (5, 5)}
        # PRD §1.5: 蔚来's measurement report is T01a, 理想's inspection report T01; both rules read as 全功能检验报告.
        coverage = {row["customer_id"]: row["coverage"] for row in empty["customers"]}
        assert "T01a" in coverage["nio"]["rule_types"] and "T01" in coverage["li"]["rule_types"]
        assert {"code": "T01a", "rule_type": "全功能检验报告"} in coverage["nio"]["types"]
        assert custom_choices(empty) == []

        def publish(client, customers, kind, predecessor=None):
            content = b"test rule source"
            version = client.post("/files", json={"original_filename": "catalog-rules.md", "byte_size": len(content), "content_type": "text/markdown"}).json()
            path = f"/files/{version['file_id']}/versions/{version['id']}"
            assert client.put(path + "/blob", content=content).status_code == 200
            assert client.post(path + "/finalize").status_code == 200
            body = {"kind": "engine", "type": kind, "customers": customers, "rules": [{"id": "1", "category": "检查", "rule_to_check": "应填写零件号", "type_of_rule": "text", "priority": "P1"}]}
            response = client.post("/rules", json={"title": "目录测试规则", "body": body, "source_file_version_id": version["id"], "predecessor_id": predecessor})
            assert response.status_code == 201
            return response.json()["id"]

        publish(second, ["其他企业客户"], "控制计划")
        old = publish(first, ["旧客户"], "控制计划")
        assert custom_choices(first.get("/audit-customers").json()) == ["旧客户"]
        publish(first, ["Customer A"], "控制计划", old)
        publish(first, [" customer a ", "Customer A"], "Control plan")
        publish(first, ["Customer A"], "过程流程图")
        publish(first, [], "全功能检验报告")
        publish(first, ["DFMEA only"], "设计失效模式及后果分析")
        result = first.get("/audit-customers")
        assert result.headers["cache-control"] == "private, no-store"
        # Recorded names stay apart; nothing is folded into one id.
        assert custom_choices(result.json()) == ["Customer A", "DFMEA only", "customer a"]
        assert custom_choices(second.get("/audit-customers").json()) == ["其他企业客户"]
        assert first.get("/audit-tasks").json()["tasks"] == []
        assert store.list_rounds(principals[0]) == []
        assert store.list_usage_events(principals[0]) == []
    finally:
        for client in clients:
            client.close()
