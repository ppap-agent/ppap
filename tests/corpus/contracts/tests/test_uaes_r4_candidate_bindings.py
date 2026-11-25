"""Every signed R4 clause has a concrete declaration without changing the official suite."""

import ast
import json
from importlib import import_module
from pathlib import Path

from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.standard_suites import _load_leaf

ROOT = Path(__file__).resolve().parents[4]
RULEBOOK = ROOT / "assets/uaes/rulebook/v1.0"
CANDIDATES = RULEBOOK / "candidates/m4"
OFFICIAL = ROOT / "packages/audit/src/audit/checkers/uaes"


def test_every_signed_uaes_r4_clause_binds_to_a_declared_candidate_method():
    signed = {row["id"]: row["rule_to_check"] for document in (RULEBOOK / "m4/条文").glob("*.json") for row in json.loads(document.read_bytes())["rules"]}
    leaves = sorted((CANDIDATES / "uaes").iterdir())
    assert len(leaves) == 19
    actual = {}
    for leaf in leaves:
        assert {item.name for item in leaf.iterdir()} == {"checker.py", "resources.py", "rules.json"}
        gate = (leaf / "checker.py").read_text()
        assert len(gate.splitlines()) == 8 and gate.splitlines()[1:] == (OFFICIAL / leaf.name / "checker.py").read_text().splitlines()[1:]
        statements = ast.parse((leaf / "resources.py").read_bytes()).body
        if ast.get_docstring(ast.Module(body=statements, type_ignores=[])) is not None:
            statements = statements[1:]
        assert len(statements) == 1
        assignment = statements[0]
        assert isinstance(assignment, ast.Assign) and len(assignment.targets) == 1
        name = assignment.targets[0]
        assert isinstance(name, ast.Name) and name.id == "RESOURCE"
        resource = ast.literal_eval(assignment.value)
        suite = _load_leaf(CANDIDATES, "uaes", leaf)
        assert suite.entrypoints == dict.fromkeys(resource["methods"], "checker:create_audit")
        assert resource["carriers"]
        for carrier in set(resource["carriers"].values()):
            module, name = carrier.split(":")
            assert module.startswith("audit.checker_lib.uaes.")
            cls = getattr(import_module(module), name)
            declared = dict(discover_declared_rules(getattr(cls, "TARGET_EXCEL_AGENT_CLASS", None) or cls))
            assert set(resource["methods"].values()) <= declared.keys()
        for row in json.loads(suite.files["rules.json"])["rules"]:
            assert row["id"] not in actual
            actual[row["id"]] = row["rule_to_check"]
    assert len(signed) == 63 and actual == signed


def test_both_revision_paths_load_as_candidates_for_the_same_installed_leaf():
    from corpus_contracts.candidate_run import candidate

    r3, installed = candidate(RULEBOOK / "candidates/m3/uaes/U08")
    r4, _ = candidate(CANDIDATES / "uaes/U08")
    assert r3.source_path == r4.source_path == "uaes/U08"
    assert r3.source_path in installed
    assert r3.revision_digest != r4.revision_digest
