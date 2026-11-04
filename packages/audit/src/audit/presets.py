"""Immutable product-owned V4 rule inputs, independent of tenant custom rules.

Extraction is not execution coverage. The Resolver must bind a source clause
explicitly before a Checker may produce a machine result for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files


@dataclass(frozen=True)
class PresetClause:
    id: str
    source_number: str
    category: str
    text: str
    locator: str


@dataclass(frozen=True)
class CustomerPreset:
    customer: str
    entry_id: str
    source_type: str
    level: str
    title: str
    source_path: str
    source_sha256: str
    clauses: tuple[PresetClause, ...]


@lru_cache(maxsize=1)
def customer_presets() -> tuple[CustomerPreset, ...]:
    data = json.loads(files("audit").joinpath("preset_rules.json").read_text(encoding="utf-8"))
    return tuple(CustomerPreset(**{**row, "clauses": tuple(PresetClause(**clause) for clause in row["clauses"])}) for row in data)


def preset_rule(customer: object, entry_id: object) -> CustomerPreset:
    for rule in customer_presets():
        if rule.customer == customer and rule.entry_id == entry_id:
            return rule
    raise ValueError("unknown_customer_rule")


def preset_document_type(preset: CustomerPreset) -> str:
    from audit.kinds import PpapDocumentType as T

    types = {
        "T01": T.DIMENSION_MEASUREMENT_REPORT,
        "T02": T.DVP_R,
        "T03": T.DPVR,
        "T04": T.SUB_SUPPLIER_LIST,
        "T05": T.CHECKING_FIXTURE_APPROVAL_REPORT,
        "T06": T.TOOLING_APPROVAL_REPORT,
        "T07": T.MSA_REPORT,
        "T08": T.INITIAL_PROCESS_CAPACITY_STUDY,
        "T09": T.PROCESS_FLOW_CHART,
        "T10": T.PFMEA,
        "T11": T.CONTROL_PLAN,
        "T12": T.PROCESS_SPECIAL_CHARACTERISTIC_LIST,
        "T13": T.EQUIPMENT_LIST,
        "T14": T.PLANT_CAPACITY_EVALUATION_REPORT,
        "T15": T.XIR_ASSEMBLY_ISSUE_REPORT,
        "T16": T.ANNUAL_TEST_REQUIREMENTS,
        "T17": T.PSW,
    }
    legacy = types.get(preset.source_type)
    return legacy.get_type_name() if legacy else preset.source_type


def preset_rule_body(customer: str, entry_id: str) -> dict:
    preset = preset_rule(customer, entry_id)
    return {
        "kind": "engine",
        "rule_profile": "v4",
        "type": preset_document_type(preset),
        "preset_key": preset.entry_id,
        "preset_source_sha256": preset.source_sha256,
        "customers": [preset.customer],
        "models": [],
        "parts": [],
        "rules": [{"id": clause.id, "category": clause.category, "priority": "P0", "rule_to_check": clause.text, "type_of_rule": "text"} for clause in preset.clauses],
    }


def validate_preset_source(body: dict) -> CustomerPreset:
    """Validate source identity, without certifying a task's effective clauses."""
    customers = body.get("customers")
    if not isinstance(customers, list) or len(customers) != 1:
        raise ValueError("invalid_preset_source")
    preset = preset_rule(customers[0], body.get("preset_key"))
    if body.get("preset_source_sha256") != preset.source_sha256 or body.get("type") != preset_document_type(preset):
        raise ValueError("invalid_preset_source")
    return preset


def validate_preset_body(body: dict) -> CustomerPreset:
    preset = validate_preset_source(body)
    expected = {clause.id: clause.text for clause in preset.clauses}
    actual = body.get("rules")
    if not isinstance(actual, list) or len(actual) != len(expected) or any(not isinstance(row, dict) for row in actual):
        raise ValueError("invalid_preset_clauses")
    if {row.get("id"): row.get("rule_to_check") for row in actual} != expected:
        raise ValueError("invalid_preset_clauses")
    return preset


def preset_source_bindings(preset: CustomerPreset, *, checker_profile: str) -> dict[str, tuple[str, str]]:
    from audit.checkers.v4.li_auto.bindings import BINDINGS as li_auto
    from audit.checkers.v4.nio.bindings import BINDINGS as nio
    from audit.checkers.v4.uaes.bindings import BINDINGS as uaes

    bindings = {"UAES": uaes, "理想": li_auto, "蔚来": nio}[preset.customer].get(checker_profile, {})
    return {clause.id: (clause.text, bindings[clause.id]) for clause in preset.clauses if clause.id in bindings}
