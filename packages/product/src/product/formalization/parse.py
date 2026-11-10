"""OWL 2 Functional Syntax and CLIF parsers for one formalization profile.

The trees are render views. They are not a second rule language and they are not executed.
"""

from __future__ import annotations

from dataclasses import dataclass

AXIOM_NAMESPACE = "https://ppap.example/formal/v1#"
AXIOM_ID = AXIOM_NAMESPACE + "axiomId"
BUILTIN_PREFIXES = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


class FormalSyntaxError(ValueError):
    """The text is not in the accepted OWL or CLIF grammar."""


@dataclass(frozen=True)
class OwlDocument:
    prefixes: dict[str, str]
    declarations: tuple[tuple[str, str], ...]
    axioms: tuple[dict, ...]


def parse_owl(text: str) -> OwlDocument:
    parser = _OwlParser(text or "")
    return parser.parse()


def parse_clif(text: str) -> tuple[dict, ...]:
    parser = _ClifParser(text or "")
    return parser.parse()


class _Lexer:
    def __init__(self, text: str):
        self.text = text
        self.n = len(text)
        self.i = 0

    def _skip(self) -> None:
        while self.i < self.n:
            char = self.text[self.i]
            if char.isspace():
                self.i += 1
                continue
            if char == "#" and (self.i == 0 or self.text[self.i - 1] in "\n\r"):
                self.i = self.text.find("\n", self.i)
                if self.i < 0:
                    self.i = self.n
                continue
            if self.text.startswith("/*", self.i):
                end = self.text.find("*/", self.i + 2)
                if end < 0:
                    raise FormalSyntaxError("未闭合的注释")
                self.i = end + 2
                continue
            return

    def peek(self) -> str | None:
        self._skip()
        if self.i >= self.n:
            return None
        char = self.text[self.i]
        if char in "()":
            return char
        if self.text.startswith(":=", self.i):
            return ":="
        if self.text.startswith("^^", self.i):
            return "^^"
        if self.text.startswith("<=", self.i) or self.text.startswith(">=", self.i):
            return self.text[self.i : self.i + 2]
        if char == ">":
            return ">"
        if char == "<":
            nxt = self.text[self.i + 1] if self.i + 1 < self.n else ""
            if nxt and not nxt.isspace() and nxt != "(":
                return self._iri()
            return "<"
        if char == '"':
            return self._string()
        return self._word()

    def pop(self) -> str:
        token = self.peek()
        if token is None:
            raise FormalSyntaxError("形式文本在此处结束")
        if token in {"(", ")", ":=", "^^", "<=", ">=", "<", ">"}:
            self.i += len(token)
            return token
        if token.startswith('"') or token.startswith("<"):
            self._skip()
            if token.startswith('"'):
                self._string(consume=True)
            else:
                self._iri(consume=True)
            return token
        self._skip()
        self._word(consume=True)
        return token

    def expect(self, token: str) -> None:
        found = self.pop()
        if found != token:
            raise FormalSyntaxError(f"需要 {token}，实际是 {found}")

    def _string(self, *, consume: bool = False) -> str:
        if self.text[self.i] != '"':
            raise FormalSyntaxError("需要字符串")
        index = self.i + 1
        chars: list[str] = []
        while index < self.n:
            char = self.text[index]
            if char == "\\":
                if index + 1 >= self.n:
                    raise FormalSyntaxError("未闭合的字符串")
                escape = self.text[index + 1]
                chars.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(escape, escape))
                index += 2
                continue
            if char == '"':
                if consume:
                    self.i = index + 1
                return '"' + "".join(chars) + '"'
            if ord(char) < 32:
                raise FormalSyntaxError("字符串含有控制字符")
            chars.append(char)
            index += 1
        raise FormalSyntaxError("未闭合的字符串")

    def _iri(self, *, consume: bool = False) -> str:
        end = self.text.find(">", self.i + 1)
        if end < 0 or any(ord(char) < 32 for char in self.text[self.i + 1 : end]):
            raise FormalSyntaxError("IRI 不完整")
        token = self.text[self.i : end + 1]
        if consume:
            self.i = end + 1
        return token

    def _word(self, *, consume: bool = False) -> str:
        index = self.i
        while index < self.n and not self.text[index].isspace() and self.text[index] not in "()<>":
            if self.text.startswith(":=", index) or self.text.startswith("^^", index):
                break
            index += 1
        if index == self.i:
            raise FormalSyntaxError("无法识别的符号")
        token = self.text[self.i : index]
        if consume:
            self.i = index
        return token


