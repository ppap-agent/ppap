"""Import lint for independent leaves and new factory candidates.

Stored historical programs and v2/v3/v4 packages are not reclassified by it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_VERSION_DIRECTORY = re.compile(r"^v[0-9]+$")
_LEAF_SOURCES = frozenset({"checker.py", "resources.py"})
_DYNAMIC = frozenset({"import_module", "__import__"})
_LEGACY_FACADES = frozenset({"audit.engine", "audit.engine.routing", "audit.engine.engine", "audit.engine.app_runtime", "audit.standard_library"})


def _forbidden(module: str) -> bool:
    return module == "audit.checkers" or module.startswith("audit.checkers.") or module in _LEGACY_FACADES


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


def _scan(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return tuple(f"{path}:{item}" for item in import_violations(tree))


def directory_import_violations(root: Path) -> tuple[str, ...]:
    """Static imports and literal ``import_module`` / ``__import__`` calls.

    A version directory with ``__init__.py`` is a legacy package and is skipped.
    """
    problems: list[str] = []
    if not root.is_dir():
        return ()
    for version in sorted(path for path in root.iterdir() if path.is_dir() and _VERSION_DIRECTORY.fullmatch(path.name)):
        marker = version / "__init__.py"
        if marker.is_file() and not marker.is_symlink():
            continue
        for path in sorted(version.rglob("*")):
            if path.name not in _LEAF_SOURCES or not path.is_file() or path.is_symlink():
                continue
            problems.extend(_scan(path))
    return tuple(problems)
