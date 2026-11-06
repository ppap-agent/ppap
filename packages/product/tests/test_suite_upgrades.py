"""Enterprise overrides survive an official upgrade without inventing validation."""

from dataclasses import replace

import pytest
from product.suite_upgrades import carry_enterprise_changes
from product.suites import ResolvedSuite, SuiteRevision, content_digest, resolved_digest


def suite(identifier, files, *, tenant=None, related=(), key="cp"):
    digests = {path: content_digest(body) for path, body in files.items()}
    return ResolvedSuite(SuiteRevision(identifier, tenant, key, related, None, digests, resolved_digest(key, related, digests), None), files, digests)


def test_file_overrides_survive_conflicting_official_changes_but_unmodified_paths_upgrade():
    before = suite("v1", {"checker.py": b"old code", "rules.json": b"old rules", "templates.json": b"old template", "resources.py": b"old resource"})
    source = suite("enterprise", {**before.files, "checker.py": b"enterprise code", "templates.json": b"enterprise template"}, tenant="a")
    latest = suite("v2", {"checker.py": b"new official code", "rules.json": b"new official rules", "templates.json": b"new template", "resources.py": b"new resource", "tests.json": b"new tests"})
    changes = carry_enterprise_changes(source, before, latest)
    assert changes.files == {"checker.py": b"enterprise code", "templates.json": b"enterprise template"}
    assert {**latest.files, **changes.files} == {**latest.files, "checker.py": b"enterprise code", "templates.json": b"enterprise template"}
    assert source.files["rules.json"] == b"old rules" and latest.files["checker.py"] == b"new official code"
    assert not changes.deleted_paths


def test_reverted_edit_does_not_shadow_new_official_content():
    original = suite("v1", {"checker.py": b"original"})
    reverted = suite("enterprise-after-two-edits", dict(original.files), tenant="a")
    latest = suite("v2", {"checker.py": b"new official"})
    assert carry_enterprise_changes(reverted, original, latest).files == {}


@pytest.mark.parametrize("latest_has_resource", [False, True])
def test_deleted_optional_resource_stays_deleted(latest_has_resource):
    before = suite("v1", {"checker.py": b"original", "resources.py": b"old"})
    source = suite("enterprise", {"checker.py": b"original"}, tenant="a")
    latest = suite("v2", {"checker.py": b"updated", **({"resources.py": b"new"} if latest_has_resource else {})})
    assert carry_enterprise_changes(source, before, latest).deleted_paths == (("resources.py",) if latest_has_resource else ())


def test_related_dependency_delta_preserves_enterprise_removals_and_official_additions():
    before = suite("v1", {}, related=("old", "stable"))
    source = suite("enterprise", {}, tenant="a", related=("private", "stable"))
    latest = suite("v2", {}, related=("new", "old", "stable"))
    assert carry_enterprise_changes(source, before, latest).related_suite_keys == ("new", "private", "stable")


def test_wrong_suite_and_private_baseline_are_rejected():
    original = suite("v1", {})
    with pytest.raises(ValueError, match="official_base"):
        carry_enterprise_changes(original, suite("private", {}, tenant="a"), original)
    with pytest.raises(ValueError, match="suite_key_mismatch"):
        carry_enterprise_changes(replace(original, revision=replace(original.revision, suite_key="wrong")), original, original)