class _OwlParser:
    def __init__(self, text: str):
        self.lexer = _Lexer(text)
        self.prefixes = dict(BUILTIN_PREFIXES)
        self.declarations: list[tuple[str, str]] = []
        self.axioms: list[dict] = []

    def parse(self) -> OwlDocument:
        saw_ontology = False
        while self.lexer.peek() is not None:
            word = self.lexer.pop()
            if word == "Prefix":
                self._prefix()
            elif word == "Ontology":
                if saw_ontology:
                    raise FormalSyntaxError("只能有一个 Ontology")
                saw_ontology = True
                self._ontology()
            else:
                raise FormalSyntaxError(f"不能识别的 OWL 构造 {word}")
        if not saw_ontology:
            raise FormalSyntaxError("缺少 Ontology")
        return OwlDocument(dict(self.prefixes), tuple(self.declarations), tuple(self.axioms))

    def _prefix(self) -> None:
        self.lexer.expect("(")
        name = self.lexer.pop()
        if not name or ":" in name or name in {"Prefix", "Ontology"}:
            raise FormalSyntaxError("前缀名称不合法")
        self.lexer.expect(":=")
        iri = self._full_iri(self.lexer.pop())
        self.lexer.expect(")")
        self.prefixes[name] = iri

    def _ontology(self) -> None:
        self.lexer.expect("(")
        while self.lexer.peek() not in {None, ")"}:
            word = self.lexer.pop()
            if word == "Declaration":
                self._declaration()
            elif word == "Annotation":
                self._annotation()
            else:
                self.axioms.append(self._axiom(word))
        self.lexer.expect(")")

    def _declaration(self) -> None:
        self.lexer.expect("(")
        kind = self.lexer.pop()
        if kind not in {"Class", "ObjectProperty", "DataProperty", "NamedIndividual", "Datatype", "AnnotationProperty"}:
            raise FormalSyntaxError(f"不能识别的声明 {kind}")
        self.lexer.expect("(")
        iri = self._iri_token(self.lexer.pop())
        self.lexer.expect(")")
        self.lexer.expect(")")
        self.declarations.append((kind, iri))

    def _annotation(self) -> tuple[str, str]:
        self.lexer.expect("(")
        iri = self._iri_token(self.lexer.pop())
        value = self.lexer.pop()
        self.lexer.expect(")")
        if value.startswith('"'):
            value = _unquote(value)
        else:
            value = self._iri_token(value)
        return iri, value

    def _axiom(self, word: str) -> dict:
        self.lexer.expect("(")
        annotations = []
        while self.lexer.peek() == "Annotation":
            self.lexer.pop()
            annotations.append(self._annotation())
        axiom_id = _axiom_id(annotations, len(self.axioms) + 1)
        if word == "SubClassOf":
            sub = self._class()
            sup = self._class()
            expr = ("subclass", sub, sup)
        elif word == "EquivalentClasses":
            expr = ("equivalent", self._class_list())
        elif word == "DisjointClasses":
            expr = ("disjoint", self._class_list())
        elif word in {"ObjectPropertyDomain", "ObjectPropertyRange", "DataPropertyDomain"}:
            prop = self._iri_token(self.lexer.pop())
            expr = (word[0].lower() + word[1:], prop, self._class() if word != "DataPropertyDomain" else self._class())
        elif word == "DataPropertyRange":
            expr = ("dataPropertyRange", self._iri_token(self.lexer.pop()), self._data_range())
        elif word in {"FunctionalObjectProperty", "InverseFunctionalObjectProperty", "FunctionalDataProperty"}:
            expr = (_camel(word), self._iri_token(self.lexer.pop()))
        elif word == "SubObjectPropertyOf":
            expr = ("subProperty", self._iri_token(self.lexer.pop()), self._iri_token(self.lexer.pop()))
        elif word == "InverseObjectProperties":
            expr = ("inverse", self._iri_token(self.lexer.pop()), self._iri_token(self.lexer.pop()))
        elif word == "ClassAssertion":
            expr = ("classAssertion", self._class(), self._iri_token(self.lexer.pop()))
        elif word in {"ObjectPropertyAssertion", "DataPropertyAssertion"}:
            prop = self._iri_token(self.lexer.pop())
            left = self._iri_token(self.lexer.pop())
            right = self._individual_or_literal()
            expr = ("assertion", prop, left, right)
        else:
            raise FormalSyntaxError(f"不在 OWL 白名单中的构造 {word}")
        self.lexer.expect(")")
        return {"id": axiom_id, "op": expr[0], "expr": expr, "kinds": _owl_kinds(expr)}

    def _class_list(self) -> tuple:
        items = [self._class()]
        while self.lexer.peek() not in {None, ")"}:
            items.append(self._class())
        if len(items) < 2:
            raise FormalSyntaxError("类表达式不足")
        return tuple(items)

    def _class(self):
        token = self.lexer.peek()
        if token in {
            "ObjectIntersectionOf",
            "ObjectUnionOf",
            "ObjectComplementOf",
            "ObjectSomeValuesFrom",
            "ObjectAllValuesFrom",
            "ObjectExactCardinality",
            "ObjectMinCardinality",
            "ObjectMaxCardinality",
            "ObjectHasValue",
            "DataSomeValuesFrom",
            "DataAllValuesFrom",
            "DataExactCardinality",
            "DataMinCardinality",
            "DataMaxCardinality",
            "DataHasValue",
        }:
            word = self.lexer.pop()
            self.lexer.expect("(")
            expr = self._restriction(word)
            self.lexer.expect(")")
            return expr
        if token == "(":
            raise FormalSyntaxError("类表达式不能是匿名括号")
        return ("class", self._iri_token(self.lexer.pop()))

    def _restriction(self, word: str):
        if word in {"ObjectIntersectionOf", "ObjectUnionOf"}:
            items = [self._class()]
            while self.lexer.peek() not in {None, ")"}:
                items.append(self._class())
            if len(items) < 2:
                raise FormalSyntaxError("布尔类表达式不足")
            return ("and", *items) if word == "ObjectIntersectionOf" else ("or", *items)
        if word == "ObjectComplementOf":
            return ("not", self._class())
        if word in {"ObjectSomeValuesFrom", "ObjectAllValuesFrom"}:
            prop = self._iri_token(self.lexer.pop())
            return ("some" if word.endswith("SomeValuesFrom") else "only", prop, self._class())
        if word in {"DataSomeValuesFrom", "DataAllValuesFrom"}:
            prop = self._iri_token(self.lexer.pop())
            return ("dataSome" if "Some" in word else "dataOnly", prop, self._data_range())
        if word in {"ObjectExactCardinality", "ObjectMinCardinality", "ObjectMaxCardinality", "DataExactCardinality", "DataMinCardinality", "DataMaxCardinality"}:
            number = self._number()
            prop = self._iri_token(self.lexer.pop())
            filler = None
            if self.lexer.peek() not in {None, ")"}:
                filler = self._data_range() if word.startswith("Data") else self._class()
            name = {
                "ObjectExactCardinality": "exact",
                "ObjectMinCardinality": "min",
                "ObjectMaxCardinality": "max",
                "DataExactCardinality": "dataExact",
                "DataMinCardinality": "dataMin",
                "DataMaxCardinality": "dataMax",
            }[word]
            return (name, number, prop, filler)
        if word == "ObjectHasValue":
            return ("has", self._iri_token(self.lexer.pop()), self._iri_token(self.lexer.pop()))
        if word == "DataHasValue":
            return ("dataHas", self._iri_token(self.lexer.pop()), self._literal())
        raise FormalSyntaxError(f"不在 OWL 白名单中的构造 {word}")

    def _data_range(self):
        token = self.lexer.peek()
        if token == "DataIntersectionOf":
            raise FormalSyntaxError("不在 OWL 白名单中的构造 DataIntersectionOf")
        return ("datatype", self._iri_token(self.lexer.pop()))

    def _literal(self):
        token = self.lexer.pop()
        if not token.startswith('"'):
            if _is_number(token):
                return ("lit", token, self.prefixes["xsd"] + ("decimal" if "." in token else "integer"))
            raise FormalSyntaxError("需要字面量")
        datatype = self.prefixes["xsd"] + "string"
        if self.lexer.peek() == "^^":
            self.lexer.pop()
            datatype = self._iri_token(self.lexer.pop())
        return ("lit", _unquote(token), datatype)

    def _individual_or_literal(self):
        token = self.lexer.peek()
        if token and (token.startswith('"') or _is_number(token)):
            return self._literal()
        return ("ind", self._iri_token(self.lexer.pop()))

    def _number(self) -> int:
        token = self.lexer.pop()
        if not token.isdigit():
            raise FormalSyntaxError("基数必须是非负整数")
        return int(token)

    def _iri_token(self, token: str) -> str:
        if token.startswith("<"):
            return self._full_iri(token)
        if ":" not in token:
            raise FormalSyntaxError(f"需要 IRI：{token}")
        prefix, local = token.split(":", 1)
        if prefix not in self.prefixes:
            raise FormalSyntaxError(f"未声明前缀 {prefix}")
        return self.prefixes[prefix] + local

    def _full_iri(self, token: str) -> str:
        if not (token.startswith("<") and token.endswith(">") and len(token) > 2):
            raise FormalSyntaxError("需要完整 IRI")
        return token[1:-1]


