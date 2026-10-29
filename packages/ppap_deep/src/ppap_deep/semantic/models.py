"""Validated plans, execution artifacts and typed source locations."""

from __future__ import annotations

from string import Formatter
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExcelCell(Artifact):
    kind: Literal["xlsx_cell"]
    sheet: str = Field(min_length=1)
    cell: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


class ExcelRange(Artifact):
    kind: Literal["xlsx_range"]
    sheet: str = Field(min_length=1)
    start: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
    end: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


class WordParagraph(Artifact):
    kind: Literal["docx_paragraph"]
    paragraph: int = Field(ge=1, strict=True)


class WordCell(Artifact):
    kind: Literal["docx_cell"]
    table: int = Field(ge=1, strict=True)
    row: int = Field(ge=1, strict=True)
    column: int = Field(ge=1, strict=True)


class PdfBlock(Artifact):
    kind: Literal["pdf_block"]
    page: int = Field(ge=1, strict=True)
    block: int = Field(ge=1, strict=True)


class PdfCell(Artifact):
    kind: Literal["pdf_cell"]
    page: int = Field(ge=1, strict=True)
    row: int = Field(ge=1, strict=True)
    column: int = Field(ge=1, strict=True)


class PublishedClause(Artifact):
    """An effective published rule clause, never a coordinate in the original file."""

    kind: Literal["published_clause"]
    clause_id: str = Field(min_length=1, max_length=256)


Locator = Annotated[ExcelCell | ExcelRange | WordParagraph | WordCell | PdfBlock | PdfCell | PublishedClause, Field(discriminator="kind")]
Scalar: TypeAlias = str | int | float | bool | None
Diagnostic: TypeAlias = Literal["parser_unknown", "unresolved_relation", "visual_only_evidence", "budget_exhausted", "unsupported_structure", "rule_ambiguity"]


class Predicate(Artifact):
    field: str
    op: Literal["not_empty", "empty", "eq", "ne", "in", "gt", "ge", "lt", "le"]
    value: Scalar = None
    values: list[Scalar] = Field(default_factory=list, max_length=256)


Normalization = Literal["exact", "whitespace", "casefold_whitespace"]


class Key(Artifact):
    fields: list[str] = Field(min_length=1, max_length=64)
    separator: str | None = Field(default=None, max_length=128, description="Literal text between field values. No placeholders; use template for formatting.")
    template: str | None = Field(default=None, max_length=1024, description="Explicit field substitution, such as {code} {name}. Mutually exclusive with separator.")
    normalization: Normalization = "exact"

    @model_validator(mode="after")
    def projection(self) -> Key:
        if any(not field.strip() for field in self.fields) or len(set(self.fields)) != len(self.fields):
            raise ValueError("A key needs distinct named fields")
        if self.separator is not None and self.template is not None:
            raise PydanticCustomError("key_projection_conflict", "Choose a joined key or a formatted key")
        if self.separator is not None and any(char in self.separator for char in "{}"):
            raise PydanticCustomError("key_separator_has_template", "A key separator is literal text; field substitutions belong in template")
        if self.template is not None:
            names = []
            for _, name, spec, conversion in Formatter().parse(self.template):
                if name is not None:
                    if name not in self.fields or any(char in name for char in ".[]") or spec or conversion:
                        raise ValueError("Key templates only substitute declared field names")
                    names.append(name)
            if set(names) != set(self.fields):
                raise ValueError("Key template must use every declared field")
        return self


class ValueComparison(Artifact):
    source_field: str = Field(min_length=1)
    target_field: str = Field(min_length=1)
    mode: Literal["exact", "semantic"]
    normalization: Normalization = "exact"


class ValueTest(Predicate):
    """Test the native value in the current row, not column existence."""

    kind: Literal["value_test"]


class ColumnExists(Artifact):
    """Schema fact only. An existing column does not imply any cell is filled."""

    kind: Literal["column_exists"]
    input: str = Field(min_length=1)
    field: str = Field(min_length=1)


class CollectionExists(Artifact):
    kind: Literal["collection_exists"]
    input: str = Field(min_length=1)


class All(Artifact):
    kind: Literal["all"]


class AnyOf(Artifact):
    kind: Literal["any"]


class Not(Artifact):
    kind: Literal["not"]


# The three grammars share the evaluator's boolean operations. Separate child
# types expose the same boundary to the compiler's JSON schema and to replay;
# a post-parse validator alone would still advertise invalid combinations.
class SchemaAll(All):
    items: list["SchemaExpr"] = Field(min_length=1, max_length=32)


class SchemaAny(AnyOf):
    items: list["SchemaExpr"] = Field(min_length=1, max_length=32)


class SchemaNot(Not):
    item: "SchemaExpr"


