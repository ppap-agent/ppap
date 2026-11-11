"""Directory identity for predefined checkers. Legacy carriers stay untouched."""

import ast
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from audit.presets import customer_presets, preset_rule
from audit.standard_suites import _CHECKERS_ROOT, customer_directory, customer_name, load_predefined_checkers, predefined_directory_suites, predefined_sources

ROOT = Path(__file__).resolve().parents[3]
LEAF = ("checker.py", "rules.json", "resources.py")
CHECKER = "def create_audit(audit_file, rule, group_context=None):\n    return None\n"
RULES = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}}


def write_leaf(root: Path, source_path: str, *, checker: str = CHECKER, rules: dict | bytes | None = None, extra: dict[str, bytes] | None = None) -> Path:
    directory = root.joinpath(*source_path.split("/"))
    directory.mkdir(parents=True)
    (directory / "checker.py").write_text(checker, encoding="utf-8")
    body = json.dumps(RULES if rules is None else rules).encode() if not isinstance(rules, bytes) else rules
    (directory / "rules.json").write_bytes(body)
    for name, content in (extra or {}).items():
        (directory / name).write_bytes(content)
    return directory


def test_installed_u08_leaf_reads_its_own_files():
    suites = predefined_directory_suites()
    assert len(suites) == 53
    suite = next(item for item in suites if item.source_path == "uaes/U08")
    assert suite.suite_key == "UAES:U08"
    assert suite.customer == "UAES" and suite.source_entry == "U08" and suite.document_type == "控制计划"
    assert suite.related_suite_keys == ("UAES:U07", "UAES:U16")
    disk = _CHECKERS_ROOT / "uaes" / "U08"
    for name in LEAF:
        assert suite.files[name] == (disk / name).read_bytes() == (ROOT / "packages/audit/src/audit/checkers/uaes/U08" / name).read_bytes()
    rules = json.loads(suite.files["rules.json"])
    assert rules["directory_coverage"] == "complete"
    assert rules["related_suite_keys"] == ["UAES:U07", "UAES:U16"]
    preset = preset_rule("UAES", "U08")
    assert [row["id"] for row in rules["rules"]] == [clause.id for clause in preset.clauses]
    assert [row["rule_to_check"] for row in rules["rules"]] == [clause.text for clause in preset.clauses]
    assert rules["checker_binding"] == {clause.id: "checker:create_audit" for clause in preset.clauses}
    assert "rule_profile" not in rules
    assert suite.entrypoints == rules["checker_binding"]
    for name in LEAF[:1] + LEAF[2:]:
        assert not _legacy_imports(suite.files[name])


def test_every_preset_has_a_complete_leaf_that_names_its_implementation():
    directory = {suite.suite_key: suite for suite in predefined_directory_suites()}
    for preset in customer_presets():
        suite = directory[f"{preset.customer}:{preset.entry_id}"]
        rules = json.loads(suite.files["rules.json"])
        clause_ids = [clause.id for clause in preset.clauses]
        assert rules["checker_binding"] == {clause_id: "checker:create_audit" for clause_id in clause_ids}
        namespace: dict = {}
        exec(suite.files["resources.py"], namespace)
        resource = namespace["RESOURCE"]
        assert resource["customer"] == preset.customer and resource["preset_key"] == preset.entry_id
        assert set(resource["methods"]) == set(clause_ids)
        assert all(path.startswith("audit.checker_lib." + customer_directory(preset.customer) + ".") for path in resource["carriers"].values())
        assert b"audit.checkers" not in suite.files["checker.py"] and b"check_clause" not in suite.files["checker.py"]
        assert [row["rule_to_check"] for row in rules["rules"]] == [clause.text for clause in preset.clauses]


def test_scan_uses_the_filesystem_not_the_customer_registry(tmp_path, monkeypatch):
    monkeypatch.setattr("audit.presets.customer_presets", lambda: (_ for _ in ()).throw(AssertionError("registry read")))
    write_leaf(tmp_path, "示例客户/Z99")
    found = load_predefined_checkers(tmp_path)
    assert [(suite.source_path, suite.suite_key) for suite in found] == [("示例客户/Z99", "示例客户:Z99")]


def test_navigation_derives_identity_and_order_from_real_paths(tmp_path):
    paths = ("uaes/U07", "uaes/U08", "li/L07", "nio/N03")
    for path in reversed(paths):
        directory, group = path.split("/")
        write_leaf(tmp_path, path, rules={**RULES, "title": "规则 " + group, "customers": [customer_name(directory)], "preset_key": group})
    found = predefined_sources(tmp_path)
    assert [row.path for row in found] == sorted(paths, key=lambda path: path.encode())
    for row in found:
        directory, group = row.path.split("/")
        customer = customer_name(directory)
        assert row.title == "规则 " + group
        assert row.suite.customer == customer and row.suite.source_entry == group
        assert row.suite.suite_key == customer + ":" + group
        assert row.suite.source_path == row.path