class _ClifParser:
    def __init__(self, text: str):
        self.lexer = _Lexer(text)

    def parse(self) -> tuple[dict, ...]:
        self.lexer.expect("(")
        if self.lexer.pop() != "cl-text":
            raise FormalSyntaxError("CLIF 必须以 cl-text 开始")
        sentences = []
        while self.lexer.peek() not in {None, ")"}:
            self.lexer.expect("(")
            if self.lexer.pop() != "sentence":
                raise FormalSyntaxError("CLIF 句子必须以 sentence 标记")
            name = self.lexer.pop()
            if not name or name.startswith("(") or name.startswith('"'):
                raise FormalSyntaxError("CLIF 句子缺少标识")
            formula = self._formula()
            self.lexer.expect(")")
            sentences.append({"id": name, "expr": formula, "kinds": _clif_kinds(formula)})
        self.lexer.expect(")")
        if self.lexer.peek() is not None:
            raise FormalSyntaxError("CLIF 文本之后还有内容")
        if not sentences:
            raise FormalSyntaxError("CLIF 没有句子")
        ids = [item["id"] for item in sentences]
        if len(ids) != len(set(ids)):
            raise FormalSyntaxError("CLIF 句子标识重复")
        return tuple(sentences)

    def _formula(self):
        self.lexer.expect("(")
        op = self.lexer.pop()
        if op in {"forall", "exists"}:
            self.lexer.expect("(")
            variables = []
            while self.lexer.peek() != ")":
                variables.append(self._variable())
            self.lexer.expect(")")
            if not variables:
                raise FormalSyntaxError("量词没有变量")
            body = self._formula()
            self.lexer.expect(")")
            return (op, tuple(variables), body)
        if op in {"and", "or"}:
            items = []
            while self.lexer.peek() != ")":
                items.append(self._formula())
            self.lexer.expect(")")
            if len(items) < 2:
                raise FormalSyntaxError("联结词缺少子公式")
            return (op, *items)
        if op == "not":
            item = self._formula()
            self.lexer.expect(")")
            return ("not", item)
        if op in {"if", "iff"}:
            left = self._formula()
            right = self._formula()
            self.lexer.expect(")")
            return (op, left, right)
        if op == "=":
            left = self._term()
            right = self._term()
            self.lexer.expect(")")
            return ("eq", left, right)
        if op in {"<", "<=", ">", ">="}:
            left = self._term()
            right = self._term()
            self.lexer.expect(")")
            return ("cmp", op, left, right)
        if op.startswith("(") or op.startswith('"'):
            raise FormalSyntaxError("不能识别的 CLIF 构造")
        terms = []
        while self.lexer.peek() != ")":
            terms.append(self._term())
        self.lexer.expect(")")
        return ("atom", op, tuple(terms))

    def _term(self):
        token = self.lexer.peek()
        if token == "(":
            self.lexer.pop()
            name = self.lexer.pop()
            if name in {"forall", "exists", "and", "or", "not", "if", "iff", "=", "<", "<=", ">", ">="}:
                raise FormalSyntaxError("项的位置出现了公式")
            args = []
            while self.lexer.peek() != ")":
                args.append(self._term())
            self.lexer.expect(")")
            if not args:
                raise FormalSyntaxError("函数缺少参数")
            return ("fn", name, tuple(args))
        token = self.lexer.pop()
        if token.startswith('"') or _is_number(token):
            value = _unquote(token) if token.startswith('"') else token
            if _is_number(token):
                mark = self.lexer.i
                unit = self.lexer.peek()
                if unit and _is_name(unit):
                    self.lexer.pop()
                    if self.lexer.peek() == ")":
                        return ("lit", value, unit)
                    self.lexer.i = mark
            return ("lit", value)
        if not _is_name(token):
            raise FormalSyntaxError("不能识别的项")
        return ("var", token)

    def _variable(self) -> str:
        token = self.lexer.pop()
        if not _is_name(token):
            raise FormalSyntaxError("量词变量不合法")
        return token


