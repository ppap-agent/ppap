import json
from dataclasses import replace

import pytest
from audit.checker_core import RuleBinding, ValidationRecord, checker_digest, rule_digest
from product.store.suites import freeze_execution
from product.suites import SuiteRevision, content_digest, resolve_suite, resolved_digest


class Assets:
    def __init__(self):
        self.rows, self.objects = {}, {}

    def add(self, identifier, *, owner=None, base=None, files=None, related=()):
        files = files or {}
        previous = self.resolve(base).object_digests if base else {}
        overrides = {path: content_digest(body) for path, body in files.items() if content_digest(body) != previous.get(path)}
        for path, digest in overrides.items():
            self.objects[owner, digest] = files[path]
        row = SuiteRevision(identifier, owner, "cp", related, base, overrides, resolved_digest("cp", related, {**previous, **overrides}), "author")
        self.rows[identifier] = row
        return row

    def resolve(self, identifier, tenant="a"):
        return resolve_suite(identifier, tenant, load_revision=self.rows.get, load_object=lambda owner, digest: self.objects[owner, digest])


def baseline():
    assets = Assets()
    assets.add(
        "official",
        files={
            "rules.json": b'{"rules":[{"id":"r1","rule_to_check":"Required"}]}',
            "checker.py": b"def check(ctx): pass",
            "templates.json": b'[{"kind":"structure","columns":["Part"]}]',
            "tests.json": b"[]",
        },
    )
    return assets


def test_two_deltas_resolve_without_repeating_unchanged_objects():
    assets = baseline()
    old = assets.resolve("official")
    first = assets.add("first", owner="a", base="official", files={"checker.py": b"def check(ctx): return 1"})
    second = assets.add("second", owner="a", base="first", files={"templates.json": b'[{"kind":"structure","columns":["Number"]}]'})
    assert list(first.overrides) == ["checker.py"]
    assert list(second.overrides) == ["templates.json"]
    assert assets.resolve("second").files["rules.json"] == old.files["rules.json"]
    assert assets.resolve("official") == old
    assert len({old.revision.resolved_digest, first.resolved_digest, second.resolved_digest}) == 3
    assert len(assets.objects) == 6


def test_rule_text_and_related_keys_change_digest_but_mapping_order_does_not():
    assets = baseline()
    rule = assets.add("rule", owner="a", base="official", files={"rules.json": b'{"rules":[{"id":"r1","rule_to_check":"Changed"}]}'})
    dependency = assets.add("related", owner="a", base="official", related=("pfmea",))
    assert rule.resolved_digest != assets.rows["official"].resolved_digest != dependency.resolved_digest
    objects = assets.resolve("official").object_digests
    assert resolved_digest("cp", ("b", "a"), objects) == resolved_digest("cp", ("a", "b"), dict(reversed(list(objects.items()))))


@pytest.mark.parametrize("fault", ["foreign", "wrong_suite", "cycle", "missing", "bytes", "digest", "too_deep"])
def test_resolution_fails_closed(fault):
    assets = baseline()
    assets.add("child", owner="a", base="official")
    if fault == "foreign":
        assets.rows["official"] = replace(assets.rows["official"], tenant_id="b")
    elif fault == "wrong_suite":
        assets.rows["official"] = replace(assets.rows["official"], suite_key="other")
    elif fault == "cycle":
        assets.rows["official"] = replace(assets.rows["official"], base_revision_id="child")
    elif fault == "missing":
        assets.objects.clear()
    elif fault == "bytes":
        digest = assets.rows["official"].overrides["checker.py"]
        assets.objects[None, digest] = b"tampered"
    elif fault == "digest":
        assets.rows["official"] = replace(assets.rows["official"], resolved_digest="0" * 64)
    else:
        for number in range(15):
            assets.add(f"depth{number}", owner="a", base=f"depth{number - 1}" if number else "child")
        assets.rows["child"] = replace(assets.rows["child"], base_revision_id="depth14")
    with pytest.raises((ValueError, KeyError)):
        assets.resolve("child")


def test_same_name_tenants_do_not_share_private_objects_or_revisions():
    assets = baseline()
    assets.add("private", owner="a", base="official", files={"checker.py": b"private"})
    with pytest.raises(KeyError):
        assets.resolve("private", tenant="b")
    assert assets.resolve("official", tenant="b").revision.tenant_id is None


def test_resolved_assets_are_immutable():
    suite = baseline().resolve("official")
    with pytest.raises(TypeError):
        suite.files["checker.py"] = b"changed"
    with pytest.raises(TypeError):
        suite.revision.overrides["checker.py"] = "0" * 64


