"""Product-owned standard suites with explicit carriers and source clause bindings.

The imports below identify library implementations, not runtime routing tiers.
Unsupported carriers never fall through to another customer's/general checker.
Templates are provenance references only: no customer's originals are published.

Directory leaves under ``checkers/vN/<customer>/<group>`` are a separate source
identity (``v5/UAES/U08``). They are not merged into ``standard_suites()``:
that tuple stays the historical 41 carrier artifacts, looked up by business
``suite_key``. A missing leaf is an assembly error, never a legacy carrier.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from audit.presets import customer_presets, preset_document_type, preset_rule_body, preset_source_bindings


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


# Only dependencies actually read by the current implementation are declared.
# Other reference types (e.g. drawings) remain frozen source inputs, not invented suites.
_RELATED = {
    "UAES:U05": ("UAES:U08",),
    "UAES:U07": ("UAES:U12",),
    "UAES:U08": ("UAES:U07", "UAES:U16"),
    "理想:L07": ("理想:L11",),
    "蔚来:N02": ("蔚来:N05",),
    "蔚来:N03": ("蔚来:N02", "蔚来:N04", "蔚来:N05"),
    "蔚来:N05": ("蔚来:N04",),
}


def _class_name(cls: type[Any]) -> str:
    return f"{cls.__module__}:{cls.__name__}"


def _carriers(customer: str, entry: str) -> dict[str, str]:
    from audit.checkers.v4 import li_auto, uaes
    from audit.checkers.v4.nio.control_plan import NioControlPlanAgent
    from audit.checkers.v4.nio.measurement import NioMeasurementAgent
    from audit.checkers.v4.nio.pfmea import NioPfmeaAgent
    from audit.checkers.v4.nio.process_flow import NioProcessFlowAgent
    from audit.checkers.v4.nio.special_characteristics import NioSpecialCharacteristicsAgent

    if customer == "UAES":
        cls = uaes.CLASS_BY_ENTRY[entry]
        return {extension: _class_name(cls) for extension in sorted(cls.SUPPORTED_EXTENSIONS)}
    if customer == "理想":
        carriers = {extension: _class_name(li_auto.EXCEL_AGENTS[entry]) for extension in (".xlsx", ".xls", ".xlsm")}
        for extension, classes in ((".pdf", li_auto.PDF_AGENTS), (".docx", li_auto.DOCX_AGENTS)):
            if entry in classes:
                carriers[extension] = _class_name(classes[entry])
        return carriers
    nio_classes = {"N01": NioMeasurementAgent, "N02": NioPfmeaAgent, "N03": NioControlPlanAgent, "N04": NioSpecialCharacteristicsAgent, "N05": NioProcessFlowAgent}
    return {extension: _class_name(nio_classes[entry]) for extension in (".xlsx", ".xls", ".xlsm", ".pdf")}


def standard_suites() -> tuple[StandardSuite, ...]:
    """Return 41 editable source artifacts, not claims of oracle correctness."""
    result = []
    for preset in customer_presets():
        key = f"{preset.customer}:{preset.entry_id}"
        methods = preset_source_bindings(preset, checker_profile="v4")
        carriers = _carriers(preset.customer, preset.entry_id)
        # Binding identities are independent of the declaration's position/name.
        entrypoints = {clause: "checker:check" for clause in methods}
        resources = {"carriers": carriers, "methods": methods}
        source = (
            "from audit.standard_library import check_clause, prepare_target\n"
            f"RESOURCES = {resources!r}\n"
            "def check(context):\n    return check_clause(context, RESOURCES)\n"
            "def precheck(context):\n    return prepare_target(context, RESOURCES)\n"
        ).encode()
        rules = preset_rule_body(preset.customer, preset.entry_id)
        rules.pop("rule_profile", None)
        # The data retains source provenance. Runtime selection uses only the
        # frozen suite and artifact, never rule_profile or a class-name heuristic.
        rules["checker_binding"] = dict(entrypoints)
        rules["title"] = preset.title
        files = {
            "checker.py": source,
            "rules.json": _json(rules),
            "appendix.json": b"[]",
            "templates.json": _json([{"kind": "structure", "source": preset.source_path, "distribution": "reference-only", "downloadable": False}]),
            "tests.json": _json([{"oracle": "assets/corpus/oracle.csv", "source_entry": preset.entry_id, "correctness": "independent-human-oracle-required"}]),
        }
        result.append(
            StandardSuite(key, preset_document_type(preset), preset.customer, preset.entry_id, _RELATED.get(key, ()), MappingProxyType(files), MappingProxyType(entrypoints), preset.source_path)
        )
    return tuple(result)


@lru_cache(maxsize=1)
def library_revision() -> str:
    """Fingerprint of shared runtime code, not of any checker version tree.

    ``checkers/vN`` business sources stay out. Directory leaves use
    ``checker_digest``. Historical v2/v3/v4 packages use
    ``legacy_runtime_revision`` and are folded in only for suites whose
    ``checker.py`` or ``resources.py`` names that package. A v4 edit therefore
    does not move this public fingerprint. ``ValidationRecord.samples_not_rerun``
    still sees it for the suite that runs that package.
    """
    import documents

    root = Path(__file__).parent
    return hash_library((("audit", root), ("documents", Path(documents.__file__).parent)))


# New predefined checkers are files on disk, not entries in a customer registry.
# A version directory that is already a Python package (v2/v3/v4) stays legacy.
_CHECKERS_ROOT = Path(__file__).resolve().parent / "checkers"
_VERSION_DIRECTORY = re.compile(r"^v[0-9]+$")
_LEGACY_FAMILIES = ("v2", "v3", "v4")
_FAMILY_TOKEN = re.compile(br"audit\.checkers\.(v2|v3|v4)(?![0-9A-Za-z_])")


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
    # Every version tree is a checker, including historical packages. Shared
    # runtime is checker_lib, the engine, and the rest of audit.
    return not (len(parts) >= 2 and parts[0] == "checkers" and _VERSION_DIRECTORY.fullmatch(parts[1]) is not None)


def legacy_families(files: Mapping[str, bytes]) -> tuple[str, ...]:
    """Version packages named by a suite's own checker or resources source."""
    blob = b"\n".join(files.get(name, b"") for name in ("checker.py", "resources.py"))
    found: list[str] = []
    for match in _FAMILY_TOKEN.finditer(blob):
        family = match.group(1).decode()
        if family not in found:
            found.append(family)
    return tuple(found)