def symbols_of_owl(document: OwlDocument) -> set[str]:
    found: set[str] = set()

    def walk(expr) -> None:
        if isinstance(expr, tuple):
            if expr and expr[0] in {"class", "datatype", "ind"}:
                found.add(expr[1])
            elif expr and isinstance(expr[1], str) and expr[1].startswith("http"):
                found.add(expr[1])
            for part in expr:
                walk(part)
        elif isinstance(expr, str) and expr.startswith("http"):
            found.add(expr)

    for _kind, iri in document.declarations:
        found.add(iri)
    for axiom in document.axioms:
        walk(axiom["expr"])
    return {iri for iri in found if not _builtin(iri)}


def symbols_of_clif(sentences: tuple[dict, ...], prefixes: dict[str, str]) -> set[str]:
    found: set[str] = set()

    def expand(name: str) -> str:
        if name.startswith("<") and name.endswith(">"):
            return name[1:-1]
        if ":" in name:
            prefix, local = name.split(":", 1)
            if prefix in prefixes:
                return prefixes[prefix] + local
        return name

    def walk(expr) -> None:
        if not isinstance(expr, tuple):
            return
        if expr[0] in {"atom", "fn"}:
            found.add(expand(expr[1]))
        for part in expr:
            walk(part)

    for sentence in sentences:
        walk(sentence["expr"])
    return {iri for iri in found if not _builtin(iri) and iri.startswith("http")}


