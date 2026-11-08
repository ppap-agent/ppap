"""Directory identity for predefined checkers. Legacy carriers stay untouched."""

import ast
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from audit.checker_core import checker_digest
from audit.presets import customer_presets, preset_rule
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
    assert suite.related_suite_keys == ("UAES:U07", "UAES:U16")
    legacy = next(item for item in standard_suites() if item.suite_key == "UAES:U08")
    assert legacy.source_path != suite.source_path
    assert legacy.revision_digest != suite.revision_digest
    assert "check_clause" in legacy.files["checker.py"].decode()
    assert len(standard_suites()) == 41
    disk = _CHECKERS_ROOT / "v5" / "UAES" / "U08"
    for name in LEAF:
        assert suite.files[name] == (disk / name).read_bytes() == (ROOT / "packages/audit/src/audit/checkers/v5/UAES/U08" / name).read_bytes()
    rules = json.loads(suite.files["rules.json"])
    assert rules["directory_coverage"] == "partial"
    assert rules["related_suite_keys"] == ["UAES:U07", "UAES:U16"]
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


def test_identical_v6_copy_keeps_one_content_digest_and_two_source_paths(tmp_path):
    installed = next(suite for suite in predefined_directory_suites() if suite.source_path == "v5/UAES/U08")
    write_leaf(tmp_path, "v5/UAES/U08", checker=installed.files["checker.py"].decode(), rules=installed.files["rules.json"], extra={"resources.py": installed.files["resources.py"]})
    shutil.copytree(tmp_path / "v5" / "UAES" / "U08", tmp_path / "v6" / "UAES" / "U08")
    found = load_predefined_checkers(tmp_path)
    assert [suite.source_path for suite in found] == ["v5/UAES/U08", "v6/UAES/U08"]
    assert {suite.suite_key for suite in found} == {"UAES:U08"}
    assert found[0].files == found[1].files
    assert found[0].revision_digest == found[1].revision_digest
    assert checker_digest(found[0].program_files) == checker_digest(found[1].program_files)
    assert found[0].related_suite_keys == found[1].related_suite_keys == ("UAES:U07", "UAES:U16")
    again = load_predefined_checkers(tmp_path)
    assert [suite.files for suite in again] == [suite.files for suite in found]


