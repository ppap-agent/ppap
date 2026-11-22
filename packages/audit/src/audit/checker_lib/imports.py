"""Import lint for independent leaves, new factory candidates and the company implementations.

Stored historical programs are not reclassified by it.
"""

from __future__ import annotations

import ast

from audit.standard_suites import CUSTOMER_DIRECTORIES

_DYNAMIC = frozenset({"import_module", "__import__"})
_LEGACY_FACADES = frozenset({"audit.engine", "audit.standard_library"})
_LIBRARY = "audit.checker_lib"


# Company reader implementations run only through a leaf's declared resource, never by import.
_IMPLEMENTATIONS = tuple(f"{_LIBRARY}.{company}" for company in CUSTOMER_DIRECTORIES)


def _forbidden(module: str) -> bool:
    return module == "audit.checkers" or module.startswith("audit.checkers.") or module in _LEGACY_FACADES or any(module == name or module.startswith(name + ".") for name in _IMPLEMENTATIONS)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def import_violations(tree: ast.AST) -> tuple[str, ...]:
    """Static imports and literal ``import_module`` / ``__import__`` calls.

    A non-literal dynamic import is reported too: the target is not visible.
    Comments are not imports. ``lineno: detail`` has no file path.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden(alias.name):
                    found.append(f"{node.lineno}: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden(module):
                found.append(f"{node.lineno}: {module}")
            if module == "audit":
                for alias in node.names:
                    if _forbidden("audit." + alias.name):
                        found.append(f"{node.lineno}: audit.{alias.name}")
        elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC:
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                found.append(f"{node.lineno}: non_literal_import")
            elif _forbidden(node.args[0].value):
                found.append(f"{node.lineno}: {node.args[0].value}")
    return tuple(found)


def _company(module: str) -> str | None:
    parts = module.split(".")
    return parts[2] if len(parts) > 2 and ".".join(parts[:2]) == _LIBRARY and parts[2] in CUSTOMER_DIRECTORIES else None


def _absolute(node: ast.ImportFrom, package: str) -> str:
    if not node.level:
        return node.module or ""
    base = package.split(".")[: len(package.split(".")) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def company_import_violations(tree: ast.AST, package: str) -> tuple[str, ...]:
    """Imports that cross a company boundary inside ``audit.checker_lib``.

    ``package`` is the importing module's package: ``audit.checker_lib`` for the shared
    layer, ``audit.checker_lib.<company>[...]`` for a company. A company imports its own
    code, the shared layer, the audit engine, documents and third parties; never another
    company or a leaf. The shared layer imports no company. Relative imports and literal
    ``import_module`` / ``__import__`` targets are resolved; a non-literal target is the
    declared-carrier loader and is not judged here. ``lineno: module`` has no file path.
    """
    own = _company(package)
    found: list[str] = []

    def judge(lineno: int, module: str) -> None:
        company = _company(module)
        if (company is not None and company != own) or module == "audit.checkers" or module.startswith("audit.checkers."):
            found.append(f"{lineno}: {module}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                judge(node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(node, package)
            judge(node.lineno, module)
            if _company(module) is None:
                for alias in node.names:
                    judge(node.lineno, f"{module}.{alias.name}")
        elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            judge(node.lineno, node.args[0].value)
    return tuple(found)