def test_official_blank_template_requires_distribution_permission():
    assets = baseline()
    assets.add("bad", base="official", files={"templates.json": b'[{"kind":"licensed_blank","uri":"private/original","license":"private"}]'})
    with pytest.raises(ValueError, match="distribution"):
        assets.resolve("bad")


def test_freeze_validation_stales_text_and_direct_dependencies_not_library():
    assets = baseline()
    assets.add("related", owner="a", base="official", related=("pfmea",))
    suite = assets.resolve("related")
    rules = json.loads(suite.files["rules.json"])
    code = checker_digest({"checker.py": suite.files["checker.py"]})
    binding = RuleBinding("cp", code, {"r1": "checker:check"}, rule_digest(rules))
    record = ValidationRecord(suite.revision.resolved_digest, rule_digest(rules), code, {"pfmea": "a" * 64}, "lib1")
    args = dict(suite=suite, file_version_id="file", binding=binding, validation=record, related_suite_digests={"pfmea": "a" * 64}, library_revision="lib2")
    target = freeze_execution(**args)
    assert target.verification == "verified" and target.samples_not_rerun
    args["related_suite_digests"] = {"pfmea": "b" * 64}
    with pytest.raises(ValueError, match="validation_required"):
        freeze_execution(**args)
    forced = freeze_execution(**args, allow_unverified=True)
    assert forced.verification == "unverified"
    assert target.verification == "verified"
    args["related_suite_digests"] = {"pfmea": "a" * 64}
    assets.add("changed", owner="a", base="related", related=("pfmea",), files={"rules.json": b'{"rules":[{"id":"r1","rule_to_check":"Changed"}]}'})
    args["suite"] = assets.resolve("changed")
    with pytest.raises(ValueError, match="validation_required"):
        freeze_execution(**args)


def test_optional_program_tombstone_preserves_base_and_cannot_delete_required_assets():
    assets = baseline()
    assets.add("with-resource", owner="a", base="official", files={"resources.py": b"VALUE = 1"})
    previous = assets.resolve("with-resource")
    digests = {key: value for key, value in previous.object_digests.items() if key != "resources.py"}
    deleted = SuiteRevision("deleted", "a", "cp", (), "with-resource", {"resources.py": None}, resolved_digest("cp", (), digests), "author")
    assets.rows["deleted"] = deleted
    candidate = assets.resolve("deleted")
    assert "resources.py" not in candidate.files
    assert candidate.files["templates.json"] == previous.files["templates.json"]
    assert assets.resolve("with-resource") == previous
    for path in ("rules.json", "checker.py", "templates.json"):
        with pytest.raises(ValueError, match="invalid_suite_objects"):
            replace(deleted, overrides={path: None})
    with pytest.raises(ValueError, match="invalid_suite_objects"):
        replace(deleted, base_revision_id=None)
    assets.rows["absent"] = replace(deleted, id="absent", base_revision_id="official")
    with pytest.raises(ValueError, match="suite_deleted_object_missing"):
        assets.resolve("absent")


@pytest.mark.parametrize("change", [None, "checker", "resources", "binding"])
def test_inert_starter_is_never_audited_and_other_unverified_programs_need_consent(change):
    from product.suites import UNIMPLEMENTED_CHECKER_SOURCE, is_unimplemented_suite

    files = {"rules.json": b'{"rules":[{"id":"r1"}],"checker_binding":{}}', "checker.py": UNIMPLEMENTED_CHECKER_SOURCE}
    if change == "checker":
        files["checker.py"] = b"raise RuntimeError('do not execute')\n"
    elif change == "resources":
        files["resources.py"] = b"raise RuntimeError('do not import')\n"
    elif change == "binding":
        files["rules.json"] = b'{"rules":[{"id":"r1"}],"checker_binding":{"r1":"checker:check"}}'
    assets = Assets()
    assets.add("starter", owner="a", files=files)
    suite = assets.resolve("starter")
    code = checker_digest({path: body for path, body in files.items() if path in {"checker.py", "resources.py"}})
    binding = RuleBinding("cp", code, json.loads(files["rules.json"])["checker_binding"])
    args = dict(suite=suite, file_version_id="file", binding=binding, validation=None, related_suite_digests={}, library_revision="lib")
    assert is_unimplemented_suite(suite) is (change is None)
    # An uploaded rule is audited only after its program is generated; consent cannot run the starter either.
    with pytest.raises(ValueError, match="checker_not_generated" if change is None else "suite_validation_required"):
        freeze_execution(**args)
    if change is None:
        with pytest.raises(ValueError, match="checker_not_generated"):
            freeze_execution(**args, allow_unverified=True)
