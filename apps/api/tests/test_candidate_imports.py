"""Candidate guards use real tenant transactions and controlled, model-free authors."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.files import ObjectExists, ObjectMissing
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.suites import SuiteStore

FACTORY = b"def create_audit(audit_file, rule, group_context=None):\n    return None\n"
OLD_IMPORT = b"from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent\n"
RULES = {
    "kind": "engine",
    "type": "自定义",
    "customers": ["UAES"],
    "rules": [{"id": "R1", "category": "数值", "rule_to_check": "A1至少10", "type_of_rule": "text"}],
    "checker_binding": {"R1": "checker:create_audit"},
}


class Objects:
    def __init__(self):
        self.data = {}

    def put_exclusive(self, key, body, content_type):
        if key in self.data:
            raise ObjectExists()
        self.data[key] = body

    def get(self, key, *, max_bytes=None):
        if key not in self.data:
            raise ObjectMissing()
        body = self.data[key]
        if max_bytes is not None and len(body) > max_bytes:
            raise ValueError("too_large")
        return body


def factory_revision(store, **changes):
    files = {"checker.py": FACTORY, "resources.py": b"VALUE = 1\n", "rules.json": json.dumps(RULES).encode(), **changes}
    return SuiteStore(store.settings, store.objects, role="migrator").create_revision(tenant_id=None, suite_key="control-plan", files=files, created_by=None)


def system(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="candidate-guard", usage_charge_enabled=False, git_sha="a" * 40), objects=Objects())
    principal = store.upsert_feishu_identity("candidate-guard", "operator")
    api = build_product_store(store.settings, objects=store.objects, role="api")
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    client = TestClient(create_app(store.settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(principal))
    return SimpleNamespace(store=store, worker=worker, principal=principal, client=client, selected=factory_revision(store))


@pytest.mark.parametrize(
    "changes",
    [
        {"checker.py": (OLD_IMPORT + FACTORY).decode()},
        {"resources.py": OLD_IMPORT.decode()},
        {"resources.py": "import importlib\nOLD = importlib.import_module('audit.checker_lib.uaes.control_plan')\n"},
        {
            "checker.py": (OLD_IMPORT + b"def check(context):\n    return []\n").decode(),
            "rules.json": json.dumps({**RULES, "checker_binding": {"R1": "checker:check"}}),
        },
    ],
)
def test_manual_factory_candidate_rejects_old_imports_without_saving(auth_store_factory, changes):
    f = system(auth_store_factory)
    path = f"/suite-revisions/{f.selected.id}/candidates"
    operation = str(uuid4())
    response = f.client.post(path, json={"operation_id": operation, "files": changes})
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "checker_cross_version_import"}
    assert f.client.get(path + "/" + operation).status_code == 404
    assert f.client.get(f"/suite-revisions/{f.selected.id}").status_code == 200
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id IS NOT NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_candidate_operations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0


def test_manual_factory_change_checks_the_inherited_resource(auth_store_factory):
    f = system(auth_store_factory)
    selected = factory_revision(f.store, **{"resources.py": OLD_IMPORT})
    response = f.client.post(f"/suite-revisions/{selected.id}/candidates", json={"files": {"checker.py": FACTORY.decode() + "# changed\n"}})
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "checker_cross_version_import"}


@pytest.mark.parametrize(
    "source",
    [
        OLD_IMPORT + FACTORY,
        b"from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent as create_audit\n",
        OLD_IMPORT + b"create_audit = UaesControlPlanAgent\n",
    ],
    ids=["factory-definition", "factory-import", "factory-assignment"],
)
def test_new_factory_format_is_checked_when_the_base_is_a_native_carrier(auth_store_factory, source):
    f = system(auth_store_factory)
    selected = factory_revision(f.store, **{"checker.py": b"def check(context): return []\n", "rules.json": b'{"rules":[{"id":"R1","rule_to_check":"Required"}]}'})
    response = f.client.post(f"/suite-revisions/{selected.id}/candidates", json={"files": {"checker.py": source.decode()}})
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "checker_cross_version_import"}


@pytest.mark.parametrize("same_program", [False, True])
def test_unchanged_historical_program_can_be_read_and_have_rules_edited(auth_store_factory, same_program):
    f = system(auth_store_factory)
    selected = factory_revision(f.store, **{"checker.py": OLD_IMPORT + FACTORY})
    changes = {"rules.json": json.dumps({**RULES, "title": "Updated title"})}
    if same_program:
        changes["checker.py"] = (OLD_IMPORT + FACTORY).decode()
    response = f.client.post(f"/suite-revisions/{selected.id}/candidates", json={"files": changes})
    assert response.status_code == 201, response.text
    resolved = SuiteStore(f.store.settings, f.store.objects).resolve_suite(response.json()["id"], f.principal.tenant_id)
    assert resolved.files["checker.py"] == OLD_IMPORT + FACTORY
    assert f.client.get(f"/suite-revisions/{selected.id}").status_code == 200
