"""Document-scoped interpretation changes over an unchanged rule obligation.

The cached RulePlan remains the rule-only proposal. A trace may strengthen its
identity interpretation for one target using current source evidence. The same
evaluator consumes the resulting plan; no refined plan enters the rule cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import Field, model_validator

from .document_tools import TableReader, col_number
from .evidence import EvidenceBudget, read_cell, resolve
from .models import Artifact, Citation, ExcelCell, Locator, PdfCell, RulePlan, WordCell


class PlanRefinement(Artifact):
    audit_id: str = Field(min_length=1, max_length=256)
    base_plan_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan: RulePlan
    reason: str = Field(min_length=1, max_length=4096)
    rule_evidence: list[Locator] = Field(min_length=1, max_length=256)
    document_evidence: list[Citation] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def explained(self) -> "PlanRefinement":
        if not self.reason.strip():
            raise ValueError("A plan refinement requires an explicit interpretation basis")
        return self


def plan_identity(plan: RulePlan) -> str:
    encoded = json.dumps(plan.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _references(value: Any, entities: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source", "target", "input"} and isinstance(item, str) and item in entities:
                return True
            if key == "inputs" and isinstance(item, list) and entities.intersection(item):
                return True
            if _references(item, entities):
                return True
    elif isinstance(value, list):
        return any(_references(item, entities) for item in value)
    return False


def _comparison_fields(value: Any, relationships: dict[str, Any], result: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        relationship_id = value.get("relationship_id")
        relationship = relationships.get(relationship_id) if isinstance(relationship_id, str) else None
        if value.get("kind") == "related" and relationship:
            for comparison in value["comparisons"]:
                for side in ("source", "target"):
                    result.setdefault(relationship[side], set()).add(comparison[side + "_field"])
        for child in value.values():
            _comparison_fields(child, relationships, result)
    elif isinstance(value, list):
        for child in value:
            _comparison_fields(child, relationships, result)


def _restore_key_fields(candidate: Any, original: Any, old_entities: dict[str, Any], new_entities: dict[str, Any], relationships: dict[str, Any]) -> None:
    """Erase only permitted key expansions before exact obligation comparison."""
    if isinstance(candidate, dict) and isinstance(original, dict):
        if candidate.get("kind") == original.get("kind") == "related":
            relationship_id = original.get("relationship_id")
            relationship = relationships.get(relationship_id) if isinstance(relationship_id, str) else None
            if relationship and relationship["kind"] == "same_instance":
                for side, entity_name in (("source_key", relationship["source"]), ("target_key", relationship["target"])):
                    old_key, new_key = original[side], candidate.get(side, {})
                    if new_key.get("fields") == old_key["fields"]:
                        continue
                    old_identity = old_entities[entity_name]["identity"]
                    new_identity = new_entities[entity_name]["identity"]
                    additions = set(new_identity) - set(old_identity)
                    fields = new_key.get("fields", [])
                    if not additions or not set(old_key["fields"]) <= set(fields) or set(fields) - set(old_key["fields"]) != additions:
                        raise ValueError("Refinement keys may only add strengthened identity components")
                    if [field for field in fields if field in old_key["fields"]] != old_key["fields"]:
                        raise ValueError("Refinement cannot reorder existing identity components")
                    new_key["fields"] = old_key["fields"]
        for key in candidate.keys() & original.keys():
            _restore_key_fields(candidate[key], original[key], old_entities, new_entities, relationships)
    elif isinstance(candidate, list) and isinstance(original, list):
        for left, right in zip(candidate, original):
            _restore_key_fields(left, right, old_entities, new_entities, relationships)


def _validate_refinement(change: PlanRefinement, original: RulePlan, documents: dict[str, dict[str, Any]], budget: EvidenceBudget) -> RulePlan:
    if change.base_plan_id != plan_identity(original):
        raise ValueError("Refinement refers to a different base plan")
    candidate = RulePlan.model_validate(change.plan.model_dump(mode="json"))
    before, after = original.model_dump(mode="json"), candidate.model_dump(mode="json")
    if before == after:
        raise ValueError("Refinement must change an interpretation")
    old_entities = {entity["id"]: entity for entity in before["ontology"]["entities"]}
    new_entities = {entity["id"]: entity for entity in after["ontology"]["entities"]}
    relationships = {relationship["id"]: relationship for relationship in before["ontology"]["relationships"]}
    compared_fields: dict[str, set[str]] = {}
    _comparison_fields(before["rules"], relationships, compared_fields)
    if list(old_entities) != list(new_entities):
        raise ValueError("Refinement cannot add or remove rule concepts")
    changed_entities = set()
    for name, previous in old_entities.items():
        current = new_entities[name]
        if not set(previous["identity"]) <= set(current["identity"]):
            raise ValueError("Refinement cannot weaken entity identity")
        additions = set(current["identity"]) - set(previous["identity"])
        if additions & compared_fields.get(name, set()):
            raise ValueError("Refinement identity must be independent of attributes under comparison")
        if [field for field in current["identity"] if field in previous["identity"]] != previous["identity"]:
            raise ValueError("Refinement cannot reorder existing identity components")
        if current != previous:
            changed_entities.add(name)
        current["grain"], current["identity"] = previous["grain"], previous["identity"]
        if current != previous:
            raise ValueError("Refinement may only clarify grain or strengthen identity")
    # Candidate is schema-validated independently; keep the original identity
    # lists for expansion checks before comparing every other obligation field.
    proposed_entities = {entity.id: entity.model_dump(mode="json") for entity in candidate.ontology.entities}
    _restore_key_fields(after["rules"], before["rules"], old_entities, proposed_entities, relationships)
    if after != before:
        raise ValueError("Refinement cannot change frozen rule obligations or matching policy")
    affected = [rule for rule in original.rules if _references(rule.program.model_dump(mode="json"), changed_entities)]
    if not affected:
        raise ValueError("Refinement must concern a concept used by an audit obligation")
    supplied = {locator.model_dump_json() for locator in change.rule_evidence}
    allowed = {rule.source_locator.model_dump_json() for rule in original.rules}
    if not supplied <= allowed or not {rule.source_locator.model_dump_json() for rule in affected} <= supplied:
        raise ValueError("Refinement requires the affected frozen rule sources")
    for citation in change.document_evidence:
        if citation.document_id not in documents or citation.locator is None:
            raise ValueError("Refinement evidence outside current document inputs")
        observed = resolve(documents[citation.document_id], citation.locator, budget)
        if not observed.known or not any(value.strip() for value in observed.values):
            raise ValueError("Refinement requires readable document evidence")
    return candidate


def effective_plans(refinements: list[PlanRefinement], documents: dict[str, dict[str, Any]], plans: dict[str, RulePlan]) -> dict[str, RulePlan]:
    """Validate document-scoped changes without mutating the cached base plans."""
    result, seen = dict(plans), set()
    budget = EvidenceBudget()
    for change in refinements:
        if change.audit_id in seen or change.audit_id not in plans:
            raise ValueError("Duplicate or unknown refinement target")
        seen.add(change.audit_id)
        result[change.audit_id] = _validate_refinement(change, plans[change.audit_id], documents, budget)
    return result


def _identity_source_fields(original: RulePlan, refined: RulePlan, entity_name: str) -> tuple[set[str], set[str]]:
    previous = next((entity for entity in original.ontology.entities if entity.id == entity_name), None)
    current = next((entity for entity in refined.ontology.entities if entity.id == entity_name), None)
    additions = set(current.identity) - set(previous.identity) if current and previous else set()
    compared: dict[str, set[str]] = {}
    if additions:
        relationships = {relation.id: relation.model_dump(mode="json") for relation in original.ontology.relationships}
        _comparison_fields([rule.model_dump(mode="json") for rule in original.rules], relationships, compared)
    return additions, compared.get(entity_name, set())


def identity_source_requirements(original: RulePlan, refined: RulePlan, entity_name: str) -> set[str]:
    """Read tested attributes needed to establish identity-source independence."""
    _additions, compared = _identity_source_fields(original, refined, entity_name)
    return compared


def _field_sources(
    record: dict[str, Any],
    field: str,
    documents: dict[str, dict[str, Any]],
    budget: EvidenceBudget,
    readers: dict[tuple[str, str], TableReader],
) -> tuple[set[tuple[str, str, str]], bool]:
    observations = record.get("observations", {}).get(field)
    if observations is None:
        observations = [{"state": record["states"].get(field), "evidence": record.get("evidence", {}).get(field)}]
    sources: set[tuple[str, str, str]] = set()
    complete = bool(observations)
    for observation in observations:
        evidence = observation.get("evidence")
        if not evidence:
            complete = False
            continue
        citation = Citation.model_validate(evidence["citation"])
        if citation.document_id not in documents or citation.locator is None:
            raise ValueError("Refinement attribute provenance outside current document inputs")
        document = documents[citation.document_id]
        locator = citation.locator
        if isinstance(locator, ExcelCell):
            table_name = locator.sheet
            row, column = int(re.sub("[A-Z]", "", locator.cell)), col_number(re.sub("[0-9]", "", locator.cell))
        elif isinstance(locator, WordCell):
            table_name, row, column = f"table:{locator.table}", locator.row, locator.column
        elif isinstance(locator, PdfCell):
            table_name, row, column = f"page-{locator.page}", locator.row, locator.column
        else:
            complete = False
            continue
        table = next((item for item in document.get("tables", []) if item["id"] == table_name), None)
        if table is None:
            raise ValueError("Refinement attribute provenance table unavailable")
        key = (citation.document_id, table_name)
        if key not in readers:
            readers[key] = TableReader(table, budget)
        cell = read_cell(table, row, column, reader=readers[key])
        # read_cell resolves native merged cells and DOCX aliases to their
        # actual source anchor; two different locator strings are not proof of
        # independent data. Logical fields retain every actual observation.
        sources.add((citation.document_id, table_name, cell["anchor"]))
        complete = complete and observation.get("state") == "known" and cell.get("state") == "known"
    return sources, complete and bool(sources)


def validate_identity_provenance(original: RulePlan, refined: RulePlan, entity_name: str, records: list[dict[str, Any]], documents: dict[str, dict[str, Any]], budget: EvidenceBudget) -> None:
    """Reject circular identity data, using the evaluator's actual observations.

    Unknown provenance does not prove identity independence. Native value states
    remain unchanged, including blanks that independently violate a rule.
    """
    additions, compared = _identity_source_fields(original, refined, entity_name)
    if not additions or not compared:
        return
    protected: set[tuple[str, str, str]] = set()
    known: list[bool] = []
    readers: dict[tuple[str, str], TableReader] = {}
    for record in records:
        complete = True
        for field in compared:
            sources, observed = _field_sources(record, field, documents, budget, readers)
            protected.update(sources)
            complete = complete and observed
        known.append(complete)
    for record, independent_basis in zip(records, known, strict=True):
        for field in additions:
            sources, observed = _field_sources(record, field, documents, budget, readers)
            if sources & protected:
                raise ValueError("Refinement identity uses original data under comparison")
            if not observed or not independent_basis:
                record["identity_unresolved_fields"] = sorted(set(record.get("identity_unresolved_fields", [])) | {field})
