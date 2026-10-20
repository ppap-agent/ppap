"""Real rule coverage is not proof that customer template files exist."""

import uuid

from fastapi.testclient import TestClient
from ppap_api import app, create_app
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings


def test_template_catalog_requires_login_and_has_a_runtime_contract():
    response = TestClient(app).get("/audit-template-catalog")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    spec = app.openapi()
    operation = spec["paths"]["/audit-template-catalog"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AuditTemplateCatalog"}
    assert "x-contract-stage" not in spec["components"]["schemas"]["AuditTemplateCatalog"]
    assert "x-contract-stage" not in spec["components"]["schemas"]["AuditTemplatePage"]
    assert "x-contract-stage" not in spec["components"]["schemas"]["AuditTemplateZip"]


def test_rule_library_requires_login_and_declares_its_metadata_contract():
    response = TestClient(app).get("/rule-library")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    operation = app.openapi()["paths"]["/rule-library"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/RuleLibrary"}


def test_catalog_uses_current_tenant_published_rules_not_rule_files_or_history(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="catalog-test"), reset=False)
    clients = []
    for _ in range(2):
        principal = store.upsert_feishu_identity("catalog-test-" + uuid.uuid4().hex, "catalog-user")
        client = TestClient(create_app(store.settings, auth_store=store))
        client.cookies.set(SESSION_COOKIE, store.create_session(principal))
        clients.append(client)
    first, second = clients
    try:
        empty = first.get("/audit-template-catalog").json()
        assert len(empty["coverage_universe"]) == len(set(empty["coverage_universe"])) == 17
        assert "设计失效模式及后果分析" not in empty["coverage_universe"]
        assert empty["customers"] == []

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
        assert first.get("/audit-template-catalog").json()["customers"] == [{"customer_id": "旧客户", "name": "旧客户", "effective_rule_types": ["控制计划"]}]
        publish(first, ["Customer A"], "过程流程图", old)
        publish(first, [" customer a ", "Customer A"], "Control plan")
        publish(first, ["Customer A"], "过程流程图")
        publish(first, [], "全功能检验报告")
        publish(first, ["DFMEA only"], "设计失效模式及后果分析")
        result = first.get("/audit-template-catalog")
        assert result.headers["cache-control"] == "private, no-store"
        customers = result.json()["customers"]
        assert len(customers) == 2
        assert customers[0]["customer_id"] == "customer a"
        assert customers[0]["name"].casefold() == "customer a"
        assert sorted(customers[0]["effective_rule_types"]) == ["控制计划", "过程流程图"]
        assert set(customers[0]) == {"customer_id", "name", "effective_rule_types"}
        assert customers[1] == {"customer_id": "dfmea only", "name": "DFMEA only", "effective_rule_types": ["设计失效模式及后果分析"]}
        assert second.get("/audit-template-catalog").json()["customers"] == [{"customer_id": "其他企业客户", "name": "其他企业客户", "effective_rule_types": ["控制计划"]}]
        library = first.get("/rule-library")
        assert library.headers["cache-control"] == "private, no-store"
        versions = library.json()["versions"]
        assert len(versions) == 6
        assert any(version["id"] == old for version in versions)
        assert any(version["predecessor_id"] == old for version in versions)
        assert all(set(version) == {"id", "title", "version", "scope", "reason", "created_at", "predecessor_id", "clause_count"} and version["clause_count"] == 1 for version in versions)
        assert not {version["id"] for version in versions} & {version["id"] for version in second.get("/rule-library").json()["versions"]}
        assert first.get("/audit-tasks").json()["tasks"] == []
        assert first.get("/rounds").json()["rounds"] == []
        assert first.get("/usage").json()["events"] == []
    finally:
        for client in clients:
            client.close()
