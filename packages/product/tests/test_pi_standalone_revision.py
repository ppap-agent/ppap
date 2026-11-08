"""Standalone optimization candidates keep the selected revision's own files."""

from types import SimpleNamespace

import pytest
from audit.checker_core import RuleBinding, checker_digest
from product.store.checker_development import _saved_binding_unverified, _standalone_candidate_files

RULES = b'{"rules":[{"id":"R1"},{"id":"R2"}],"checker_binding":{"R1":"checker:create_audit","R2":"checker:create_audit"}}'
PARTIAL = b'{"rules":[{"id":"U08:1"},{"id":"U08:2"}],"checker_binding":{"U08:1":"checker:create_audit"}}'


def _base(files, suite_key="threshold"):
    return SimpleNamespace(files=dict(files), revision=SimpleNamespace(suite_key=suite_key))


def _artifact(files, binding=None):
    return SimpleNamespace(files=dict(files), binding=binding)


def test_candidate_copies_every_selected_file_and_keeps_resources():
    source = {"rules.json": RULES, "checker.py": b"def create_audit():\n    return []\n", "resources.py": b"VALUE = 1\n", "templates.json": b"[]"}
    saved = _standalone_candidate_files(_base(source), _artifact({"checker.py": b"def create_audit():\n    return ['edited']\n", "resources.py": b"VALUE = 2\n"}))
    assert saved["checker.py"] == b"def create_audit():\n    return ['edited']\n"
    assert saved["resources.py"] == b"VALUE = 2\n"
    assert saved["rules.json"] == RULES
    assert saved["templates.json"] == b"[]"
    assert not _saved_binding_unverified(saved, _artifact(saved))


def test_omitted_resources_are_absent_instead_of_inherited():
    source = {"rules.json": PARTIAL, "checker.py": b"def create_audit():\n    return []\n", "resources.py": b"VALUE = 1\n"}
    artifact = _artifact({"checker.py": b"def create_audit():\n    return []\n"})
    saved = _standalone_candidate_files(_base(source, "UAES:U08"), artifact)
    assert "resources.py" not in saved
    assert saved["rules.json"] == PARTIAL
    assert _saved_binding_unverified(saved, artifact)


def test_expanded_or_missing_binding_is_refused():
    source = {"rules.json": PARTIAL, "checker.py": b"def create_audit():\n    return []\n"}
    files = {"checker.py": source["checker.py"]}
    expanded = RuleBinding("UAES:U08", checker_digest(files), {"U08:1": "checker:create_audit", "U08:2": "checker:create_audit"})
    with pytest.raises(ValueError, match="checker_binding_expanded"):
        _standalone_candidate_files(_base(source, "UAES:U08"), _artifact(files, expanded))
    missing = {"rules.json": b'{"rules":[{"id":"R1"}]}', "checker.py": b"def create_audit():\n    return []\n"}
    with pytest.raises(ValueError, match="checker_binding_required"):
        _standalone_candidate_files(_base(missing), _artifact({"checker.py": missing["checker.py"]}))
    with pytest.raises(ValueError, match="checker_artifact_unavailable"):
        _standalone_candidate_files(_base(source, "UAES:U08"), _artifact({"checker.py": source["checker.py"], "notes.txt": b"x"}))