class LocalAll(All):
    items: list["LocalExpr"] = Field(min_length=1, max_length=32)


class LocalAny(AnyOf):
    items: list["LocalExpr"] = Field(min_length=1, max_length=32)


class LocalNot(Not):
    item: "LocalExpr"


class RequireAll(All):
    items: list["RowExpr"] = Field(min_length=1, max_length=32)


class RequireAny(AnyOf):
    items: list["RowExpr"] = Field(min_length=1, max_length=32)


class RequireNot(Not):
    item: "RowExpr"


class Judge(Artifact):
    kind: Literal["judge"]
    question: str = Field(min_length=1, max_length=4096)
    fields: list[str] = Field(default_factory=list, max_length=64, description="Required native fields in row context; search hints only in document context.")
    inputs: list[str] = Field(default_factory=list, max_length=16)


class Related(Artifact):
    kind: Literal["related"]
    target: str = Field(min_length=1)
    relationship_id: str | None = Field(default=None, min_length=1, description="Exact ontology relationship used by this expression; required when the plan declares ontology.")
    source_key: Key
    target_key: Key
    match: Literal["any", "all", "one_to_one"]
    key_match: Literal["exact", "semantic", "evidence"] = "exact"
    comparisons: list[ValueComparison] = Field(default_factory=list, max_length=32)
    target_when: "LocalExpr | None" = Field(default=None, description="Current target-row membership condition. Use value_test or a local judge, never column existence.")

    @model_validator(mode="after")
    def equal_key_width(self) -> Related:
        def width(key: Key) -> int:
            return 1 if key.separator is not None or key.template is not None else len(key.fields)

        if width(self.source_key) != width(self.target_key):
            raise PydanticCustomError("key_width_mismatch", "Projected source and target keys must have equal width")
        pairs = [(item.source_field, item.target_field) for item in self.comparisons]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Duplicate value comparison")
        return self


SchemaExpr = Annotated[ColumnExists | CollectionExists | SchemaAll | SchemaAny | SchemaNot | Judge, Field(discriminator="kind")]
LocalExpr = Annotated[ValueTest | LocalAll | LocalAny | LocalNot | Judge, Field(discriminator="kind")]
RowExpr = Annotated[ValueTest | RequireAll | RequireAny | RequireNot | Judge | Related, Field(discriminator="kind")]
Expr: TypeAlias = SchemaExpr | LocalExpr | RowExpr
for _node in (SchemaAll, SchemaAny, SchemaNot, LocalAll, LocalAny, LocalNot, RequireAll, RequireAny, RequireNot, Related):
    _node.model_rebuild()


class DocumentProgram(Artifact):
    kind: Literal["document"]
    precondition: SchemaExpr | None = None
    require: SchemaExpr


class RecordsProgram(Artifact):
    kind: Literal["records"]
    source: str = Field(min_length=1)
    precondition: SchemaExpr | None = Field(default=None, description="Required document/schema facts. False means NG; true does not prove row values.")
    when: LocalExpr | None = Field(default=None, description="Only an explicit applicability condition on the current row. False excludes it; do not move failed requirements here.")
    require: RowExpr = Field(description="Required current-row values and directed relations. Column/collection existence belongs in precondition.")
    quantifier: Literal["forall", "exists"]


RuleProgram = Annotated[DocumentProgram | RecordsProgram, Field(discriminator="kind")]


class PlannedRule(Artifact):
    id: str
    source_locator: Locator
    program: RuleProgram
    # Required even for UNABLE so old cached plans cannot silently inherit a
    # new empty-scope policy. PASS/NA require an explicit source-based basis.
    empty_scope_status: Literal["UNABLE", "NA", "PASS"]
    empty_scope_basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def explained_empty_scope(self) -> PlannedRule:
        if not self.empty_scope_basis.strip():
            raise ValueError("Empty-scope policy needs an explanation")
        nodes = 0

        def visit(node: Expr, depth: int) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > 256 or depth > 16:
                raise ValueError("Rule program exceeds bounded expression limits")
            if isinstance(node, (All, AnyOf)):
                for item in node.items:
                    visit(item, depth + 1)
            elif isinstance(node, Not):
                visit(node.item, depth + 1)
            elif isinstance(node, Related) and node.target_when is not None:
                visit(node.target_when, depth + 1)

        program = self.program
        if program.precondition is not None:
            visit(program.precondition, 1)
        if isinstance(program, RecordsProgram) and program.when is not None:
            visit(program.when, 1)
        visit(program.require, 1)
        return self


