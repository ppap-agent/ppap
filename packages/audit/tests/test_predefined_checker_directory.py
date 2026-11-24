"""Directory identity for predefined checkers."""

import importlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from audit.presets import customer_presets
from audit.standard_suites import _CHECKERS_ROOT, customer_directory, customer_name, load_predefined_checkers, predefined_directory_suites, predefined_sources

ROOT = Path(__file__).resolve().parents[3]
# The UAES v0.0 m1 leaves the old human truths were judged under; they still run the current shared library.
ARCHIVED = ROOT / "assets/uaes/rulebook/v0.0/m1/checkers"
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


def test_every_preset_has_exactly_one_complete_leaf_that_names_its_own_implementation():
    from audit.engine.checkers.rule_declarations import discover_declared_rules

    directory = {suite.suite_key: suite for suite in predefined_directory_suites()}
    assert set(directory) == {f"{preset.customer}:{preset.entry_id}" for preset in customer_presets()}
    for preset in customer_presets():
        suite = directory[f"{preset.customer}:{preset.entry_id}"]
        for name in LEAF:
            assert suite.files[name] == (_CHECKERS_ROOT / suite.source_path / name).read_bytes()
        rules = json.loads(suite.files["rules.json"])
        clause_ids = [clause.id for clause in preset.clauses]
        assert suite.document_type == rules["type"]
        assert [row["id"] for row in rules["rules"]] == clause_ids
        assert [row["rule_to_check"] for row in rules["rules"]] == [clause.text for clause in preset.clauses]
        assert rules["checker_binding"] == dict(suite.entrypoints) == {clause_id: "checker:create_audit" for clause_id in clause_ids}
        namespace: dict = {}
        exec(suite.files["resources.py"], namespace)
        resource = namespace["RESOURCE"]
        assert resource["customer"] == preset.customer and resource["preset_key"] == preset.entry_id
        assert set(resource["methods"]) == set(clause_ids)
        for path in set(resource["carriers"].values()):
            assert path.startswith("audit.checker_lib." + customer_directory(preset.customer) + "."), path
            module, name = path.split(":", 1)
            cls = getattr(importlib.import_module(module), name)
            declared = getattr(cls, "TARGET_EXCEL_AGENT_CLASS", None) or cls
            assert set(resource["methods"].values()) <= dict(discover_declared_rules(declared)).keys(), path


def _carriers(leaf_resource, suite) -> list[tuple[str, type]]:
    found = []
    for suffix, path in leaf_resource(suite.source_path)["carriers"].items():
        module, name = path.split(":", 1)
        found.append((suffix, getattr(importlib.import_module(module), name)))
    return found


def test_li_and_nio_carriers_read_their_suffix_and_the_leaf_document_type(leaf_resource):
    from audit.checker_lib.excel_agent import ExcelAgent
    from audit.checker_lib.li.excel_agent import ExcelAgent as LiExcelAgent
    from audit.engine.checkers.docx_workbook_agent import StructuredDocxWorkbookMixin
    from audit.kinds import PpapDocumentType

    checked = 0
    for suite in predefined_directory_suites():
        if suite.source_path.split("/")[0] not in {"li", "nio"}:
            continue
        expected = PpapDocumentType.from_string(json.loads(suite.files["rules.json"])["type"])
        for suffix, cls in _carriers(leaf_resource, suite):
            table = getattr(cls, "TARGET_EXCEL_AGENT_CLASS", None) or cls
            assert issubclass(table, (ExcelAgent, LiExcelAgent)), (suite.source_path, suffix)
            if issubclass(cls, StructuredDocxWorkbookMixin):
                assert suffix == ".docx", (suite.source_path, suffix)
            elif table is not cls:
                assert suffix == ".pdf", (suite.source_path, suffix)
            else:
                assert suffix in {".xlsx", ".xls", ".xlsm"}, (suite.source_path, suffix)
            # DVP&R and DPVR (and every other type) never borrow another type's reader.
            assert table.DOC_TYPE is expected, (suite.source_path, suffix)
            checked += 1
    assert checked


@pytest.mark.parametrize("root", [_CHECKERS_ROOT, ARCHIVED], ids=["installed", "v0.0-m1"])
def test_uaes_carriers_declare_the_formats_their_leaves_hand_them(leaf_resource, root):
    carried: dict[type, set[str]] = {}
    for suite in load_predefined_checkers(root):
        if suite.source_path.startswith("uaes/"):
            for suffix, path in leaf_resource(suite.source_path, root)["carriers"].items():
                module, name = path.split(":", 1)
                carried.setdefault(getattr(importlib.import_module(module), name), set()).add(suffix)
    assert all(carried[cls] >= cls.SUPPORTED_EXTENSIONS for cls in carried), "a declared format no leaf hands over"
    if root == ARCHIVED:
        unread = {(cls.__name__, suffix) for cls, suffixes in carried.items() for suffix in suffixes - cls.SUPPORTED_EXTENSIONS}
        # The MSA and control plan leaves hand over a DOCX their table readers do not read: UNABLE (below).
        assert unread == {("UaesMsaAgent", ".docx"), ("UaesControlPlanAgent", ".docx")}


def _bare_docx(tmp_path, monkeypatch) -> Path:
    import docx

    def no_model(*args, **kwargs):
        raise AssertionError("model called")

    monkeypatch.setattr("documents.ai.run_json", no_model)
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", no_model)
    path = tmp_path / "original.docx"
    document = docx.Document()
    document.add_paragraph("文件")
    document.add_table(rows=1, cols=2).cell(0, 0).text = "零件号"
    document.save(path)
    return path


