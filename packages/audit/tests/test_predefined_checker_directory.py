"""Directory identity for predefined checkers. Legacy carriers stay untouched."""

import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from audit.checker_core import checker_digest
from audit.contracts import AuditInput, RuleInput
from audit.presets import customer_presets, preset_rule
from audit.standard_library import check_factory
from audit.standard_suites import _CHECKERS_ROOT, load_predefined_checkers, predefined_directory_suites, standard_suites

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


def test_installed_u08_leaf_is_a_distinct_source_from_the_legacy_carrier():
    suites = predefined_directory_suites()
    assert [suite.source_path for suite in suites] == ["v5/UAES/U08"]
    suite = suites[0]
    assert suite.suite_key == "UAES:U08"
    assert suite.customer == "UAES" and suite.source_entry == "U08" and suite.document_type == "控制计划"
    assert suite.related_suite_keys == ()
    legacy = next(item for item in standard_suites() if item.suite_key == "UAES:U08")
    assert legacy.source_path != suite.source_path
    assert legacy.revision_digest != suite.revision_digest
    assert "check_clause" in legacy.files["checker.py"].decode()
    assert len(standard_suites()) == 41
    disk = _CHECKERS_ROOT / "v5" / "UAES" / "U08"
    for name in LEAF:
        assert suite.files[name] == (disk / name).read_bytes() == (ROOT / "packages/audit/src/audit/checkers/v5/UAES/U08" / name).read_bytes()
    rules = json.loads(suite.files["rules.json"])
    preset = preset_rule("UAES", "U08")
    assert [row["id"] for row in rules["rules"]] == [clause.id for clause in preset.clauses]
    assert [row["rule_to_check"] for row in rules["rules"]] == [clause.text for clause in preset.clauses]
    assert rules["checker_binding"] == {"U08:1": "checker:create_audit", "U08:4": "checker:create_audit"}
    assert "rule_profile" not in rules
    assert suite.entrypoints == rules["checker_binding"]
    for name in LEAF[:1] + LEAF[2:]:
        assert not _legacy_imports(suite.files[name])


def test_migration_inventory_names_every_current_group_and_only_u08_is_partial():
    directory = {suite.suite_key: suite.source_path for suite in predefined_directory_suites()}
    rows = []
    for preset in customer_presets():
        key = f"{preset.customer}:{preset.entry_id}"
        rows.append({"suite_key": key, "status": "partial-directory-slice" if key in directory else "not-migrated", "directory_source": directory.get(key)})
    assert len(rows) == 41
    assert [row for row in rows if row["status"] != "not-migrated"] == [{"suite_key": "UAES:U08", "status": "partial-directory-slice", "directory_source": "v5/UAES/U08"}]
    assert sum(row["status"] == "not-migrated" for row in rows) == 40


def test_scan_uses_the_filesystem_not_the_customer_registry(tmp_path, monkeypatch):
    monkeypatch.setattr("audit.presets.customer_presets", lambda: (_ for _ in ()).throw(AssertionError("registry read")))
    write_leaf(tmp_path, "v5/示例客户/Z99")
    found = load_predefined_checkers(tmp_path)
    assert [(suite.source_path, suite.suite_key) for suite in found] == [("v5/示例客户/Z99", "示例客户:Z99")]


def test_v6_copy_is_discovered_without_a_registry_and_is_not_the_same_source(tmp_path):
    installed = next(suite for suite in predefined_directory_suites() if suite.source_path == "v5/UAES/U08")
    write_leaf(tmp_path, "v5/UAES/U08", checker=installed.files["checker.py"].decode(), rules=installed.files["rules.json"], extra={"resources.py": installed.files["resources.py"]})
    copied = tmp_path / "v6" / "UAES" / "U08"
    shutil.copytree(tmp_path / "v5" / "UAES" / "U08", copied)
    checker = copied / "checker.py"
    checker.write_bytes(checker.read_bytes() + b"\n# v6 candidate\n")
    found = load_predefined_checkers(tmp_path)
    assert [suite.source_path for suite in found] == ["v5/UAES/U08", "v6/UAES/U08"]
    assert {suite.suite_key for suite in found} == {"UAES:U08"}
    assert found[0].revision_digest != found[1].revision_digest
    assert checker_digest(found[0].program_files) != checker_digest(found[1].program_files)
    again = load_predefined_checkers(tmp_path)
    assert [suite.files for suite in again] == [suite.files for suite in found]


