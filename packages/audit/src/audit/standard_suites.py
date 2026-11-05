"""Product-owned standard suites with explicit carriers and source clause bindings.

The imports below identify library implementations, not runtime routing tiers.
Unsupported carriers never fall through to another customer's/general checker.
Templates are provenance references only: no customer's originals are published.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

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
    """Content identity for the installed shared algorithms and document readers."""
    import documents

    root = Path(__file__).parent
    digest = hashlib.sha256()
    for package, directory in (("audit", root), ("documents", Path(documents.__file__).parent)):
        for path in sorted(directory.rglob("*.py")):
            digest.update(f"{package}/{path.relative_to(directory)}".encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()