def _axiom_id(annotations: list[tuple[str, str]], index: int) -> str:
    found = [value for iri, value in annotations if iri == AXIOM_ID]
    if len(found) > 1:
        raise FormalSyntaxError("一条公理有多个标识")
    if found:
        if not found[0] or any(char.isspace() for char in found[0]):
            raise FormalSyntaxError("公理标识不合法")
        return found[0]
    return f"owl:{index}"


def _owl_kinds(expr: tuple) -> frozenset[str]:
    found: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, tuple) or not node:
            return
        op = node[0]
        if op == "subclass":
            found.add("subclass")
        elif op == "some":
            found.add("existential")
        elif op == "only":
            found.add("universal")
        elif op in {"exact", "dataExact"}:
            found.add("cardinality")
            if node[1] == 1:
                found.add("unique")
        elif op in {"min", "max", "dataMin", "dataMax"}:
            found.add("cardinality")
            if op in {"dataMin", "min"} and node[1] >= 1:
                found.add("non_empty")
        elif op == "dataSome":
            found.add("field_presence")
            found.add("existential")
        elif op == "dataOnly":
            found.add("universal")
        elif op == "dataHas":
            found.add("comparison")
        elif op == "and":
            found.add("conjunction")
        elif op == "or":
            found.add("disjunction")
        elif op == "not":
            found.add("exception")
        elif op in {"functionalObjectProperty", "inverseFunctionalObjectProperty", "functionalDataProperty"}:
            found.add("cardinality")
        elif op == "if":
            found.add("implication")
        for part in node[1:]:
            walk(part)

    walk(expr)
    return frozenset(found)


