"""The one customer register: a stable id, the identity key, the one name people read, and the customer's document type set M.

A customer's name and key name its rule set. Every task names its rule set by key, chosen by a person. The customer a
file writes is one of the file's facts: it is shown as recorded and never picks, renames or checks a rule set.

M is versioned. Template coverage (N_t) and rule coverage (N_r) count against the same M with
independent numerators: N_t counts types with a controlled template, N_r counts types with a current
rule, each type once however many rules it has. A missing M yields no coverage at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class TypeSet:
    """One version of a customer's document types in audit scope, as PPAP type codes."""

    version: str
    effective_from: str
    types: tuple[str, ...]


@dataclass(frozen=True)
class RegisteredCustomer:
    id: str
    # The identity value checker resources, suite keys, rule bodies and stored records carry; never shown.
    key: str
    # The one name people read.
    name: str
    type_set: TypeSet | None


_V3_33 = "2026-11-18"
_T = tuple(f"T{index:02d}" for index in range(1, 18))

CUSTOMERS: tuple[RegisteredCustomer, ...] = (
    RegisteredCustomer(
        "uaes",
        "UAES",
        "联电科技/UAES",
        TypeSet("V3.33", _V3_33, ("T04", "T07", "T08", "T09", "T10", "T11", "T12", "T17", *(f"E{index:02d}" for index in range(1, 12)))),
    ),
    RegisteredCustomer("li", "理想", "理想/Li Auto", TypeSet("V3.33", _V3_33, _T)),
    RegisteredCustomer("nio", "蔚来", "蔚来/Nio", TypeSet("V3.33", _V3_33, ("T01a", "T09", "T10", "T11", "T12"))),
)

# Counterpart documents: compared against, never audited on their own and in no customer's M.
COUNTERPARTS = {"E12": "采购件质量计划（PGQP1）"}


def find(customer_id: str) -> RegisteredCustomer | None:
    return next((customer for customer in CUSTOMERS if customer.id == customer_id), None)


def by_key(key: object) -> RegisteredCustomer | None:
    """The registered customer whose rule set a key names: a task's chosen customer, a rule's customers, a preset's customer."""
    return next((customer for customer in CUSTOMERS if customer.key == key), None)


def customer_name(key: str) -> str:
    """The name people read for a chosen rule set: a registered customer's name by its key; an enterprise's own rule set is named by itself.

    Only rule sets are named here. The customer a file writes is one of its facts and is always shown as recorded.
    """
    customer = by_key(key)
    return customer.name if customer else key


def type_code(rule_type: object, customer: object = None) -> str | None:
    """The PPAP dictionary code of a rule's document type for one customer: a customer-only E code as is, a standard type by its name.

    One type name can stand for different dictionary members per customer (PRD §1.5: 蔚来's measurement report is
    T01a, 理想's inspection report is T01, both read as 全功能检验报告). The customer's own preset entries decide;
    a customer without an entry for that name gets the plain T code.
    """
    if not isinstance(rule_type, str) or not rule_type.strip():
        return None
    text = rule_type.strip()
    if len(text) == 3 and text[0] == "E" and text[1:].isdigit():
        return text
    registered = by_key(customer)
    own = _standard_codes().get(registered.key if registered else None, {})
    return own.get(text) or _standard_codes()[None].get(text)


def type_name(code: str) -> str:
    """The document type name rules use for a code (an E code names itself)."""
    return next((name for codes in _standard_codes().values() for name, value in codes.items() if value == code), code)


@lru_cache(maxsize=1)
def type_names() -> dict[str, str]:
    """The one name people read for each type code the product shows: every type of every M, then the counterparts.

    A customer-only E code reads as its PPAP-00-A dictionary name, any other type as its rule type name.
    """
    from product.precheck_content import ENTRIES

    dictionary = {names[0]: names[1] for _customer, names in ENTRIES.values() if names[0].startswith("E")}
    codes = dict.fromkeys(code for customer in CUSTOMERS if customer.type_set for code in customer.type_set.types)
    return {**{code: dictionary.get(code) or type_name(code) for code in codes}, **COUNTERPARTS}


@lru_cache(maxsize=1)
def _standard_codes() -> dict[str | None, dict[str, str]]:
    """Type name → T code per preset customer, and under None the plain codes (T01, not T01a) every customer shares."""
    from audit.presets import customer_presets, preset_document_type

    codes: dict[str | None, dict[str, str]] = {None: {}}
    for preset in customer_presets():
        if not preset.source_type.startswith("T"):
            continue
        name = preset_document_type(preset)
        codes.setdefault(preset.customer, {})[name] = preset.source_type
        if len(preset.source_type) == 3:
            codes[None][name] = preset.source_type
    return codes


def covered(customer: RegisteredCustomer, codes: Iterable[str | None]) -> list[str]:
    """Types of M among `codes`, each once, in M's order."""
    present = set(codes)
    return [code for code in customer.type_set.types if code in present] if customer.type_set else []


def rule_type_codes(customer: RegisteredCustomer, rules: Iterable[dict]) -> set[str]:
    """Types with a current engine rule for this customer. `rules` are already the latest versions."""
    codes: set[str] = set()
    for rule in rules:
        named = rule.get("customers")
        if rule.get("kind") != "engine" or not isinstance(named, list):
            continue
        if customer.key in named and (code := type_code(rule.get("type"), customer.key)):
            codes.add(code)
    return codes


def in_type_set(rule: dict) -> bool:
    """Whether new tasks may use this rule: a rule naming registered customers is offered only for a type in their M.

    Rules of other types stay stored and keep their history (for example 蔚来 N06–N17 outside its five types); an
    unregistered customer or an unrecognised type is not judged here.
    """
    named = [customer for value in rule.get("customers") or [] if (customer := by_key(value))]
    codes = [(customer, type_code(rule.get("type"), customer.key)) for customer in named]
    if not named or any(code is None for _customer, code in codes):
        return True
    return any(customer.type_set and code in customer.type_set.types for customer, code in codes)


def coverage(customer: RegisteredCustomer, template_codes: Iterable[str | None], rule_codes: Iterable[str | None]) -> dict | None:
    """M and the two independent numerators, or None when M is not configured."""
    if not customer.type_set or not customer.type_set.types:
        return None
    type_set = customer.type_set
    return {
        "version": type_set.version,
        "effective_from": type_set.effective_from,
        "types": [{"code": code, "rule_type": type_name(code)} for code in type_set.types],
        "template_types": covered(customer, template_codes),
        "rule_types": covered(customer, rule_codes),
    }
