"""Standalone optimization candidates keep the selected revision's own files."""

import json
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


def test_optimization_copy_keeps_confirmed_formalizations():
    rules = {
        "rules": [{"id": "R1"}, {"id": "R2"}],
        "checker_binding": {"R1": "checker:threshold", "R2": "checker:extra"},
        "formalizations": {"R1": {"cnl_text": "每一条发票至少关联一条订单", "completeness": "complete"}},
    }
    source = {
        "rules.json": json.dumps(rules).encode(),
        "checker.py": b"def threshold():\n    return []\n",
        "resources.py": b"VALUE = 1\n",
        "templates.json": b"[]",
        "appendix.json": b'["keep-appendix"]',
    }
    saved = _standalone_candidate_files(_base(source), _artifact({"checker.py": b"def create_audit():\n    return ['edited']\n"}))
    rewritten = json.loads(saved["rules.json"])
    assert rewritten["formalizations"]["R1"]["cnl_text"] == "每一条发票至少关联一条订单"
    assert rewritten["checker_binding"] == {"R1": "checker:create_audit", "R2": "checker:create_audit"}
    assert saved["templates.json"] == b"[]"
    assert saved["appendix.json"] == b'["keep-appendix"]'
    assert source["rules.json"] == json.dumps(rules).encode()


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


def test_native_program_keeps_source_binding_and_factory_rewrites_only_its_copy():
    native = b'{"rules":[{"id":"R1"},{"id":"R2"}],"checker_binding":{"R1":"checker:threshold","R2":"checker:extra"}}'
    source = {"rules.json": native, "checker.py": b"def threshold():\n    return []\n", "resources.py": b"VALUE = 1\n"}
    files = {"checker.py": b"def threshold():\n    return ['fixed']\n", "resources.py": b"VALUE = 1\n"}
    binding = RuleBinding("threshold", checker_digest(files), {"R1": "checker:threshold", "R2": "checker:extra"})
    saved = _standalone_candidate_files(_base(source), _artifact(files, binding))
    assert saved["rules.json"] == native
    assert saved["checker.py"] == files["checker.py"]
    factory = _artifact({"checker.py": b"def create_audit():\n    return []\n"})
    generated = _standalone_candidate_files(_base(source), factory)
    assert json.loads(generated["rules.json"])["checker_binding"] == {"R1": "checker:create_audit", "R2": "checker:create_audit"}
    assert "resources.py" not in generated
    assert source["rules.json"] == native
    assert not _saved_binding_unverified(generated, factory)


def test_partial_factory_keeps_its_clause_set_and_a_foreign_entrypoint_is_refused():
    partial = b'{"rules":[{"id":"U08:1"},{"id":"U08:2"}],"checker_binding":{"U08:1":"checker:threshold"}}'
    source = {"rules.json": partial, "checker.py": b"def threshold():\n    return []\n"}
    saved = _standalone_candidate_files(_base(source, "UAES:U08"), _artifact({"checker.py": b"def create_audit():\n    return []\n"}))
    assert json.loads(saved["rules.json"])["checker_binding"] == {"U08:1": "checker:create_audit"}
    assert _saved_binding_unverified(saved, _artifact({"checker.py": saved["checker.py"]}))
    assert source["rules.json"] == partial
    refused = _artifact({"checker.py": b"def create_audit():\n    return []\n"})
    refused.entrypoint = "checker:threshold"
    with pytest.raises(ValueError, match="checker_artifact_identity_mismatch"):
        _standalone_candidate_files(_base(source, "UAES:U08"), refused)