def _clif_kinds(expr: tuple) -> frozenset[str]:
    found: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, tuple) or not node:
            return
        op = node[0]
        if op == "forall":
            found.add("universal")
        elif op == "exists":
            found.add("existential")
            if _as_unique(node) is not None:
                found.add("unique")
        elif op == "and":
            found.add("conjunction")
        elif op == "or":
            found.add("disjunction")
        elif op == "if":
            found.add("implication")
        elif op == "eq":
            found.add("comparison")
            if _normalized(node):
                found.add("normalization")
        elif op == "cmp":
            found.add("threshold")
            found.add("comparison")
        elif op == "not":
            found.add("exception")
        for part in node[1:]:
            walk(part)

    walk(expr)
    return frozenset(found)


def _as_unique(expr: tuple):
    if not isinstance(expr, tuple) or expr[0] != "exists" or len(expr[1]) != 1:
        return None
    variable = expr[1][0]
    body = expr[2]
    parts = list(body[1:]) if body[0] == "and" else [body]
    guards = [part for part in parts if isinstance(part, tuple) and part[0] == "forall" and len(part[1]) == 1]
    if len(guards) != 1:
        return None
    guard = guards[0]
    other = guard[1][0]
    inner = guard[2]
    if inner[0] != "if" or inner[2] != ("eq", ("var", variable), ("var", other)):
        return None
    rest = [part for part in parts if part is not guard]
    condition = rest[0] if len(rest) == 1 else ("and", *rest)
    if _rename(condition, variable, other) != inner[1]:
        return None
    return variable, condition


def _rename(expr, source: str, target: str):
    if isinstance(expr, tuple):
        if expr == ("var", source):
            return ("var", target)
        return tuple(_rename(part, source, target) if not isinstance(part, str) else part for part in expr)
    return expr


def _normalized(expr: tuple) -> bool:
    def nested(term) -> bool:
        return isinstance(term, tuple) and term and term[0] == "fn" and any(isinstance(part, tuple) and part and part[0] == "fn" for part in term[2])

    return any(nested(part) for part in expr[1:])


def _builtin(iri: str) -> bool:
    return any(iri.startswith(prefix) for prefix in BUILTIN_PREFIXES.values()) or iri.startswith(AXIOM_NAMESPACE)


def _camel(word: str) -> str:
    return word[0].lower() + word[1:]


def _unquote(token: str) -> str:
    if len(token) < 2 or not token.startswith('"') or not token.endswith('"'):
        raise FormalSyntaxError("字符串不完整")
    return token[1:-1]


def _is_number(token: str) -> bool:
    if token.count(".") > 1 or token in {".", ""}:
        return False
    return token.replace(".", "", 1).isdigit()