def test_related_keys_come_from_the_leaf_and_do_not_fall_back(tmp_path, monkeypatch):
    from audit import standard_suites as module

    monkeypatch.setitem(module._RELATED, "UAES:U08", ("UAES:U12",))
    monkeypatch.setitem(module._RELATED, "UAES:U07", ("UAES:U12",))
    monkeypatch.setitem(module._RELATED, "示例客户:Z99", ("UAES:U99",))
    installed = next(suite for suite in predefined_directory_suites() if suite.source_path == "v5/UAES/U08")
    assert installed.related_suite_keys == ("UAES:U07", "UAES:U16")
    write_leaf(
        tmp_path,
        "v6/示例客户/Z99",
        rules={
            "rules": [{"id": "C1", "rule_to_check": "clause"}, {"id": "C2", "rule_to_check": "other"}],
            "checker_binding": {"C1": "checker:create_audit", "C2": "checker:create_audit"},
            "related_suite_keys": ["示例客户:Z01"],
        },
    )
    write_leaf(
        tmp_path,
        "v6/UAES/U08",
        rules={
            "rules": [{"id": "C1", "rule_to_check": "clause"}, {"id": "C2", "rule_to_check": "other"}],
            "checker_binding": {"C1": "checker:create_audit"},
            "directory_coverage": "partial",
            "related_suite_keys": ["UAES:U07"],
        },
    )
    found = {suite.source_path: suite for suite in load_predefined_checkers(tmp_path)}
    assert found["v6/示例客户/Z99"].related_suite_keys == ("示例客户:Z01",)
    assert "UAES:U99" not in found["v6/示例客户/Z99"].related_suite_keys
    assert found["v6/UAES/U08"].related_suite_keys == ("UAES:U07",)
    assert "UAES:U12" not in found["v6/UAES/U08"].related_suite_keys
    assert not (tmp_path / "v6" / "示例客户" / "Z01").exists()
    assert not (tmp_path / "v4").exists()


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
        ("coverage", "predefined_invalid_rules"),
        ("related", "predefined_invalid_rules"),
        ("partial", "predefined_partial_unmarked"),
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
        elif mutate == "coverage":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}, "directory_coverage": "partial"}
        elif mutate == "related":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}, "related_suite_keys": ["v4/UAES/U07"]}
        elif mutate == "partial":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {}}
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
    write_leaf(tmp_path, "v5/UAES/U08", rules={"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {}, "directory_coverage": "partial"})
    suite = load_predefined_checkers(tmp_path)[0]
    assert suite.entrypoints == {}
    assert suite.related_suite_keys == ()


def test_scan_order_is_independent_of_locale(tmp_path, monkeypatch):
    write_leaf(tmp_path, "v5/理想/L08")
    write_leaf(tmp_path, "v5/UAES/U08")
    monkeypatch.setenv("LC_ALL", "C")
    first = [suite.source_path for suite in load_predefined_checkers(tmp_path)]
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    second = [suite.source_path for suite in load_predefined_checkers(tmp_path)]
    assert first == second == sorted(first, key=lambda value: value.encode("utf-8"))


def test_u08_bound_clauses_read_xlsx_and_do_not_pass_without_evidence(tmp_path):
    suite = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    unable = _run(suite, pdf, ("U08:1", "U08:4"))
    assert [item.status for item in unable] == ["UNABLE", "UNABLE"]
    assert {item.suggestions for item in unable} == {"该预置 Checker 不支持此原件载体。"}

    passed = _run(suite, _workbook(tmp_path / "ready.xlsx", resource="压机", mark="CC"), ("U08:1", "U08:4"))
    assert [item.status for item in passed] == ["PASS", "PASS"]
    assert all(item.evidence and item.evidence.sources for item in passed)
    assert all(locator["file_version_id"] == "frozen-version" and locator["kind"] == "xlsx_cell" for item in passed for locator in _locators(item))
    failed = _run(suite, _workbook(tmp_path / "gap.xlsx", resource="", mark=""), ("U08:1", "U08:4"))
    assert [item.status for item in failed] == ["NG", "NG"]
    assert all(locator["file_version_id"] == "frozen-version" and locator["kind"] == "xlsx_cell" for item in failed for locator in _locators(item))
    missing = _run(suite, _workbook(tmp_path / "plain.xlsx", headers=("名称",), resource="x", mark="x"), ("U08:1", "U08:4"))
    assert [item.status for item in missing] == ["NG", "NG"]
    for item in missing:
        locators = _locators(item)
        assert locators and {locator["kind"] for locator in locators} == {"xlsx_range"}
        assert all(locator["file_version_id"] == "frozen-version" for locator in locators)
        assert all(locator.get("cell") != "A1" for locator in locators)
        assert [diag.code for diag in item.evidence.diagnostics] == ["column_not_in_sheet"]
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


def _locators(item):
    return [json.loads(source.locator) for source in item.evidence.sources]


def _view(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _view(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_view(item) for item in value)
    return value


def _save(path: Path, build) -> Path:
    from openpyxl import Workbook

    book = Workbook()
    build(book)
    book.save(path)
    book.close()
    return path


def _execute(files: dict[str, bytes], path: Path, clauses):
    """Run frozen program bytes through the official guest entry."""
    from agent.execution.audit_runtime import run_audit_process
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding

    body = path.read_bytes()
    rules = json.loads(files["rules.json"])
    program = {name: files[name] for name in ("checker.py", "resources.py") if name in files}
    digest = checker_digest(program)
    declared = rules.get("checker_binding") or {clause: "checker:create_audit" for clause in clauses}
    binding = {clause: declared[clause] for clause in clauses}
    source = FrozenInput("frozen-version", hashlib.sha256(body).hexdigest(), "target", path.name)
    target = FrozenTarget("frozen-version", "revision", "predefined:test", "a" * 64, rules, RuleBinding("predefined:test", digest, binding))
    snapshot = ExecutionSnapshot("tenant", "initiator", (source,), (target,), "runtime", "library", {})
    result = run_audit_process(snapshot, {"frozen-version": body}, {digest: program})
    by_clause = {row["clause_id"]: row for row in result["findings"]}
    return [_view(by_clause[clause]) for clause in clauses]


def test_incomplete_scan_formula_merge_and_extra_tables_are_unable(tmp_path):
    suite = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    files = dict(suite.files)

    def run(path: Path, clauses=("U08:1", "U08:4")):
        return _execute(files, path, clauses)

    def unable(path: Path, code: str, clause: str = "U08:1"):
        item = run(path, (clause,))[0]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert item.status == "UNABLE"
        assert [diag.code for diag in item.evidence.diagnostics] == [code]
        locators = _locators(item)
        assert locators
        assert all(locator["file_version_id"] == "frozen-version" and locator["source_sha256"] == digest for locator in locators)
        assert all(locator["kind"] == "xlsx_range" for locator in locators) if code != "formula_cache_missing" else True
        return locators

    def headers(sheet, row=1):
        sheet.cell(row, 1, "工序名称/操作描述")
        sheet.cell(row, 2, "机器/装置/夹具/工装")
        sheet.cell(row, 3, "特性类型")

    row_limit = _save(
        tmp_path / "row-limit.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            book.active.cell(41, 1, "工序名称/操作描述"),
            book.active.cell(41, 2, "机器/装置/夹具/工装"),
            book.active.cell(42, 1, "装配"),
            book.active.cell(42, 2, "压机"),
        ),
    )
    located = unable(row_limit, "scan_incomplete")
    assert located[0]["end"] == "B40"
    assert run(row_limit)[1].status == "UNABLE"

    col_limit = _save(
        tmp_path / "col-limit.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            book.active.cell(1, 31, "工序名称/操作描述"),
            book.active.cell(1, 32, "机器/装置/夹具/工装"),
            book.active.cell(2, 31, "装配"),
            book.active.cell(2, 32, "压机"),
        ),
    )
    assert unable(col_limit, "scan_incomplete")[0]["end"] == "AD2"

    def wide(book):
        sheet = book.active
        sheet.title = "控制计划"
        headers(sheet)
        sheet.cell(2, 1, "装配")
        sheet.cell(2, 2, "压机")
        sheet.cell(2, 3, "CC")
        sheet.cell(1, 31, "备注")

    assert unable(_save(tmp_path / "wide.xlsx", wide), "scan_incomplete")[0]["end"] == "AD2"
    assert run(_save(tmp_path / "wide.xlsx", wide), ("U08:1",))[0].status != "PASS"

    def fill_past_data_window(book):
        sheet = book.active
        sheet.title = "控制计划"
        headers(sheet)
        for row in range(2, 202):
            sheet.cell(row, 1, "装配")
            sheet.cell(row, 2, "压机")
            sheet.cell(row, 3, "CC")
        sheet.cell(202, 1, "装配")

    past = _save(tmp_path / "past-data.xlsx", fill_past_data_window)
    past_range = unable(past, "scan_incomplete")
    assert past_range[0]["end"] == "B201"
    assert run(past, ("U08:4",))[0].status == "UNABLE"

    formula = _save(
        tmp_path / "formula.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            headers(book.active),
            book.active.cell(2, 1, "装配"),
            book.active.cell(2, 2, "=A2"),
            book.active.cell(2, 3, "CC"),
        ),
    )
    formula_digest = hashlib.sha256(formula.read_bytes()).hexdigest()
    assert unable(formula, "formula_cache_missing")[0] == {"kind": "xlsx_cell", "sheet": "控制计划", "cell": "B2", "file_version_id": "frozen-version", "source_sha256": formula_digest}

    hidden_header = _save(
        tmp_path / "formula-header.xlsx",
        lambda book: (
            setattr(book.active, "title", "控制计划"),
            book.active.cell(1, 1, '="工序名称/操作描述"'),
            book.active.cell(1, 2, "机器/装置/夹具/工装"),
            book.active.cell(2, 1, "装配"),
            book.active.cell(2, 2, "压机"),
        ),
    )
    assert unable(hidden_header, "formula_cache_missing")[0]["cell"] == "A1"
    assert run(hidden_header, ("U08:1",))[0].status != "NG"

    errored = _save(
        tmp_path / "error.xlsx",
        lambda book: (setattr(book.active, "title", "控制计划"), book.active.cell(1, 1, "特性类型"), book.active.cell(2, 1, "#N/A")),
    )
    assert unable(errored, "formula_cache_missing", "U08:4")[0]["cell"] == "A2"

    def complete(sheet):
        sheet.title = "控制计划"
        headers(sheet, 2)
        sheet.cell(1, 1, "控制计划")
        sheet.cell(3, 1, "装配")
        sheet.cell(3, 2, "压机")
        sheet.cell(3, 3, "CC")

    blank_extra = _save(
        tmp_path / "blank-extra.xlsx",
        lambda book: (complete(book.active), book.create_sheet("封面"), book.create_sheet("图例")),
    )
    assert [item.status for item in run(blank_extra)] == ["PASS", "PASS"]

    def legend_and_plan(book):
        plan = book.active
        plan.title = "控制计划"
        plan.cell(1, 1, "特性类型")
        legend = book.create_sheet("图例")
        legend.cell(1, 1, "特性类型")
        legend.cell(2, 1, "CC")

    legend = unable(_save(tmp_path / "legend.xlsx", legend_and_plan), "legend_or_multiple_tables", "U08:4")
    assert {locator["kind"] for locator in legend} == {"xlsx_range"}

    def two_tables(book):
        complete(book.active)
        other = book.create_sheet("过程")
        headers(other)
        other.cell(2, 1, "装配")
        other.cell(2, 2, "压机")
        other.cell(2, 3, "CC")

    tables = unable(_save(tmp_path / "two.xlsx", two_tables), "legend_or_multiple_tables")
    assert len(tables) == 2 and {locator["kind"] for locator in tables} == {"xlsx_range"}

    def merged_data(book):
        sheet = book.active
        sheet.title = "控制计划"
        headers(sheet)
        sheet.cell(2, 1, "装配")
        sheet.cell(2, 2, "压机")
        sheet.cell(3, 1, "检验")
        sheet.merge_cells("B2:B3")

    assert unable(_save(tmp_path / "merged.xlsx", merged_data), "merged_range")[0]["kind"] == "xlsx_range"

    def title_merge(book):
        sheet = book.active
        complete(sheet)
        sheet.merge_cells("A1:C1")

    assert [item.status for item in run(_save(tmp_path / "title.xlsx", title_merge))] == ["PASS", "PASS"]

    def outside_merge(book):
        sheet = book.active
        headers(sheet)
        sheet.cell(2, 1, "装配")
        sheet.cell(2, 2, "压机")
        sheet.cell(2, 3, "CC")
        sheet.cell(2, 4, "备注")
        sheet.merge_cells("D2:D3")

    assert [item.status for item in run(_save(tmp_path / "outside.xlsx", outside_merge))] == ["PASS", "PASS"]

    def merged_title_without_column(book):
        sheet = book.active
        sheet.title = "控制计划"
        sheet.cell(1, 1, "标题")
        sheet.merge_cells("A1:C1")
        sheet.cell(2, 1, "名称")

    assert unable(_save(tmp_path / "merged-title.xlsx", merged_title_without_column), "scan_incomplete")[0]["kind"] == "xlsx_range"

    empty = _save(tmp_path / "empty.xlsx", lambda book: setattr(book.active, "title", "控制计划"))
    blank = run(empty, ("U08:1",))[0]
    assert blank.status == "NG" and blank.evidence.used == "none" and blank.evidence.sources == ()


def test_digest_helper_is_stable():
    suite = predefined_directory_suites()[0]
    assert hashlib.sha256(suite.files["rules.json"]).hexdigest() == hashlib.sha256((ROOT / "packages/audit/src/audit/checkers/v5/UAES/U08/rules.json").read_bytes()).hexdigest()