def legacy_tree_revision(audit_root: Path, family: str) -> str:
    """Hash one historical checker package. Not part of ``library_revision``."""
    if family not in _LEGACY_FAMILIES:
        raise ValueError("unknown_legacy_runtime")
    directory = audit_root / "checkers" / family
    paths = tuple(path for path in sorted(directory.rglob("*.py")) if path.is_file() and not path.is_symlink()) if directory.is_dir() else ()
    digest = hashlib.sha256()
    for path in paths:
        digest.update(f"audit/{path.relative_to(audit_root)}".encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=4)
def legacy_runtime_revision(family: str) -> str:
    """Installed fingerprint of one historical checker package."""
    return legacy_tree_revision(Path(__file__).resolve().parent, family)


def combine_library_revision(shared: str, family_revisions: tuple[tuple[str, str], ...]) -> str:
    """Public fingerprint, plus each named historical package in sorted order."""
    if not family_revisions:
        return shared
    digest = hashlib.sha256()
    digest.update(b"shared\0")
    digest.update(shared.encode())
    digest.update(b"\0")
    for family, revision in family_revisions:
        digest.update(family.encode())
        digest.update(b"\0")
        digest.update(revision.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def library_revision_for_families(families: tuple[str, ...] | list[str]) -> str:
    """Shared fingerprint, with one installed legacy package hash per family."""
    ordered = tuple(sorted(dict.fromkeys(families)))
    if any(family not in _LEGACY_FAMILIES for family in ordered):
        raise ValueError("unknown_legacy_runtime")
    shared = library_revision()
    return combine_library_revision(shared, tuple((family, legacy_runtime_revision(family)) for family in ordered))


def suite_library_revision(files: Mapping[str, bytes]) -> str:
    """Fingerprint a suite proof compares. Directory leaves stay on the public one."""
    return library_revision_for_families(legacy_families(files))


def execution_library_revision(groups: Sequence[Mapping[str, bytes]]) -> str:
    """Union of legacy families actually named by the suites in one run."""
    found: list[str] = []
    for files in groups:
        for family in legacy_families(files):
            if family not in found:
                found.append(family)
    return library_revision_for_families(found)


def stored_library_needs_rerun(stored_library: str, families: tuple[str, ...] | list[str]) -> bool:
    """Same comparison ``suite_releases._validation`` uses for samples_not_rerun."""
    return stored_library != library_revision_for_families(families)


_LEAF_FILES = frozenset({"checker.py", "rules.json", "resources.py", "appendix.json", "templates.json", "tests.json"})
_REQUIRED_LEAF_FILES = frozenset({"checker.py", "rules.json"})
_OPTIONAL_JSON = frozenset({"appendix.json", "templates.json", "tests.json"})
_PREDEFINED_ENTRYPOINT = "checker:create_audit"


def predefined_directory_suites() -> tuple[StandardSuite, ...]:
    """Installed directory leaves. Source path, not suite_key, is their identity."""
    return load_predefined_checkers()


def load_predefined_checkers(root: Path | None = None) -> tuple[StandardSuite, ...]:
    """Read ``v<digits>/<customer>/<group>`` leaves. Do not import or execute them.

    Legacy version packages are skipped. Any other broken, escaping, or
    unbound-to-the-wrong-entrypoint leaf fails; nothing is filled from v4/v3/v2.
    """
    base = (root or _CHECKERS_ROOT).resolve()
    if not base.is_dir():
        raise ValueError("predefined_root_missing")
    suites: list[StandardSuite] = []
    seen: set[Path] = set()
    for version in _entries(base):
        if not _VERSION_DIRECTORY.fullmatch(version.name):
            continue
        _inside(version, base)
        if not version.is_dir():
            raise ValueError("predefined_unknown_asset")
        if _legacy_version_package(version):
            continue
        customers = _entries(version)
        if not customers:
            raise ValueError("predefined_missing_leaf_file")
        for customer in customers:
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
                suites.append(_load_leaf(base, version.name, customer.name, group))
    return tuple(sorted(suites, key=lambda suite: suite.source_path.encode("utf-8")))


def _legacy_version_package(version: Path) -> bool:
    marker = version / "__init__.py"
    if not marker.is_file() or marker.is_symlink():
        return False
    if _has_asset_leaf(version):
        raise ValueError("predefined_directory_conflict")
    return True


def _has_asset_leaf(version: Path) -> bool:
    try:
        customers = list(version.iterdir())
    except OSError:
        return False
    for customer in customers:
        if customer.is_symlink() or not customer.is_dir():
            continue
        try:
            groups = list(customer.iterdir())
        except OSError:
            continue
        for group in groups:
            if group.is_symlink() or not group.is_dir():
                continue
            if (group / "checker.py").is_file() or (group / "rules.json").is_file():
                return True
    return False


def _entries(directory: Path) -> list[Path]:
    try:
        children = list(directory.iterdir())
    except OSError:
        raise ValueError("predefined_path_escape") from None
    children.sort(key=lambda path: path.name.encode("utf-8"))
    return children


def _inside(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve()
    except OSError:
        raise ValueError("predefined_path_escape") from None
    if not resolved.is_relative_to(root):
        raise ValueError("predefined_path_escape")


def _component(name: str) -> None:
    if not name or name in {".", ".."} or name.strip() != name or any(char in name for char in ":/\\\x00"):
        raise ValueError("predefined_invalid_name")


def _load_leaf(root: Path, version: str, customer: str, group: Path) -> StandardSuite:
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
        f"{version}/{customer}/{group.name}",
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