def test_legacy_version_packages_are_not_scanned_and_a_package_leaf_conflicts(tmp_path):
    legacy = tmp_path / "v4" / "uaes"
    legacy.mkdir(parents=True)
    (tmp_path / "v4" / "__init__.py").write_text("", encoding="utf-8")
    (legacy / "control_plan.py").write_text("class Agent: pass\n", encoding="utf-8")
    assert load_predefined_checkers(tmp_path) == ()
    write_leaf(tmp_path, "v4/UAES/U08")
    with pytest.raises(ValueError, match="predefined_directory_conflict"):
        load_predefined_checkers(tmp_path)


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
    ],
)
def test_directory_failures_do_not_fall_back(tmp_path, mutate, code):
    if mutate == "missing":
        directory = tmp_path / "v5" / "UAES" / "U08"
        directory.mkdir(parents=True)
        (directory / "rules.json").write_bytes(json.dumps(RULES).encode())
    elif mutate == "name":
        write_leaf(tmp_path, "v5/UAES:BAD/U08")
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
        write_leaf(tmp_path, "v5/UAES/U08", checker=checker, rules=rules, extra=extra)
        if mutate == "nested":
            (tmp_path / "v5" / "UAES" / "U08" / "nested").mkdir()
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
    (root / "v5" / "UAES").mkdir(parents=True)
    (root / "v5" / "UAES" / "U08").symlink_to(outside / "secret", target_is_directory=True)
    with pytest.raises(ValueError, match="predefined_path_escape"):
        load_predefined_checkers(root)
    shutil.rmtree(root)
    write_leaf(root, "v5/UAES/U08")
    (root / "v6" / "UAES").mkdir(parents=True)
    (root / "v6" / "UAES" / "U08").symlink_to(root / "v5" / "UAES" / "U08", target_is_directory=True)
    with pytest.raises(ValueError, match="predefined_duplicate_path"):
        load_predefined_checkers(root)


def test_scan_does_not_execute_checker_source(tmp_path):
    write_leaf(tmp_path, "v5/UAES/U08", checker=CHECKER + "raise SystemExit('scan executed the checker')\n")
    found = load_predefined_checkers(tmp_path)
    assert found[0].source_path == "v5/UAES/U08"


def test_dynamic_legacy_import_is_rejected(tmp_path):
    checker = "import importlib\n\ndef create_audit(audit_file, rule, group_context=None):\n    return importlib.import_module('audit.checkers.v4.uaes.control_plan')\n"
    write_leaf(tmp_path, "v5/UAES/U08", checker=checker)
    with pytest.raises(ValueError, match="predefined_legacy_checker_import"):
        load_predefined_checkers(tmp_path)


