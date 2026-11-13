"""Independent leaves and factory candidates cannot import other implementations or old routing."""

import ast

import pytest
from audit.checker_lib.imports import import_violations

REJECTED = [
    ("import audit.checkers.uaes.U08", "audit.checkers.uaes.U08"),
    ("from audit.checkers.li.L01 import checker", "audit.checkers.li.L01"),
    ("from audit import checkers", "audit.checkers"),
    ("from audit.standard_library import check_clause, check_factory", "audit.standard_library.check_clause"),
    ("import audit.checker_lib.nio.excel_agent", "audit.checker_lib.nio.excel_agent"),
    ("import audit.engine.routing as old", "audit.engine.routing"),
    ("from audit.engine.routing import resolve_engine_agent_class as select", "audit.engine.routing"),
    ("import audit.engine as old\nold.resolve_engine_agent_class('控制计划', 'file.xlsx')", "audit.engine"),
    ("from audit import engine as old", "audit.engine"),
    ("from audit.engine import routing as old", "audit.engine"),
    ("import audit.standard_library as old", "audit.standard_library"),
    ("from audit import standard_library as old", "audit.standard_library"),
    ("from importlib import import_module\nold = import_module('audit.standard_library')", "audit.standard_library"),
    ("import importlib\nold = importlib.import_module('audit.checker_lib.uaes.native')", "audit.checker_lib.uaes.native"),
    ("old = __import__('audit.engine')", "audit.engine"),
    ("from importlib import import_module\nname = 'audit.checker_lib.excel'\nimport_module(name)", "non_literal_import"),
]

ALLOWED = [
    "from audit.checker_lib.excel import load_workbook",
    "from importlib import import_module\nimport_module('audit.checker_lib.excel')",
    "# import audit.engine.checkers.visual_facts\n# from audit.checkers.uaes.U08 import checker",
    "from audit.engine.checkers.date_utils import parse_date",
    "import audit.engine.checkers.date_utils as dates",
    "from audit.engine.runtime import bind_runtime",
    "from audit.engine.rule_engine.context import RuleContext",
    "from audit.checker_lib.source import SourceAudit",
]


@pytest.mark.parametrize(("source", "violation"), REJECTED)
def test_import_lint_rejects(source, violation):
    assert any(problem.endswith(": " + violation) for problem in import_violations(ast.parse(source)))


@pytest.mark.parametrize("source", ALLOWED)
def test_import_lint_allows(source):
    assert import_violations(ast.parse(source)) == ()