@pytest.mark.parametrize("claim", [{"customers": ["Other"]}, {"customers": "UAES"}, {"preset_key": "U99"}])
def test_metadata_cannot_override_a_directory_identity(tmp_path, claim):
    write_leaf(tmp_path, "uaes/U08", rules={**RULES, **claim})
    with pytest.raises(ValueError, match="predefined_identity_conflict"):
        predefined_sources(tmp_path)


def test_internal_file_symlink_is_not_a_source_asset(tmp_path):
    directory = write_leaf(tmp_path, "uaes/U08")
    rules = directory / "rules.json"
    rules.rename(tmp_path / "elsewhere.json")
    rules.symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(ValueError, match="predefined_path_escape"):
        predefined_sources(tmp_path)


def test_related_keys_come_from_the_leaf_and_do_not_fall_back(tmp_path):
    installed = next(suite for suite in predefined_directory_suites() if suite.source_path == "uaes/U08")
    assert installed.related_suite_keys == ("UAES:U07", "UAES:U16")
    write_leaf(
        tmp_path,
        "示例客户/Z99",
        rules={
            "rules": [{"id": "C1", "rule_to_check": "clause"}, {"id": "C2", "rule_to_check": "other"}],
            "checker_binding": {"C1": "checker:create_audit", "C2": "checker:create_audit"},
            "related_suite_keys": ["示例客户:Z01"],
        },
    )
    write_leaf(
        tmp_path,
        "uaes/U08",
        rules={
            "rules": [{"id": "C1", "rule_to_check": "clause"}, {"id": "C2", "rule_to_check": "other"}],
            "checker_binding": {"C1": "checker:create_audit"},
            "directory_coverage": "partial",
            "related_suite_keys": ["UAES:U07"],
        },
    )
    found = {suite.source_path: suite for suite in load_predefined_checkers(tmp_path)}
    assert found["示例客户/Z99"].related_suite_keys == ("示例客户:Z01",)
    assert "UAES:U99" not in found["示例客户/Z99"].related_suite_keys
    assert found["uaes/U08"].related_suite_keys == ("UAES:U07",)
    assert "UAES:U12" not in found["uaes/U08"].related_suite_keys
    assert not (tmp_path / "示例客户" / "Z01").exists()
    assert not (tmp_path / "v4").exists()


def test_bytecode_cache_does_not_drop_an_installed_leaf(tmp_path):
    directory = write_leaf(tmp_path, "uaes/U08")
    cache = directory / "__pycache__"
    cache.mkdir()
    (cache / "checker.cpython-312.pyc").write_bytes(b"\x00pyc")
    (directory / "resources.cpython-312.pyc").write_bytes(b"\x00pyc")
    found = load_predefined_checkers(tmp_path)
    assert [suite.source_path for suite in found] == ["uaes/U08"]
    assert set(found[0].files) == {"checker.py", "rules.json"}


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        ("missing", "predefined_missing_leaf_file"),
        ("unknown", "predefined_unknown_asset"),
        ("nested", "predefined_directory_conflict"),
        ("json", "predefined_bad_json"),
        ("rules", "predefined_invalid_rules"),
        ("binding", "predefined_invalid_binding"),
        ("entrypoint", "predefined_invalid_binding"),
        ("syntax", "predefined_checker_syntax"),
        ("factory", "predefined_checker_entrypoint_missing"),
        ("legacy", "predefined_legacy_checker_import"),
        ("name", "predefined_invalid_name"),
        ("coverage", "predefined_invalid_rules"),
        ("related", "predefined_invalid_rules"),
        ("partial", "predefined_partial_unmarked"),
    ],
)
def test_directory_failures_do_not_fall_back(tmp_path, mutate, code):
    if mutate == "missing":
        directory = tmp_path / "uaes" / "U08"
        directory.mkdir(parents=True)
        (directory / "rules.json").write_bytes(json.dumps(RULES).encode())
    elif mutate == "name":
        write_leaf(tmp_path, "UAES:BAD/U08")
    else:
        rules = dict(RULES)
        checker = CHECKER
        extra = {}
        if mutate == "unknown":
            extra["notes.txt"] = b"x"
        elif mutate == "json":
            rules = b"{"
        elif mutate == "rules":
            rules = {"rules": [{"id": "C1"}, {"id": "C1"}]}
        elif mutate == "binding":
            rules = {"rules": [{"id": "C1"}], "checker_binding": {"missing": "checker:create_audit"}}
        elif mutate == "entrypoint":
            rules = {"rules": [{"id": "C1"}], "checker_binding": {"C1": "checker:check"}}
        elif mutate == "syntax":
            checker = "def create_audit(\n"
        elif mutate == "factory":
            checker = "def check(context):\n    return None\n"
        elif mutate == "legacy":
            checker = "from audit.checkers.v4.uaes.control_plan import UaesControlPlanAgent\n\ndef create_audit(audit_file, rule, group_context=None):\n    return UaesControlPlanAgent\n"
        elif mutate == "coverage":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}, "directory_coverage": "partial"}
        elif mutate == "related":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}, "related_suite_keys": ["v4/uaes/U07"]}
        elif mutate == "partial":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {}}
        write_leaf(tmp_path, "uaes/U08", checker=checker, rules=rules, extra=extra)
        if mutate == "nested":
            (tmp_path / "uaes" / "U08" / "nested").mkdir()
    with pytest.raises(ValueError, match=code):
        load_predefined_checkers(tmp_path)
    missing = tmp_path / "missing-root"
    with pytest.raises(ValueError, match="predefined_root_missing"):
        load_predefined_checkers(missing)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_predefined_checkers(empty) == ()