def test_installed_uaes_docx_never_passes_without_a_model(tmp_path, leaf_resource, monkeypatch):
    from audit.checker_lib.agents import AgentAudit
    from audit.contracts import AuditInput, RuleInput

    path = _bare_docx(tmp_path, monkeypatch)
    checked = 0
    for suite in predefined_directory_suites():
        resource = leaf_resource(suite.source_path)
        if not suite.source_path.startswith("uaes/") or ".docx" not in resource["carriers"]:
            continue
        rule = RuleInput.of(json.loads(suite.files["rules.json"]), rule_id=suite.source_path)
        audit = AgentAudit(AuditInput(audit_id=suite.source_path, file_url=str(path)), rule, None, resource=resource)
        try:
            results = {clause: audit.evaluate(clause) for clause in resource["methods"]}
        finally:
            audit.close()
        assert "PASS" not in {item.status for item in results.values()}, (suite.source_path, {clause: (item.status, item.suggestions) for clause, item in results.items()})
        checked += 1
    assert checked


@pytest.mark.parametrize("entry", ["U02", "U03", "U05", "U08", "U09", "U10", "U11", "U14", "U15", "U17", "U18"])
def test_uaes_docx_is_decided_or_unable_without_a_model(tmp_path, leaf_resource, monkeypatch, entry):
    from audit.checker_lib.agents import AgentAudit
    from audit.contracts import AuditInput, RuleInput

    resource = leaf_resource(f"uaes/{entry}", ARCHIVED)
    assert ".docx" in resource["carriers"]
    path = _bare_docx(tmp_path, monkeypatch)
    rule = RuleInput.of(json.loads((ARCHIVED / "uaes" / entry / "rules.json").read_text(encoding="utf-8")), rule_id=entry)
    audit = AgentAudit(AuditInput(audit_id=entry, file_url=str(path)), rule, None, resource=resource)
    results = {clause: audit.evaluate(clause) for clause in resource["methods"]}
    assert {item.status for item in results.values()} <= {"NG", "UNABLE"}, {clause: (item.status, item.suggestions) for clause, item in results.items()}
    if entry in {"U05", "U08"}:
        assert {item.status for item in results.values()} == {"UNABLE"}
        assert {item.suggestions for item in results.values()} == {"检查程序不能按本条款读取DOCX格式的原件，需人工复核。"}


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
    assert found["uaes/U08"].related_suite_keys == ("UAES:U07",)
    assert not (tmp_path / "示例客户" / "Z01").exists()


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
            checker = "from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent\n\ndef create_audit(audit_file, rule, group_context=None):\n    return UaesControlPlanAgent\n"
        elif mutate == "coverage":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}, "directory_coverage": "partial"}
        elif mutate == "related":
            rules = {"rules": [{"id": "C1", "rule_to_check": "clause"}], "checker_binding": {"C1": "checker:create_audit"}, "related_suite_keys": ["uaes/U07"]}
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
    (root / "uaes").mkdir(parents=True)
    (root / "uaes" / "U08").symlink_to(outside / "secret", target_is_directory=True)
    with pytest.raises(ValueError, match="predefined_path_escape"):
        load_predefined_checkers(root)
    shutil.rmtree(root)
    write_leaf(root, "uaes/U08")
    (root / "uaes2").mkdir(parents=True)
    (root / "uaes2" / "U08").symlink_to(root / "uaes" / "U08", target_is_directory=True)
    with pytest.raises(ValueError, match="predefined_duplicate_path"):
        load_predefined_checkers(root)


def test_scan_does_not_execute_checker_source(tmp_path):
    write_leaf(tmp_path, "uaes/U08", checker=CHECKER + "raise SystemExit('scan executed the checker')\n")
    found = load_predefined_checkers(tmp_path)
    assert found[0].source_path == "uaes/U08"


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
    assert "COPY packages /app/packages" in app
    # Deep is a stage of the same image graph: it runs the app stage's own /app source, checkers included.
    assert "FROM app AS deep" in app
    assert "ln -s /app/packages/audit/src/audit /opt/agent/semantic" in app
    ignored = {line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    assert not ({"packages", "packages/audit", "**/checkers", "packages/audit/src/audit/checkers"} & ignored)
    uv = shutil.which("uv")
    assert uv
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT / "packages/audit")], check=True, cwd=ROOT)
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as package:
        names = set(package.namelist())
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


def test_u13_accepts_image_formats_only(tmp_path, leaf_resource):
    # v0.0 U13:1 "提交文件为图片格式": a PDF is not an image format, so no implementation reads it.
    import io

    from audit.checker_lib.agents import AgentAudit
    from audit.contracts import AuditInput, RuleInput
    from PIL import Image

    resource = leaf_resource("uaes/U13", ARCHIVED)
    rule = RuleInput.of(json.loads((ARCHIVED / "uaes/U13/rules.json").read_text(encoding="utf-8")), rule_id="u13")
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    statuses = {}
    for suffix in (".png", ".pdf"):
        path = tmp_path / f"photo{suffix}"
        path.write_bytes(stream.getvalue())
        statuses[suffix] = AgentAudit(AuditInput(audit_id=suffix, file_url=str(path)), rule, None, resource=resource).evaluate("U13:1").status
    assert statuses == {".png": "PASS", ".pdf": "UNABLE"}
