"""Rule customer choices use only the current tenant's published versions."""

import uuid
from types import SimpleNamespace

from api import app, create_app
from api.rule_routes import customer_catalog
from fastapi.testclient import TestClient
from product.identity import SESSION_COOKIE
from product.settings import Settings


def test_customer_catalog_requires_login_and_has_a_minimal_runtime_contract():
    response = TestClient(app).get("/audit-customers")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    spec = app.openapi()
    operation = spec["paths"]["/audit-customers"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AuditCustomerCatalog"}
    assert "x-contract-stage" not in spec["components"]["schemas"]["AuditCustomerCatalog"]
    schemas = spec["components"]["schemas"]
    assert set(schemas["AuditCustomerCatalog"]["properties"]) == {"customers"}
    assert set(schemas["AuditCustomer"]["properties"]) == {"customer_id", "name"}
    assert "/audit-template-catalog" not in spec["paths"]
    assert not any(path.startswith("/audit-template") for path in spec["paths"])
    assert not any(name.startswith("AuditTemplate") for name in schemas)


def test_rule_library_requires_login_and_declares_its_metadata_contract():
    response = TestClient(app).get("/rule-library")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    operation = app.openapi()["paths"]["/rule-library"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/RuleLibrary"}


def test_customer_catalog_keeps_latest_rule_versions_tenant_scope_and_unique_names():
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
            rule("dfmea", ["DFMEA only"], document_type="设计失效模式及后果分析"),
        ],
        "second": [rule("separate", ["其他企业客户"])],
    }
    store = SimpleNamespace(list_rules=lambda tenant_id: rules[tenant_id])

    assert customer_catalog(store, "first") == {"customers": [{"customer_id": "customer a", "name": "customer a"}, {"customer_id": "dfmea only", "name": "DFMEA only"}]}
    assert customer_catalog(store, "second") == {"customers": [{"customer_id": "其他企业客户", "name": "其他企业客户"}]}


def test_catalog_uses_current_tenant_published_rules_not_rule_files_or_history(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="catalog-test"), reset=False)
    clients = []
    for _ in range(2):
        principal = store.upsert_feishu_identity("catalog-test-" + uuid.uuid4().hex, "catalog-user")
        client = TestClient(create_app(store.settings, auth_store=store))
        client.cookies.set(SESSION_COOKIE, store.create_session(principal))
        clients.append(client)
    first, second = clients

    def custom_choices(result):
        return [row for row in result["customers"] if row["customer_id"] not in {"uaes", "理想", "蔚来"}]

    try:
        empty = first.get("/audit-customers").json()
        assert {row["customer_id"] for row in empty["customers"]} == {"uaes", "理想", "蔚来"}
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
        assert custom_choices(first.get("/audit-customers").json()) == [{"customer_id": "旧客户", "name": "旧客户"}]
        publish(first, ["Customer A"], "过程流程图", old)
        publish(first, [" customer a ", "Customer A"], "Control plan")
        publish(first, ["Customer A"], "过程流程图")
        publish(first, [], "全功能检验报告")
        publish(first, ["DFMEA only"], "设计失效模式及后果分析")
        result = first.get("/audit-customers")
        assert result.headers["cache-control"] == "private, no-store"
        customers = custom_choices(result.json())
        assert len(customers) == 2
        assert customers[0]["customer_id"] == "customer a"
        assert customers[0]["name"].casefold() == "customer a"
        assert set(customers[0]) == {"customer_id", "name"}
        assert customers[1] == {"customer_id": "dfmea only", "name": "DFMEA only"}
        assert custom_choices(second.get("/audit-customers").json()) == [{"customer_id": "其他企业客户", "name": "其他企业客户"}]
        library = first.get("/rule-library")
        assert library.headers["cache-control"] == "private, no-store"
        versions = library.json()["versions"]
        assert len(versions) == 6
        assert any(version["id"] == old for version in versions)
        assert any(version["predecessor_id"] == old for version in versions)
        version_fields = {"id", "title", "version", "scope", "reason", "created_at", "predecessor_id", "clause_count", "document_type"}
        assert all(set(version) == version_fields and version["clause_count"] == 1 for version in versions)
        assert not {version["id"] for version in versions} & {version["id"] for version in second.get("/rule-library").json()["versions"]}
        assert first.get("/audit-tasks").json()["tasks"] == []
        assert first.get("/rounds").json()["rounds"] == []
        assert first.get("/usage").json()["events"] == []
    finally:
        for client in clients:
            client.close()