def _is_name(token: str) -> bool:
    return bool(token) and token not in {"(", ")", ":="} and not token.startswith('"') and not token.startswith("<")


def serialize_owl(document: OwlDocument) -> str:
    """Render a parsed OWL document. The text is a check view, not a second authority."""
    prefixes = document.prefixes

    def iri(value: str) -> str:
        matches = [(name, base) for name, base in prefixes.items() if value.startswith(base)]
        if matches:
            name, base = max(matches, key=lambda item: len(item[1]))
            return f"{name}:{value[len(base):]}"
        return f"<{value}>"

    def literal(expr: tuple) -> str:
        value = str(expr[1]).replace("\\", "\\\\").replace('"', '\\"')
        if len(expr) < 3:
            return f'"{value}"'
        datatype = expr[2]
        if datatype == prefixes.get("xsd", "") + "integer" and str(expr[1]).isdigit():
            return str(expr[1])
        if datatype == prefixes.get("xsd", "") + "decimal" and _is_number(str(expr[1])) and "." in str(expr[1]):
            return str(expr[1])
        if datatype == prefixes.get("xsd", "") + "string":
            return f'"{value}"'
        return f'"{value}"^^{iri(datatype)}'

    def class_expr(expr) -> str:
        if not isinstance(expr, tuple) or not expr:
            raise FormalSyntaxError("不能序列化的类表达式")
        op = expr[0]
        if op == "class":
            return iri(expr[1])
        if op == "and":
            return "ObjectIntersectionOf(" + " ".join(class_expr(item) for item in expr[1:]) + ")"
        if op == "or":
            return "ObjectUnionOf(" + " ".join(class_expr(item) for item in expr[1:]) + ")"
        if op == "not":
            return f"ObjectComplementOf({class_expr(expr[1])})"
        if op == "some":
            return f"ObjectSomeValuesFrom({iri(expr[1])} {class_expr(expr[2])})"
        if op == "only":
            return f"ObjectAllValuesFrom({iri(expr[1])} {class_expr(expr[2])})"
        if op == "dataSome":
            return f"DataSomeValuesFrom({iri(expr[1])} {class_expr(expr[2])})"
        if op == "dataOnly":
            return f"DataAllValuesFrom({iri(expr[1])} {class_expr(expr[2])})"
        if op in {"exact", "min", "max", "dataExact", "dataMin", "dataMax"}:
            names = {
                "exact": "ObjectExactCardinality",
                "min": "ObjectMinCardinality",
                "max": "ObjectMaxCardinality",
                "dataExact": "DataExactCardinality",
                "dataMin": "DataMinCardinality",
                "dataMax": "DataMaxCardinality",
            }
            filler = "" if expr[3] is None else " " + class_expr(expr[3])
            return f"{names[op]}({expr[1]} {iri(expr[2])}{filler})"
        if op == "has":
            return f"ObjectHasValue({iri(expr[1])} {iri(expr[2])})"
        if op == "dataHas":
            return f"DataHasValue({iri(expr[1])} {literal(expr[2])})"
        if op == "datatype":
            return iri(expr[1])
        raise FormalSyntaxError(f"不能序列化的类表达式 {op}")

    def axiom(item: dict, index: int) -> str:
        expr = item["expr"]
        op = expr[0]
        names = {
            "subclass": "SubClassOf",
            "equivalent": "EquivalentClasses",
            "disjoint": "DisjointClasses",
            "objectPropertyDomain": "ObjectPropertyDomain",
            "objectPropertyRange": "ObjectPropertyRange",
            "dataPropertyDomain": "DataPropertyDomain",
            "dataPropertyRange": "DataPropertyRange",
            "functionalObjectProperty": "FunctionalObjectProperty",
            "inverseFunctionalObjectProperty": "InverseFunctionalObjectProperty",
            "functionalDataProperty": "FunctionalDataProperty",
            "subProperty": "SubObjectPropertyOf",
            "inverse": "InverseObjectProperties",
            "classAssertion": "ClassAssertion",
        }
        if op == "subclass":
            body = f"{class_expr(expr[1])} {class_expr(expr[2])}"
            word = names[op]
        elif op in {"equivalent", "disjoint"}:
            body = " ".join(class_expr(part) for part in expr[1])
            word = names[op]
        elif op in {"objectPropertyDomain", "objectPropertyRange", "dataPropertyDomain"}:
            body = f"{iri(expr[1])} {class_expr(expr[2])}"
            word = names[op]
        elif op == "dataPropertyRange":
            body = f"{iri(expr[1])} {class_expr(expr[2])}"
            word = names[op]
        elif op in {"functionalObjectProperty", "inverseFunctionalObjectProperty", "functionalDataProperty"}:
            body = iri(expr[1])
            word = names[op]
        elif op in {"subProperty", "inverse"}:
            body = f"{iri(expr[1])} {iri(expr[2])}"
            word = names[op]
        elif op == "classAssertion":
            body = f"{class_expr(expr[1])} {iri(expr[2])}"
            word = names[op]
        elif op == "assertion":
            right = expr[3]
            word = "DataPropertyAssertion" if isinstance(right, tuple) and right[0] == "lit" else "ObjectPropertyAssertion"
            rendered_right = literal(right) if word == "DataPropertyAssertion" else iri(right[1])
            body = f"{iri(expr[1])} {iri(expr[2])} {rendered_right}"
        else:
            raise FormalSyntaxError(f"不能序列化的公理 {op}")
        mark = ""
        if item["id"] != f"owl:{index}":
            mark = f'Annotation({iri(AXIOM_ID)} "{item["id"]}") '
        return f"{word}({mark}{body})"

    lines = [f"Prefix({name}:=<{base}>)" for name, base in prefixes.items()]
    body = [f"Declaration({kind}({iri(value)}))" for kind, value in document.declarations]
    body.extend(axiom(item, index) for index, item in enumerate(document.axioms, start=1))
    lines.append("Ontology(")
    lines.extend(body)
    lines.append(")")
    return "\n".join(lines) + "\n"


