"""Machine-readable contract for one clause formalization.

OWL text and CLIF text are the authority. The parse tree exists only to check
coverage and to render the same specification.
"""

from __future__ import annotations

import re
from typing import Literal

from audit.formal_digest import formal_digest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from product.formalization.cnl import RENDERER_VERSION, coverage, verbalize
from product.formalization.parse import BUILTIN_PREFIXES, FormalSyntaxError, parse_clif, parse_owl, symbols_of_clif, symbols_of_owl

MAX_FORMAL_CHARS = 65536
MAX_LABEL = 200
SPAN = re.compile(r"^(\d+):(\d+)$")
KINDS = {
    "universal",
    "existential",
    "unique",
    "conjunction",
    "disjunction",
    "implication",
    "comparison",
    "cardinality",
    "direction",
    "exception",
    "empty_policy",
    "unknown_policy",
    "missing_policy",
    "duplicate_policy",
    "threshold",
    "field_presence",
    "column_presence",
    "non_empty",
    "scope",
    "normalization",
    "subclass",
    "property_restriction",
}
ENTITY_KINDS = {"class", "objectProperty", "dataProperty", "individual", "datatype", "function", "predicate"}
ROLES = {"none", "empty", "missing", "unknown", "normalize", "scope", "column", "duplicate"}


class FormalizationError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    counterexample: str = Field(min_length=1, max_length=500)


class Unresolved(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)
    choices: list[Choice] | None = None


