"""Just enough OWL 2 Functional Syntax to rewrite clause ontologies: parse, expand names, print back.

A document is read into `Node(head, args)` trees whose leaves are strings: full IRIs as `<...>`,
literals as written (`"..."`, with any `^^type` expanded), keywords and numbers as written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<comment>#[^\n]*)"
    r'|(?P<literal>"(?:[^"\\]|\\.)*"(?:\^\^(?:<[^>]*>|[^\s()]+)|@[\w-]+)?)'
    r"|(?P<iri><[^>]*>)|(?P<open>\()|(?P<close>\))|(?P<name>[^\s()\"<>]+)"
)
STANDARD = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}
THING = "<http://www.w3.org/2002/07/owl#Thing>"
NOTHING = "<http://www.w3.org/2002/07/owl#Nothing>"


@dataclass
class Node:
    head: str
    args: list = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.head}({' '.join(str(arg) for arg in self.args)})"


@dataclass
class Ontology:
    iri: str
    annotations: list[Node]
    axioms: list[Node]


def local(iri: str) -> str:
    return re.split(r"[#/]", iri.strip("<>"))[-1]


def literal_text(token: str) -> str:
    body = token[1 : token.rindex('"')]
    return re.sub(r"\\(.)", r"\1", body)


def literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse(path: Path) -> Ontology:
    tokens: list[tuple[str, str]] = []
    for match in TOKEN.finditer(path.read_text(encoding="utf-8")):
        kind = match.lastgroup
        if kind not in ("space", "comment"):
            tokens.append((kind, match.group()))
    prefixes = dict(STANDARD)
    position = 0

    def expand(name: str) -> str:
        prefix, colon, rest = name.partition(":")
        if not colon or prefix not in prefixes:
            return name
        return f"<{prefixes[prefix]}{rest}>"

    def leaf(kind: str, text: str) -> str:
        if kind == "name":
            return expand(text)
        if kind == "literal" and "^^" in text and not text.endswith(">"):
            body, _, datatype = text.rpartition("^^")
            return f"{body}^^{expand(datatype)}"
        return text

    def node() -> Node:
        nonlocal position
        head = tokens[position][1]
        if tokens[position + 1][0] != "open":
            raise SyntaxError(f"{path}: {head} 后面缺少 (")
        position += 2
        args: list = []
        while True:
            if position >= len(tokens):
                raise SyntaxError(f"{path}: {head}(...) 没有闭合")
            kind, text = tokens[position]
            if kind == "close":
                position += 1
                return Node(head, args)
            if kind == "name" and position + 1 < len(tokens) and tokens[position + 1][0] == "open":
                args.append(node())
            else:
                args.append(leaf(kind, text))
                position += 1

    ontology = None
    while position < len(tokens):
        kind, text = tokens[position]
        if text == "Prefix":
            declared = node()
            prefixes[declared.args[0].rstrip("=").rstrip(":")] = declared.args[1].strip("<>")
        elif text == "Ontology":
            ontology = node()
        else:
            raise SyntaxError(f"{path}: 无法识别的顶层内容 {text}")
    if ontology is None:
        raise SyntaxError(f"{path}: 没有 Ontology(...)")
    iri = next((arg for arg in ontology.args if isinstance(arg, str)), "<urn:anonymous>")
    nodes = [arg for arg in ontology.args if isinstance(arg, Node)]
    return Ontology(iri, [n for n in nodes if n.head == "Annotation"], [n for n in nodes if n.head not in ("Annotation", "Import")])


def walk(item):
    yield item
    if isinstance(item, Node):
        for arg in item.args:
            yield from walk(arg)


def rename(item, mapping: dict[str, str]):
    if isinstance(item, Node):
        return Node(item.head, [rename(arg, mapping) for arg in item.args])
    return mapping.get(item, item)


def write(path: Path, iri: str, axioms: list[Node]) -> None:
    lines = [f"Prefix({name}:=<{base}>)" for name, base in STANDARD.items()]
    lines.append(f"Ontology({iri}")
    lines += [f"  {axiom}" for axiom in axioms]
    lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