class EntityConcept(Artifact):
    id: str = Field(min_length=1, max_length=256)
    grain: str = Field(min_length=1, max_length=1024)
    attributes: dict[str, Literal["string", "number", "boolean"]] = Field(max_length=64)
    identity: list[str] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def declared_identity(self) -> "EntityConcept":
        if not set(self.identity) <= self.attributes.keys() or len(set(self.identity)) != len(self.identity):
            raise ValueError("Identity must use distinct declared attributes")
        return self


class RelationshipConcept(Artifact):
    id: str = Field(min_length=1, max_length=256)
    kind: Literal["same_instance", "association"]
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    meaning: str = Field(min_length=1, max_length=1024)


class Ontology(Artifact):
    entities: list[EntityConcept] = Field(default_factory=list, max_length=64)
    relationships: list[RelationshipConcept] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def endpoints(self) -> "Ontology":
        entities = {entity.id for entity in self.entities}
        if len(entities) != len(self.entities) or len({relation.id for relation in self.relationships}) != len(self.relationships):
            raise ValueError("Duplicate ontology concept")
        if any(relation.source not in entities or relation.target not in entities for relation in self.relationships):
            raise ValueError("Relationship endpoint is not a declared entity")
        return self


class RulePlan(Artifact):
    source_sha256: str
    plan_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    rules: list[PlannedRule] = Field(min_length=1)
    ontology: Ontology = Field(default_factory=Ontology)
    # Copied from the frozen effective rule by the host, never inferred from chat.
    appendix: list[dict[str, Any]] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def ontology_programs(self) -> "RulePlan":
        entities = {entity.id: entity for entity in self.ontology.entities}
        if not entities:
            return self  # Existing row programs use their original physical grain.

        def fields(entity: str | None, names: list[str]) -> None:
            if entity not in entities or not set(names) <= entities[entity].attributes.keys():
                raise ValueError("Program attribute outside declared ontology")

        def visit(node: Expr | None, current: str | None) -> None:
            if node is None:
                return
            if isinstance(node, ValueTest):
                fields(current, [node.field])
            elif isinstance(node, (ColumnExists, CollectionExists)):
                fields(node.input, [node.field] if isinstance(node, ColumnExists) else [])
            elif isinstance(node, (All, AnyOf, Not)):
                for child in node.items if isinstance(node, (All, AnyOf)) else [node.item]:
                    visit(child, current)
            elif isinstance(node, Judge):
                if current is not None:
                    fields(current, node.fields)
                for entity in node.inputs:
                    fields(entity, [])
            elif isinstance(node, Related):
                fields(current, node.source_key.fields + [comparison.source_field for comparison in node.comparisons])
                fields(node.target, node.target_key.fields + [comparison.target_field for comparison in node.comparisons])
                relation = next((relation for relation in self.ontology.relationships if relation.id == node.relationship_id), None)
                if relation is None:
                    raise ValueError("Program relationship must reference a declared ontology relationship")
                if relation.source != current or relation.target != node.target:
                    raise ValueError("Program relationship direction outside declared ontology")
                if relation.kind == "same_instance" and (entities[relation.source].identity != node.source_key.fields or entities[node.target].identity != node.target_key.fields):
                    raise ValueError("Same-instance relationship keys must equal the ordered entity identity")
                if relation.kind == "same_instance" and any(key.separator is not None or key.template is not None for key in (node.source_key, node.target_key)):
                    raise ValueError("Same-instance identity uses component tuples, not a string projection")
                visit(node.target_when, node.target)

        for rule in self.rules:
            program = rule.program
            source = program.source if isinstance(program, RecordsProgram) else None
            if isinstance(program, RecordsProgram):
                fields(program.source, [])
                visit(program.when, program.source)
            visit(program.precondition, None)
            visit(program.require, source)
        return self


class CompiledOntology(Ontology):
    """A new compiled plan must state its object grain and identity."""

    entities: list[EntityConcept] = Field(min_length=1, max_length=64)


class CompiledRulePlan(RulePlan):
    # RulePlan remains the common replay model for hand-built unit programs.
    # The real compiler/cache boundary has no implicit physical-row ontology.
    ontology: CompiledOntology


class Citation(Artifact):
    document_id: str
    locator: Locator | None = None


class Check(Artifact):
    audit_id: str
    rule_id: str
    status: Literal["PASS", "NG", "UNABLE", "NA"]
    reason: str
    total: int = Field(ge=0, strict=True)
    checked: int = Field(ge=0, strict=True)
    unknown: int = Field(ge=0, strict=True)
    evidence: list[Citation] = Field(default_factory=list, max_length=64)
    diagnostic: Diagnostic | None = None


class AuditReport(Artifact):
    # A bundle with only failed rule plans has no business checks. The bundle
    # validator still requires every frozen rule to have a plan or failure.
    checks: list[Check] = Field(max_length=16384)
