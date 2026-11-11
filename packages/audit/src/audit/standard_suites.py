"""Product-owned checker leaves under ``checkers/<company>/<group>``.

A leaf's path (``uaes/U08``) is its source identity; its rules, resources and
program are files on disk. Unsupported formats never fall through to another
customer's checker. A missing leaf is an assembly error.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class StandardSuite:
    suite_key: str
    document_type: str
    customer: str
    source_entry: str
    related_suite_keys: tuple[str, ...]
    files: Mapping[str, bytes]
    entrypoints: Mapping[str, str]
    source_path: str

    @property
    def revision_digest(self) -> str:
        """Content identity. source_path is omitted, so an identical copy keeps this digest."""
        objects = {name: hashlib.sha256(body).hexdigest() for name, body in self.files.items()}
        return hashlib.sha256(_json({"suite_key": self.suite_key, "related_suite_keys": sorted(self.related_suite_keys), "objects": objects})).hexdigest()

    @property
    def program_files(self) -> dict[str, bytes]:
        return {name: value for name, value in self.files.items() if name in {"checker.py", "resources.py"}}


@dataclass(frozen=True)
class PredefinedSource:
    """One real source path; historical packages have no executable leaf."""

    path: str
    title: str
    suite: StandardSuite | None = None


def predefined_sources(root: Path | None = None) -> tuple[PredefinedSource, ...]:
    """Read navigation from disk, without installing or selecting a Revision."""
    suites = predefined_directory_suites() if root is None else load_predefined_checkers(root)
    result = [PredefinedSource(suite.source_path, source_title(suite), suite) for suite in suites]
    paths = [row.path for row in result]
    if len(paths) != len(set(paths)):
        raise ValueError("predefined_duplicate_path")
    return tuple(sorted(result, key=lambda row: row.path.encode("utf-8")))


def source_title(suite: StandardSuite) -> str:
    rules = _json_object(suite.files["rules.json"])
    title = rules.get("title", suite.source_entry)
    if not isinstance(title, str) or not title.strip():
        raise ValueError("predefined_invalid_rules")
    return title


def validate_source_identity(path: str, rules: Mapping[str, Any]) -> None:
    """Business metadata may agree with a path, but cannot replace its identity."""
    parts = path.split("/")
    if len(parts) != 2:
        raise ValueError("predefined_invalid_name")
    customer, group = customer_name(parts[0]), parts[1]
    if "customers" in rules and rules["customers"] != [customer]:
        raise ValueError("predefined_identity_conflict")
    if "preset_key" in rules and rules["preset_key"] != group:
        raise ValueError("predefined_identity_conflict")


@lru_cache(maxsize=1)
def library_revision() -> str:
    """Fingerprint of shared runtime code, including the company reader implementations.

    Company directory leaves (``checkers/<company>/<group>``) stay out; a leaf's
    identity is ``checker_digest``. A change here makes
    ``ValidationRecord.samples_not_rerun`` true.
    """
    import documents

    root = Path(__file__).parent
    return hash_library((("audit", root), ("documents", Path(documents.__file__).parent)))


# Predefined checkers are files on disk, grouped by customer, not entries in a registry.
# Directory names are ASCII; rules, suite keys and presets keep the customer's own name.
CUSTOMER_DIRECTORIES = {"uaes": "UAES", "li": "理想", "nio": "蔚来"}


def customer_name(directory: str) -> str:
    return CUSTOMER_DIRECTORIES.get(directory, directory)


def customer_directory(customer: str) -> str:
    return next((name for name, value in CUSTOMER_DIRECTORIES.items() if value == customer), customer)


_CHECKERS_ROOT = Path(__file__).resolve().parent / "checkers"


def library_files(package: str, directory: Path) -> tuple[Path, ...]:
    """Python files that contribute to ``library_revision`` for one package root."""
    return tuple(path for path in sorted(directory.rglob("*.py")) if _counts_toward_library(package, directory, path))


def hash_library(entries: tuple[tuple[str, Path], ...]) -> str:
    """Hash the same path-and-bytes sequence ``library_revision`` uses."""
    digest = hashlib.sha256()
    for package, directory in entries:
        for path in library_files(package, directory):
            digest.update(f"{package}/{path.relative_to(directory)}".encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _counts_toward_library(package: str, directory: Path, path: Path) -> bool:
    if package != "audit":
        return True
    parts = path.relative_to(directory).parts
    if len(parts) < 2 or parts[0] != "checkers":
        return True
    # Company leaves are outside the public fingerprint; their digest is their identity.
    return len(parts) == 2


_LEAF_FILES = frozenset({"checker.py", "rules.json", "resources.py", "appendix.json", "templates.json", "tests.json"})
_REQUIRED_LEAF_FILES = frozenset({"checker.py", "rules.json"})
_OPTIONAL_JSON = frozenset({"appendix.json", "templates.json", "tests.json"})
_PREDEFINED_ENTRYPOINT = "checker:create_audit"


def predefined_directory_suites() -> tuple[StandardSuite, ...]:
    """Installed directory leaves. Source path, not suite_key, is their identity."""
    return load_predefined_checkers()


def load_predefined_checkers(root: Path | None = None) -> tuple[StandardSuite, ...]:
    """Read ``<customer>/<group>`` leaves. Do not import or execute them.

    Any broken, escaping, or unbound leaf fails; nothing is filled from another
    customer.
    """
    base = (root or _CHECKERS_ROOT).resolve()
    if not base.is_dir():
        raise ValueError("predefined_root_missing")
    suites: list[StandardSuite] = []
    seen: set[Path] = set()
    for customer in _entries(base):
        # Only directories are customers; the package marker and stray files are not sources.
        if not customer.is_dir() or customer.is_symlink():
            if customer.is_symlink() and customer.is_dir():
                raise ValueError("predefined_duplicate_path")
            continue
        _component(customer.name)
        _inside(customer, base)
        if not customer.is_dir():
            raise ValueError("predefined_unknown_asset")
        groups = _entries(customer)
        if not groups:
            raise ValueError("predefined_missing_leaf_file")
        for group in groups:
            _component(group.name)
            _inside(group, base)
            if not group.is_dir():
                raise ValueError("predefined_unknown_asset")
            real = group.resolve()
            if real in seen:
                raise ValueError("predefined_duplicate_path")
            seen.add(real)
            suites.append(_load_leaf(base, customer.name, group))
    return tuple(sorted(suites, key=lambda suite: suite.source_path.encode("utf-8")))


def _bytecode_artifact(path: Path) -> bool:
    # Baked images compile the leaf modules in place. That cache is not a checker asset.
    return path.name == "__pycache__" or path.suffix == ".pyc"


def _entries(directory: Path) -> list[Path]:
    try:
        children = list(directory.iterdir())
    except OSError:
        raise ValueError("predefined_path_escape") from None
    children = [path for path in children if not _bytecode_artifact(path)]
    children.sort(key=lambda path: path.name.encode("utf-8"))
    return children


def _inside(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve()
    except OSError:
        raise ValueError("predefined_path_escape") from None
    if not resolved.is_relative_to(root):
        raise ValueError("predefined_path_escape")
    if path.is_symlink():
        raise ValueError("predefined_duplicate_path" if path.is_dir() else "predefined_path_escape")


def _component(name: str) -> None:
    if not name or name in {".", ".."} or name.strip() != name or any(char in name for char in ":/\\\x00"):
        raise ValueError("predefined_invalid_name")


def _load_leaf(root: Path, directory: str, group: Path) -> StandardSuite:
    files: dict[str, bytes] = {}
    for child in _entries(group):
        _inside(child, root)
        if child.is_dir():
            raise ValueError("predefined_directory_conflict")
        if child.name not in _LEAF_FILES:
            raise ValueError("predefined_unknown_asset")
        body = _read_bytes(child)
        if not body:
            raise ValueError("predefined_missing_leaf_file")
        files[child.name] = body
    if not _REQUIRED_LEAF_FILES <= files.keys():
        raise ValueError("predefined_missing_leaf_file")
    _validate_python(files["checker.py"], "predefined_checker_syntax")
    if "resources.py" in files:
        _validate_python(files["resources.py"], "predefined_resources_syntax")
    rules = _json_object(files["rules.json"])
    validate_source_identity(f"{directory}/{group.name}", rules)
    customer = customer_name(directory)
    for name in _OPTIONAL_JSON:
        if name in files:
            _json_list(files[name])
    binding, clause_ids = _binding(rules)
    if not _defines_create_audit(files["checker.py"]):
        raise ValueError("predefined_checker_entrypoint_missing")
    suite_key = f"{customer}:{group.name}"
    # The leaf declares its own business dependencies. _RELATED is the legacy
    # carrier map and is not a fallback, a transitive walk, or a v4 import.
    _require_coverage(rules, binding, clause_ids)
    document_type = rules.get("type") if isinstance(rules.get("type"), str) else ""
    return StandardSuite(
        suite_key,
        document_type,
        customer,
        group.name,
        _related_keys(rules, suite_key),
        MappingProxyType(dict(sorted(files.items()))),
        MappingProxyType(binding),
        f"{directory}/{group.name}",
    )


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise ValueError("predefined_path_escape") from None


def _json_object(body: bytes) -> Any:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        raise ValueError("predefined_bad_json") from None
    if not isinstance(value, dict):
        raise ValueError("predefined_invalid_rules")
    return value


def _json_list(body: bytes) -> None:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        raise ValueError("predefined_bad_json") from None
    if not isinstance(value, list):
        raise ValueError("predefined_invalid_rules")


def _binding(rules: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    rows = rules.get("rules")
    if not isinstance(rows, list):
        raise ValueError("predefined_invalid_rules")
    ids: list[str] = []
    for row in rows:
        clause = row.get("id") if isinstance(row, dict) else None
        if not isinstance(clause, str) or not clause.strip() or clause.strip() != clause:
            raise ValueError("predefined_invalid_rules")
        ids.append(clause)
    if len(set(ids)) != len(ids):
        raise ValueError("predefined_invalid_rules")
    binding = rules.get("checker_binding", {})
    if not isinstance(binding, dict):
        raise ValueError("predefined_invalid_binding")
    known = set(ids)
    cleaned: dict[str, str] = {}
    for clause, entrypoint in binding.items():
        if not isinstance(clause, str) or clause not in known or entrypoint != _PREDEFINED_ENTRYPOINT:
            raise ValueError("predefined_invalid_binding")
        cleaned[clause] = entrypoint
    return cleaned, ids


def _related_keys(rules: Mapping[str, Any], suite_key: str) -> tuple[str, ...]:
    if "related_suite_keys" not in rules:
        return ()
    raw = rules["related_suite_keys"]
    if not isinstance(raw, list):
        raise ValueError("predefined_invalid_rules")
    keys: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item or item.strip() != item or len(item) > 200 or item == suite_key or any(char in item for char in "/\\\x00"):
            raise ValueError("predefined_invalid_rules")
        keys.append(item)
    if len(set(keys)) != len(keys):
        raise ValueError("predefined_invalid_rules")
    return tuple(keys)


def _require_coverage(rules: Mapping[str, Any], binding: Mapping[str, str], clause_ids: list[str]) -> None:
    declared = rules.get("directory_coverage", "complete")
    if not isinstance(declared, str) or declared not in {"partial", "complete"}:
        raise ValueError("predefined_invalid_rules")
    complete = set(binding) == set(clause_ids)
    if declared == "partial" and complete:
        raise ValueError("predefined_invalid_rules")
    if declared == "complete" and not complete:
        raise ValueError("predefined_partial_unmarked")


def _validate_python(source: bytes, syntax_code: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise ValueError(syntax_code) from None
    from audit.checker_lib.imports import import_violations

    if import_violations(tree):
        raise ValueError("predefined_legacy_checker_import")


def _defines_create_audit(source: bytes) -> bool:
    tree = ast.parse(source)
    return any(isinstance(node, ast.FunctionDef) and node.name == "create_audit" for node in tree.body)