def test_empty_binding_is_stored_but_authorizes_no_clause(tmp_path):
    write_leaf(tmp_path, "v5/UAES/U08", rules={"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {}})
    suite = load_predefined_checkers(tmp_path)[0]
    assert suite.entrypoints == {}


def test_scan_order_is_independent_of_locale(tmp_path, monkeypatch):
    write_leaf(tmp_path, "v5/理想/L08")
    write_leaf(tmp_path, "v5/UAES/U08")
    monkeypatch.setenv("LC_ALL", "C")
    first = [suite.source_path for suite in load_predefined_checkers(tmp_path)]
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    second = [suite.source_path for suite in load_predefined_checkers(tmp_path)]
    assert first == second == sorted(first, key=lambda value: value.encode("utf-8"))


def test_u08_bound_clauses_read_xlsx_and_do_not_pass_without_evidence(tmp_path, monkeypatch):
    calls = []

    def forbid_loader(path):
        calls.append(path)
        raise AssertionError("unsupported carrier must not open a workbook")

    monkeypatch.setattr("documents.excel.load_excel_workbook", forbid_loader)
    suite = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    unable = _run(suite, pdf, ("U08:1", "U08:4"))
    assert [item.status for item in unable] == ["UNABLE", "UNABLE"]
    assert {item.suggestions for item in unable} == {"该预置 Checker 不支持此原件载体。"}
    assert calls == []
    monkeypatch.undo()

    passed = _run(suite, _workbook(tmp_path / "ready.xlsx", resource="压机", mark="CC"), ("U08:1", "U08:4"))
    assert [item.status for item in passed] == ["PASS", "PASS"]
    assert all(item.evidence and item.evidence.sources for item in passed)
    failed = _run(suite, _workbook(tmp_path / "gap.xlsx", resource="", mark=""), ("U08:1", "U08:4"))
    assert [item.status for item in failed] == ["NG", "NG"]
    missing = _run(suite, _workbook(tmp_path / "plain.xlsx", headers=("名称",), resource="x", mark="x"), ("U08:1", "U08:4"))
    assert [item.status for item in missing] == ["NG", "NG"]
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a workbook")
    unread = _run(suite, broken, ("U08:1",))
    assert unread[0].status == "UNABLE"
    assert unread[0].status != "PASS"


def test_execution_uses_frozen_bytes_after_the_source_directory_changes(tmp_path):
    checker = (
        "from audit.contracts import AuditResultItem\n"
        "TOKEN = 'ORIGINAL'\n"
        "class _Check:\n"
        "    def __init__(self):\n"
        "        self.rule_item = type('Rule', (), {'id': 'C1'})()\n"
        "    def check(self):\n"
        "        return AuditResultItem(classification='x', item='x', priority='P0', status='NG', suggestions=TOKEN, clause_id='C1')\n"
        "def create_audit(audit_file, rule, group_context=None):\n"
        "    return type('Audit', (), {'checks': [_Check()], 'close': lambda self: None})()\n"
    )
    write_leaf(tmp_path, "v5/UAES/U08", checker=checker)
    frozen = dict(load_predefined_checkers(tmp_path)[0].files)
    path = tmp_path / "v5" / "UAES" / "U08" / "checker.py"
    path.write_text(checker.replace("ORIGINAL", "LIVE"), encoding="utf-8")
    assert b"LIVE" in load_predefined_checkers(tmp_path)[0].files["checker.py"]
    target = tmp_path / "ignored.xlsx"
    target.write_bytes(b"unused")
    result = _execute(frozen, target, ["C1"])
    assert result[0].suggestions == "ORIGINAL"
    assert result[0].status == "NG"


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
            archived = f"audit/checkers/v5/UAES/U08/{name}"
            assert archived in names
            assert package.read(archived) == (ROOT / "packages/audit/src/audit/checkers/v5/UAES/U08" / name).read_bytes()


def _legacy_imports(source: bytes) -> list[str]:
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        found.extend(module for module in modules if module.startswith(("audit.checkers.v2", "audit.checkers.v3", "audit.checkers.v4")))
    return found


def _workbook(path: Path, *, headers=("工序名称/操作描述", "机器/装置/夹具/工装", "特性类型"), resource="压机", mark="CC") -> Path:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    sheet.append(["控制计划"])
    sheet.append(list(headers))
    sheet.append(["装配", resource, mark])
    book.save(path)
    book.close()
    return path


def _run(suite, path: Path, clauses: tuple[str, ...]):
    return _execute(dict(suite.files), path, clauses)


def _execute(files: dict[str, bytes], path: Path, clauses):
    rules = json.loads(files["rules.json"])
    directory = path.parent / f"program-{uuid4().hex}"
    directory.mkdir()
    modules = {}
    saved = {stem: sys.modules.get(stem) for stem in ("resources", "checker")}
    try:
        for stem in ("resources", "checker"):
            name = stem + ".py"
            if name not in files:
                continue
            (directory / name).write_bytes(files[name])
            spec = importlib.util.spec_from_file_location(f"ppap_predefined_{uuid4().hex}", directory / name)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[stem] = module
            spec.loader.exec_module(module)
            modules[stem] = module
        rule = RuleInput(rule_id="frozen", type=rules.get("type"), rules=rules["rules"], customers=rules.get("customers") or [])
        binding = rules.get("checker_binding") or {clause: "checker:create_audit" for clause in clauses}
        context = SimpleNamespace(
            audit_file=AuditInput(audit_id="frozen", file_url=str(path)),
            rule=rule,
            group_context=None,
            target=SimpleNamespace(binding=SimpleNamespace(checker_digest=checker_digest({name: files[name] for name in files if name in {"checker.py", "resources.py"}}), entrypoints=binding)),
            cache={},
            on_close=lambda item: None,
            clause_id=clauses[0],
        )
        results = []
        for clause in clauses:
            context.clause_id = clause
            results.append(check_factory(context, modules["checker"].create_audit))
        return results
    finally:
        for stem, module in saved.items():
            if module is None:
                sys.modules.pop(stem, None)
            else:
                sys.modules[stem] = module


def test_digest_helper_is_stable():
    suite = predefined_directory_suites()[0]
    assert hashlib.sha256(suite.files["rules.json"]).hexdigest() == hashlib.sha256((ROOT / "packages/audit/src/audit/checkers/v5/UAES/U08/rules.json").read_bytes()).hexdigest()
