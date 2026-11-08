"""Preparation freshness includes the historical package a checker names."""

from types import SimpleNamespace

from audit.standard_suites import execution_library_revision, library_revision


class _Suite:
    def __init__(self, files):
        self.files = files


def test_precheck_current_expires_only_the_family_a_checker_names(monkeypatch):
    from audit import standard_suites as suites
    from product.store.prechecks import precheck_suites_current

    box = {"files": {"checker.py": b"from audit.checkers.v4.uaes import Agent\n"}}

    class FakeSuites:
        def __init__(self, settings, objects, role):
            del settings, objects, role

        def resolve_suite(self, revision_id, tenant_id, conn=None):
            del revision_id, tenant_id, conn
            return _Suite(box["files"])

    monkeypatch.setattr("product.store.suites.SuiteStore", FakeSuites)
    monkeypatch.setattr(
        "product.store.suite_releases.current_selection",
        lambda conn, tenant_id, suite_key: {"revision_id": "rev", "resolved_digest": "digest", "related_revision_ids": {}},
    )
    store = SimpleNamespace(settings=None, objects=None, role=None)
    public = library_revision()
    historical = execution_library_revision([box["files"]])
    assert historical != public

    def payload(revision):
        body = {
            "tenant_id": "tenant",
            "library_revision": public,
            "files": [{"suite_target": {"suite_key": "UAES:U08", "suite_revision_id": "rev", "suite_digest": "digest", "related_suite_digests": {}}}],
        }
        if revision is not None:
            body["suite_library_revision"] = revision
        return body

    assert precheck_suites_current(store, None, payload(historical)) is True
    assert precheck_suites_current(store, None, payload(public)) is False
    assert precheck_suites_current(store, None, payload(None)) is True

    box["files"] = {"checker.py": b"def create_audit():\n    return None\n"}
    assert precheck_suites_current(store, None, payload(public)) is True

    box["files"] = {"checker.py": b"from audit.checkers.v4.uaes import Agent\n"}
    real = suites.legacy_runtime_revision

    def flipped(family):
        if family == "v4":
            return "e" * 64
        return real(family)

    monkeypatch.setattr(suites, "legacy_runtime_revision", flipped)
    assert precheck_suites_current(store, None, payload(historical)) is False
    box["files"] = {"checker.py": b"def create_audit():\n    return None\n"}
    assert precheck_suites_current(store, None, payload(public)) is True
    drifted = dict(payload(public), library_revision="0" * 64)
    assert precheck_suites_current(store, None, drifted) is False