def test_symlink_escape_and_duplicate_real_path_fail(tmp_path):
    outside = tmp_path / "outside"
    write_leaf(outside, "secret")
    root = tmp_path / "checkers"
    (root / "UAES").mkdir(parents=True)
    (root / "uaes" / "U08").symlink_to(outside / "secret", target_is_directory=True)
    with pytest.raises(ValueError, match="predefined_path_escape"):
        load_predefined_checkers(root)
    shutil.rmtree(root)
    write_leaf(root, "uaes/U08")
    (root / "UAES2").mkdir(parents=True)
    (root / "UAES2" / "U08").symlink_to(root / "uaes" / "U08", target_is_directory=True)
    with pytest.raises(ValueError, match="predefined_duplicate_path"):
        load_predefined_checkers(root)


def test_scan_does_not_execute_checker_source(tmp_path):
    write_leaf(tmp_path, "uaes/U08", checker=CHECKER + "raise SystemExit('scan executed the checker')\n")
    found = load_predefined_checkers(tmp_path)
    assert found[0].source_path == "uaes/U08"


def test_dynamic_legacy_import_is_rejected(tmp_path):
    checker = "import importlib\n\ndef create_audit(audit_file, rule, group_context=None):\n    return importlib.import_module('audit.checkers.v4.uaes.control_plan')\n"
    write_leaf(tmp_path, "uaes/U08", checker=checker)
    with pytest.raises(ValueError, match="predefined_legacy_checker_import"):
        load_predefined_checkers(tmp_path)


def test_empty_binding_is_stored_but_authorizes_no_clause(tmp_path):
    write_leaf(tmp_path, "uaes/U08", rules={"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {}, "directory_coverage": "partial"})
    suite = load_predefined_checkers(tmp_path)[0]
    assert suite.entrypoints == {}
    assert suite.related_suite_keys == ()


def test_scan_order_is_independent_of_locale(tmp_path, monkeypatch):
    write_leaf(tmp_path, "li/L08")
    write_leaf(tmp_path, "uaes/U08")
    monkeypatch.setenv("LC_ALL", "C")
    first = [suite.source_path for suite in load_predefined_checkers(tmp_path)]
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    second = [suite.source_path for suite in load_predefined_checkers(tmp_path)]
    assert first == second == sorted(first, key=lambda value: value.encode("utf-8"))


def test_wheel_and_container_context_keep_the_same_leaf_bytes(tmp_path):
    app = (ROOT / "deploy/app/Dockerfile").read_text(encoding="utf-8")
    deep = (ROOT / "deploy/deep/Dockerfile").read_text(encoding="utf-8")
    assert "COPY packages /app/packages" in app
    assert "COPY packages/audit/src /app/packages/audit/src" in deep
    ignored = {line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    assert not ({"packages", "packages/audit", "**/checkers", "packages/audit/src/audit/checkers"} & ignored)
    uv = shutil.which("uv")
    assert uv
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT / "packages/audit")], check=True, cwd=ROOT)
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as package:
        names = set(package.namelist())
        for name in LEAF:
            archived = f"audit/checkers/uaes/U08/{name}"
            assert archived in names
            assert package.read(archived) == (ROOT / "packages/audit/src/audit/checkers/uaes/U08" / name).read_bytes()
        for preset in customer_presets():
            leaf = ROOT / "packages/audit/src/audit/checkers" / customer_directory(preset.customer) / preset.entry_id
            for name in ("checker.py", "rules.json", "resources.py"):
                archived = f"audit/checkers/{customer_directory(preset.customer)}/{preset.entry_id}/{name}"
                assert archived in names
                assert package.read(archived) == (leaf / name).read_bytes()
        for source in predefined_sources():
            if source.suite is None:
                for path in (_CHECKERS_ROOT / source.path).glob("*.py"):
                    archived = "audit/checkers/" + path.relative_to(_CHECKERS_ROOT).as_posix()
                    assert package.read(archived) == path.read_bytes()


def _legacy_imports(source: bytes) -> list[str]:
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        found.extend(module for module in modules if module.startswith(("audit.checker_lib.li", "audit.checker_lib.nio", "audit.checkers.v4")))
    return found


def test_digest_helper_is_stable():
    suite = next(item for item in predefined_directory_suites() if item.source_path == "uaes/U08")
    assert hashlib.sha256(suite.files["rules.json"]).hexdigest() == hashlib.sha256((ROOT / "packages/audit/src/audit/checkers/uaes/U08/rules.json").read_bytes()).hexdigest()