class Obligation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    source_span: str
    semantic_kind: str
    formal_ref: str = Field(min_length=1, max_length=120)

    @field_validator("semantic_kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in KINDS:
            raise ValueError("unknown_semantic_kind")
        return value

    @field_validator("source_span")
    @classmethod
    def _span(cls, value: str) -> str:
        match = SPAN.fullmatch(value)
        if match is None or int(match.group(1)) > int(match.group(2)):
            raise ValueError("invalid_source_span")
        return value


class Assumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iri: str = Field(min_length=1, max_length=500)
    kind: Literal["class", "objectProperty", "dataProperty", "individual", "datatype", "function", "predicate"]
    label: str = Field(min_length=1, max_length=MAX_LABEL)
    role: Literal["none", "empty", "missing", "unknown", "normalize", "scope", "column", "duplicate"] = "none"


class Vocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefixes: dict[str, str]
    entities: list[Entity] = Field(max_length=200)


class FormalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal["1"]
    formal_profile: Literal["owl2-dl-v1", "owl2-plus-clif-v1"]
    owl_content: str = Field(min_length=1, max_length=MAX_FORMAL_CHARS)
    clif_content: str | None = Field(default=None, max_length=MAX_FORMAL_CHARS)
    vocabulary: Vocabulary
    source_rule_version_id: str = Field(min_length=1, max_length=80)
    clause_id: str = Field(min_length=1, max_length=200)
    obligations: list[Obligation] = Field(min_length=1, max_length=100)
    unresolved: list[Unresolved] = Field(default_factory=list, max_length=20)
    completeness: Literal["complete", "partial"]
    assumptions: list[Assumption] = Field(default_factory=list, max_length=20)


def check_formal_spec(payload: dict, *, clause_text: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise FormalizationError("invalid_formalization", "规范必须是对象")
    _reject_controls(payload)
    try:
        spec = FormalSpec.model_validate(payload)
    except Exception as exc:
        raise FormalizationError("invalid_formalization", _one_line(exc)) from None
    if spec.formal_profile == "owl2-dl-v1" and spec.clif_content is not None:
        raise FormalizationError("invalid_formalization", "owl2-dl-v1 不能附带 CLIF")
    if spec.formal_profile == "owl2-plus-clif-v1" and not spec.clif_content:
        raise FormalizationError("invalid_formalization", "owl2-plus-clif-v1 需要 CLIF")
    try:
        owl = parse_owl(spec.owl_content)
    except FormalSyntaxError as exc:
        raise FormalizationError("invalid_syntax", f"OWL: {exc}") from None
    clif: tuple[dict, ...] = ()
    if spec.clif_content:
        try:
            clif = parse_clif(spec.clif_content)
        except FormalSyntaxError as exc:
            raise FormalizationError("invalid_syntax", f"CLIF: {exc}") from None
    prefixes = {**BUILTIN_PREFIXES, **spec.vocabulary.prefixes, **owl.prefixes}
    nodes = _nodes(owl, clif, prefixes)
    roles = {entity.iri: entity.role for entity in spec.vocabulary.entities}
    _reject_visible_markup(spec)
    gaps = _gaps(spec, owl, clif, prefixes, nodes, roles, clause_text)
    completeness = "partial" if gaps or spec.unresolved else "complete"
    public = spec.model_dump(mode="json")
    public["completeness"] = completeness
    formal = formal_digest(public)
    checked = {**public, "nodes": nodes, "formal_digest": formal, "completeness": completeness, "gaps": gaps}
    rendered = _rendered(checked)
    if rendered["unsupported"] and completeness == "complete":
        completeness = "partial"
        public["completeness"] = completeness
        checked["completeness"] = completeness
        rendered = _rendered(checked)
    return {
        "spec": public,
        "nodes": nodes,
        "cnl_text": rendered["cnl"],
        "cnl_renderer_version": RENDERER_VERSION,
        "formal_digest": formal,
        "display_digest": rendered["display_digest"],
        "completeness": completeness,
        "unsupported": [*gaps, *rendered["unsupported"]],
    }


def _nodes(owl, clif: tuple[dict, ...], prefixes: dict[str, str]) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    for axiom in owl.axioms:
        if axiom["id"] in nodes:
            raise FormalizationError("invalid_formalization", "表达式标识重复")
        nodes[axiom["id"]] = {"language": "owl", "expr": axiom["expr"], "kinds": set(axiom["kinds"])}
    for sentence in clif:
        if sentence["id"] in nodes:
            raise FormalizationError("invalid_formalization", "表达式标识重复")
        nodes[sentence["id"]] = {"language": "clif", "expr": _expand(sentence["expr"], prefixes), "kinds": set(sentence["kinds"])}
    return nodes


def _gaps(spec: FormalSpec, owl, clif, prefixes, nodes, roles, clause_text: str | None) -> list[dict]:
    gaps = []
    declared = {entity.iri for entity in spec.vocabulary.entities}
    if len(declared) != len(spec.vocabulary.entities):
        raise FormalizationError("invalid_formalization", "词汇 IRI 重复")
    for entity in spec.vocabulary.entities:
        if not entity.iri.startswith("http://") and not entity.iri.startswith("https://"):
            raise FormalizationError("invalid_formalization", "词汇 IRI 必须是绝对地址")
    used = symbols_of_owl(owl) | _expanded_symbols(clif, prefixes)
    missing = sorted(iri for iri in used if iri not in declared)
    if missing:
        raise FormalizationError("invalid_formalization", "未声明符号 " + missing[0])
    ids = [item.id for item in spec.obligations]
    if len(ids) != len(set(ids)):
        raise FormalizationError("invalid_formalization", "义务标识重复")
    cited = [(item.formal_ref, item.semantic_kind) for item in spec.obligations]
    if len(cited) != len(set(cited)):
        raise FormalizationError("invalid_formalization", "义务引用重复")
    for item in spec.obligations:
        node = nodes.get(item.formal_ref)
        if node is None or item.semantic_kind not in coverage(node, roles):
            gaps.append({"id": item.id, "formal_ref": item.formal_ref, "reason": "uncovered_obligation", "semantic_kind": item.semantic_kind, "source_span": item.source_span})
        if clause_text is not None:
            start, end = (int(part) for part in item.source_span.split(":"))
            if end > len(clause_text):
                raise FormalizationError("invalid_formalization", "原文位置超出条款")
    if spec.formal_profile == "owl2-dl-v1" and any(nodes[item.formal_ref]["language"] != "owl" for item in spec.obligations if item.formal_ref in nodes):
        raise FormalizationError("invalid_formalization", "owl2-dl-v1 的义务必须引用 OWL 公理")
    return gaps


def _expanded_symbols(clif, prefixes: dict[str, str]) -> set[str]:
    raw = symbols_of_clif(tuple({"id": item["id"], "expr": _expand(item["expr"], prefixes)} for item in clif), prefixes)
    return raw


def _expand(expr, prefixes: dict[str, str]):
    if isinstance(expr, tuple):
        if expr and expr[0] in {"atom", "fn"}:
            return (
                expr[0],
                _expand_name(expr[1], prefixes),
            ) + tuple(_expand(part, prefixes) for part in expr[2:])
        return tuple(_expand(part, prefixes) if isinstance(part, tuple) else part for part in expr)
    return expr


def _expand_name(name: str, prefixes: dict[str, str]) -> str:
    if name.startswith("http://") or name.startswith("https://"):
        return name
    if ":" in name:
        prefix, local = name.split(":", 1)
        if prefix not in prefixes:
            raise FormalizationError("invalid_formalization", f"未声明前缀 {prefix}")
        return prefixes[prefix] + local
    return name


def _rendered(checked: dict) -> dict:
    rendered = verbalize(checked)
    if "<" in rendered["cnl"] or ">" in rendered["cnl"]:
        raise FormalizationError("invalid_formalization", "可见文本不能包含标记")
    return rendered


def _reject_visible_markup(spec: FormalSpec) -> None:
    visible = [entity.label for entity in spec.vocabulary.entities]
    visible.extend(item.text for item in spec.assumptions)
    for item in spec.unresolved:
        visible.append(item.reason)
        for choice in item.choices or []:
            visible.extend((choice.summary, choice.counterexample))
    if any("<" in text or ">" in text for text in visible):
        raise FormalizationError("invalid_formalization", "可见文本不能包含标记")


def _reject_controls(value) -> None:
    if isinstance(value, str):
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise FormalizationError("invalid_formalization", "文本含有控制字符")
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_controls(item)
    elif isinstance(value, list):
        for item in value:
            _reject_controls(item)


def _one_line(exc: Exception) -> str:
    text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return text[:300]
