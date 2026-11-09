"""New factories cannot reach old business routing through import facades."""

import ast

import pytest
from audit.checker_lib.imports import import_violations


@pytest.mark.parametrize(
    "source",
    [
        "import audit.engine.routing as old",
        "from audit.engine.routing import resolve_engine_agent_class as select",
        "from audit.engine import resolve_engine_agent_class",
        "import audit.engine as old\nold.resolve_engine_agent_class('控制计划', 'file.xlsx')",
        "from audit import engine as old",
        "from audit.engine import routing as old",
        "from audit.engine import EXCEL_AGENTS",
        "from audit.engine import run_audit",
        "import audit.engine.engine as old",
        "import audit.engine.app_runtime as old",
        "import audit.standard_library as old",
        "from audit import standard_library as old",
        "from importlib import import_module\nold = import_module('audit.standard_library')",
        "import importlib\nold = importlib.import_module('audit.engine.routing')",
        "old = __import__('audit.engine')",
    ],
)
def test_legacy_execution_facades_are_rejected(source):
    assert import_violations(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "from audit.engine.checkers.date_utils import parse_date",
        "import audit.engine.checkers.date_utils as dates",
        "from audit.engine.runtime import bind_runtime",
        "from audit.engine.rule_engine.context import RuleContext",
        "from audit.checker_lib.source import SourceAudit",
    ],
)
def test_shared_helpers_remain_available(source):
    assert import_violations(ast.parse(source)) == ()
