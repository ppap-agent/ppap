"""Import lint for independent leaves and new factory candidates.

Stored historical programs are not reclassified by it.
"""

from __future__ import annotations

import ast

_DYNAMIC = frozenset({"import_module", "__import__"})
_LEGACY_FACADES = frozenset({"audit.engine", "audit.engine.routing", "audit.standard_library"})


# Company reader implementations run only through a leaf's declared resource, never by import.
_IMPLEMENTATIONS = ("audit.checker_lib.li", "audit.checker_lib.nio", "audit.checker_lib.uaes")


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
            if module == "audit.standard_library" and any(alias.name == "check_clause" for alias in node.names):
                found.append(f"{node.lineno}: audit.standard_library.check_clause")
        elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC:
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                found.append(f"{node.lineno}: non_literal_import")
            elif _forbidden(node.args[0].value):
                found.append(f"{node.lineno}: {node.args[0].value}")
    return tuple(found)