def serialize_clif(sentences: tuple[dict, ...]) -> str:
    """Render parsed CLIF sentences. The text is a check view, not a second authority."""

    def term(expr) -> str:
        if not isinstance(expr, tuple) or not expr:
            raise FormalSyntaxError("不能序列化的项")
        if expr[0] == "var":
            return expr[1]
        if expr[0] == "lit":
            value = str(expr[1])
            if len(expr) == 3 and _is_number(value):
                return f"{value} {expr[2]}"
            if _is_number(value) and not value.startswith("0"):
                return value
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        if expr[0] == "fn":
            return "(" + " ".join([expr[1], *(term(item) for item in expr[2])]) + ")"
        raise FormalSyntaxError(f"不能序列化的项 {expr[0]}")

    def formula(expr) -> str:
        op = expr[0]
        if op in {"forall", "exists"}:
            return f"({op} ({' '.join(expr[1])}) {formula(expr[2])})"
        if op in {"and", "or"}:
            return "(" + " ".join([op, *(formula(item) for item in expr[1:])]) + ")"
        if op == "not":
            return f"(not {formula(expr[1])})"
        if op in {"if", "iff"}:
            return f"({op} {formula(expr[1])} {formula(expr[2])})"
        if op == "eq":
            return f"(= {term(expr[1])} {term(expr[2])})"
        if op == "cmp":
            return f"({expr[1]} {term(expr[2])} {term(expr[3])})"
        if op == "atom":
            return "(" + " ".join([expr[1], *(term(item) for item in expr[2])]) + ")"
        raise FormalSyntaxError(f"不能序列化的公式 {op}")

    rendered = " ".join(f"(sentence {item['id']} {formula(item['expr'])})" for item in sentences)
    return f"(cl-text {rendered})"
