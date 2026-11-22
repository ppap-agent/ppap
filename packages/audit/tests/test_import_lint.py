"""Leaves and factory candidates cannot import implementations; a company implementation cannot import another company."""

import ast
from pathlib import Path

import audit.checker_lib
import pytest
from audit.checker_lib.imports import company_import_violations, import_violations

REJECTED = [
    ("import audit.checkers.uaes.U08", "audit.checkers.uaes.U08"),
    ("from audit.checkers.li.L01 import checker", "audit.checkers.li.L01"),
    ("from audit import checkers", "audit.checkers"),
    ("from audit.standard_library import check_factory", "audit.standard_library"),
    ("import audit.checker_lib.nio.excel_agent", "audit.checker_lib.nio.excel_agent"),
    ("import audit.engine as old", "audit.engine"),
    ("from audit import engine as old", "audit.engine"),
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


LIBRARY = Path(audit.checker_lib.__file__).parent

COMPANY_REJECTED = [
    ("audit.checker_lib.uaes", "from audit.checker_lib.nio.excel_agent import rule", "audit.checker_lib.nio.excel_agent"),
    ("audit.checker_lib.uaes", "import audit.checker_lib.li.pdf_agent", "audit.checker_lib.li.pdf_agent"),
    ("audit.checker_lib.uaes", "from audit.checker_lib import nio", "audit.checker_lib.nio"),
    ("audit.checker_lib.uaes", "from ..nio.cross_file_extractors import normalize_key", "audit.checker_lib.nio.cross_file_extractors"),
    ("audit.checker_lib.uaes.resources", "from ...li import excel_agent", "audit.checker_lib.li"),
    ("audit.checker_lib.nio", "from importlib import import_module\nimport_module('audit.checker_lib.uaes.native')", "audit.checker_lib.uaes.native"),
    ("audit.checker_lib.li", "from audit.checkers.li.L01 import resources", "audit.checkers.li.L01"),
    ("audit.checker_lib", "from audit.checker_lib.nio.cross_file_extractors import normalize_key", "audit.checker_lib.nio.cross_file_extractors"),
    ("audit.checker_lib", "from . import uaes", "audit.checker_lib.uaes"),
]

COMPANY_ALLOWED = [
    ("audit.checker_lib.nio", "from .cross_file_extractors import normalize_key"),
    ("audit.checker_lib.nio", "from audit.checker_lib.nio.control_plan_shared import CONTROL_PLAN_FIELD_ALIASES"),
    ("audit.checker_lib.uaes.resources", "from .. import excel"),
    ("audit.checker_lib.uaes", "from ..excel_agent import ExcelAgent, rule"),
    ("audit.checker_lib.uaes", "from audit.checker_lib.base_agent import BaseAgent"),
    ("audit.checker_lib.li", "from audit.engine.rule_engine import RuleContext"),
    ("audit.checker_lib.li", "from audit.contracts import AuditInput\nfrom documents.excel import load_excel_workbook\nfrom openpyxl import Workbook"),
    ("audit.checker_lib", "from audit.checker_lib.excel import load_workbook_auto"),
    ("audit.checker_lib", "import importlib\nimportlib.import_module(module)"),
]


@pytest.mark.parametrize(("package", "source", "violation"), COMPANY_REJECTED)
def test_company_import_lint_rejects(package, source, violation):
    assert any(problem.endswith(": " + violation) for problem in company_import_violations(ast.parse(source), package))


@pytest.mark.parametrize(("package", "source"), COMPANY_ALLOWED)
def test_company_import_lint_allows(package, source):
    assert company_import_violations(ast.parse(source), package) == ()


def test_every_implementation_file_stays_inside_its_company():
    checked, problems = 0, {}
    for path in sorted(LIBRARY.rglob("*.py")):
        package = ".".join(("audit", "checker_lib", *path.parent.relative_to(LIBRARY).parts))
        found = company_import_violations(ast.parse(path.read_text(encoding="utf-8")), package)
        if found:
            problems[str(path.relative_to(LIBRARY))] = found
        checked += 1
    assert checked and problems == {}
