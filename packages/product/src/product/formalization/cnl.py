"""Deterministic Chinese verbalization of one checked OWL/CLIF specification.

The text is a projection of that specification. It is not a second authority.
"""

from __future__ import annotations

from audit.formal_digest import CNL_RENDERER_VERSION, display_digest

from product.formalization.parse import _as_unique

RENDERER_VERSION = CNL_RENDERER_VERSION


def verbalize(checked: dict) -> dict:
    labels = {entity["iri"]: entity["label"] for entity in checked["vocabulary"]["entities"]}
    roles = {entity["iri"]: entity.get("role") or "none" for entity in checked["vocabulary"]["entities"]}
    pieces: list[str] = []
    unsupported: list[dict] = []
    for obligation in checked["obligations"]:
        node = checked["nodes"].get(obligation["formal_ref"])
        if node is None:
            unsupported.append({"id": obligation["id"], "formal_ref": obligation["formal_ref"], "reason": "missing_expression"})
            continue
        sentence, covered = _sentence(node, labels, roles)
        if obligation["semantic_kind"] not in covered or _generic(sentence):
            unsupported.append(
                {
                    "id": obligation["id"],
                    "formal_ref": obligation["formal_ref"],
                    "reason": "kind_not_in_expression",
                    "semantic_kind": obligation["semantic_kind"],
                    "source_span": obligation.get("source_span"),
                }
            )
        pieces.append(f"{obligation['id']}：{sentence}")
    unresolved = checked["unresolved"]
    gaps = checked.get("gaps") or []
    if unresolved or unsupported or gaps or checked["completeness"] != "complete":
        lines = ["尚不能确定："]
        for item in gaps:
            span = f"（原文 {item['source_span']}）" if item.get("source_span") else ""
            lines.append(f"- {item.get('reason')}：{item.get('formal_ref')}{span}")
        for item in unresolved:
            lines.append(f"- {item['reason']}")
            for choice in item.get("choices") or []:
                lines.append(f"  选项 {choice['id']}：{choice['summary']}。反例：{choice['counterexample']}")
        for item in unsupported:
            span = f"（原文 {item['source_span']}）" if item.get("source_span") else ""
            lines.append(f"- 义务 {item['id']} 没有被形式表达式覆盖（{item['reason']}：{item.get('formal_ref')}）{span}")
        if pieces:
            lines.extend(["", "候选理解（未确认）：", *pieces])
        cnl = "\n".join(lines)
    else:
        cnl = "\n".join(pieces)
    for assumption in checked["assumptions"]:
        cnl += f"\n已声明前提：{assumption['text']}"
    if "全部符合" in cnl or "规则解释已确认" in cnl:
        raise RuntimeError("回译不得宣称已经确认或全部符合")
    return {
        "cnl": cnl,
        "obligations": list(checked["obligations"]),
        "unsupported": unsupported,
        "renderer_revision": RENDERER_VERSION,
        "display_digest": display_digest(cnl, checked["formal_digest"], checked["unresolved"], checked["completeness"]),
    }


def structural_signature(checked: dict) -> list:
    names: dict[str, str] = {}

    def name(value: str) -> str:
        if value not in names:
            names[value] = f"n{len(names) + 1}"
        return names[value]

    def walk(expr):
        if isinstance(expr, tuple):
            if expr and expr[0] in {"atom", "fn", "class"}:
                return (
                    expr[0],
                    name(str(expr[1])),
                ) + tuple(walk(part) for part in expr[2:])
            return tuple(walk(part) if isinstance(part, tuple) else part for part in expr)
        if isinstance(expr, dict):
            return walk(expr.get("expr"))
        return expr

    obligations = checked["obligations"] if "obligations" in checked else checked["spec"]["obligations"]
    return [walk(checked["nodes"][item["formal_ref"]]) for item in obligations]


def coverage(node: dict, roles: dict[str, str]) -> set[str]:
    covered = set(node["kinds"]) | _policy_kinds(node["expr"], roles)
    if node.get("language") == "clif" and _relation(node["expr"], {}) is not None:
        covered.add("direction")
    return covered


def _sentence(node: dict, labels: dict[str, str], roles: dict[str, str]) -> tuple[str, set[str]]:
    covered = coverage(node, roles)
    if node.get("language") == "owl":
        return _owl(node["expr"], labels), covered
    relation = _relation(node["expr"], labels)
    if relation is not None:
        return relation, covered
    return _clif(node["expr"], labels, roles) + "。", covered


def _relation(expr: tuple, labels: dict[str, str]) -> str | None:
    if expr[0] != "forall" or len(expr[1]) != 1 or expr[2][0] != "if":
        return None
    variable = expr[1][0]
    source = _atom_name(expr[2][1], variable)
    if source is None:
        return None
    source_label = _label(source, labels)
    body = expr[2][2]
    split = _key_then_rest(body, labels)
    if split is not None:
        target, key_text, rest_text = split
        return f"每一条「{source_label}」恰好有一条「{_label(target, labels)}」，且{key_text}。并且，每一条满足该条件的「{_label(target, labels)}」，{rest_text}。"
    unique = _as_unique(body)
    if unique is not None:
        target, conditions = _target_conditions(unique[1], labels)
        if target is None:
            return None
        target_label = _label(target, labels)
        if len(conditions) > 1:
            return f"每一条「{source_label}」恰好关联一条同时满足{'和'.join(conditions)}的「{target_label}」。"
        if conditions:
            return f"每一条「{source_label}」恰好关联一条「{target_label}」，且{conditions[0]}。"
        return f"每一条「{source_label}」恰好关联一条「{target_label}」。"
    target, conditions = _exists_target(body, labels)
    if target is None:
        return None
    target_label = _label(target, labels)
    if conditions:
        return f"每一条「{source_label}」至少关联一条「{target_label}」，且{'，并且'.join(conditions)}。"
    return f"每一条「{source_label}」至少关联一条「{target_label}」。"


def _key_then_rest(body: tuple, labels: dict[str, str]):
    if body[0] != "and" or len(body) != 3:
        return None
    unique = _as_unique(body[1])
    rest = body[2]
    if unique is None or rest[0] != "forall":
        return None
    target, conditions = _target_conditions(unique[1], labels)
    if target is None or len(conditions) != 1 or rest[2][0] != "if":
        return None
    conclusion = _equation(rest[2][2], labels)
    if conclusion is None:
        return None
    return target, conditions[0], conclusion


def _exists_target(body: tuple, labels: dict[str, str]):
    if body[0] != "exists":
        return None, []
    return _target_conditions(body[2], labels)


def _target_conditions(expr: tuple, labels: dict[str, str]):
    parts = list(expr[1:]) if isinstance(expr, tuple) and expr and expr[0] == "and" else [expr]
    target = None
    conditions = []
    for part in parts:
        if isinstance(part, tuple) and part[0] == "atom" and len(part[2]) == 1 and target is None:
            target = part[1]
            continue
        text = _equation(part, labels) if isinstance(part, tuple) else None
        if text is None:
            return None, []
        conditions.append(text)
    return target, conditions


def _equation(expr: tuple, labels: dict[str, str]) -> str | None:
    if expr[0] == "eq":
        left, right = expr[1], expr[2]
        if left[0] == "fn" and right[0] == "fn" and left[1] == right[1]:
            if any(part[0] == "fn" for part in left[2] if isinstance(part, tuple)):
                return f"按已声明的归一化「{_label(left[1], labels)}」后严格相等"
            return f"「{_label(left[1], labels)}」严格相等"
        return "两端严格相等"
    if expr[0] == "cmp":
        field = expr[2][1] if expr[2][0] == "fn" else "值"
        if expr[3][0] == "lit":
            value = expr[3][1] + (f" {expr[3][2]}" if len(expr[3]) > 2 and expr[3][2] else "")
        else:
            value = "另一值"
        words = {">=": "大于或等于", ">": "大于", "<=": "小于或等于", "<": "小于"}
        return f"「{_label(field, labels)}」{words[expr[1]]} {value}"
    return None


def _atom_name(expr: tuple, variable: str) -> str | None:
    if isinstance(expr, tuple) and expr[0] == "atom" and expr[2] == (("var", variable),):
        return expr[1]
    return None


def _owl(expr: tuple, labels: dict[str, str]) -> str:
    if expr[0] == "subclass":
        return f"每一条「{_class_name(expr[1], labels)}」{_restriction(expr[2], labels)}"
    if expr[0] == "dataHas":
        return f"「{_label(expr[1], labels)}」的值是「{expr[2][1]}」，按符号逐字比较。"
    if expr[0] == "functionalDataProperty":
        return f"同一条记录的「{_label(expr[1], labels)}」最多一个值。这不是跨记录的唯一匹配。"
    if len(expr) > 1 and isinstance(expr[1], str):
        return f"未覆盖的形式构造「{_label(expr[1], labels)}」。"
    return "未覆盖的形式构造。"


def _generic(sentence: str) -> bool:
    return "未覆盖的形式构造" in sentence


def _restriction(expr: tuple, labels: dict[str, str]) -> str:
    if expr[0] == "class":
        return f"属于「{_label(expr[1], labels)}」"
    if expr[0] == "some":
        return f"至少有一条「{_label(expr[1], labels)}」指向「{_class_name(expr[2], labels)}」。"
    if expr[0] == "only":
        return f"的全部「{_label(expr[1], labels)}」都指向「{_class_name(expr[2], labels)}」。"
    if expr[0] in {"exact", "min", "max"}:
        word = {"exact": "恰好有", "min": "至少有", "max": "至多有"}[expr[0]]
        target = f"指向「{_class_name(expr[3], labels)}」" if expr[3] else ""
        return f"{word} {expr[1]} 条「{_label(expr[2], labels)}」{target}。"
    if expr[0] == "dataSome":
        return f"的「{_label(expr[1], labels)}」至少有一个值。"
    if expr[0] in {"dataMin", "dataMax", "dataExact"}:
        word = {"dataMin": "至少有", "dataMax": "至多有", "dataExact": "恰好有"}[expr[0]]
        count = "一个" if expr[1] == 1 else f"{expr[1]} 个"
        return f"的「{_label(expr[2], labels)}」{word} {count}值。"
    if expr[0] == "dataOnly":
        return f"的全部「{_label(expr[1], labels)}」值都属于「{_range_name(expr[2], labels)}」。"
    if expr[0] == "dataHas":
        return f"的「{_label(expr[1], labels)}」取值「{expr[2][1]}」，按符号逐字比较。"
    if expr[0] == "and":
        return "同时满足" + "，并且".join(_restriction(item, labels).rstrip("。") for item in expr[1:]) + "。"
    if expr[0] == "or":
        return "满足其中之一：" + "，或者".join(_restriction(item, labels).rstrip("。") for item in expr[1:]) + "。"
    if expr[0] == "not":
        return "除外：" + _restriction(expr[1], labels)
    if expr[0] == "has":
        return f"有一个「{_label(expr[1], labels)}」等于「{_label(expr[2], labels)}」。"
    if len(expr) > 1 and isinstance(expr[1], str):
        return f"未覆盖的形式构造「{_label(expr[1], labels)}」。"
    return "未覆盖的形式构造。"


def _clif(expr: tuple, labels: dict[str, str], roles: dict[str, str]) -> str:
    op = expr[0]
    if op == "forall":
        return "对每一条" + "、".join(expr[1]) + "：" + _clif(expr[2], labels, roles)
    if op == "exists":
        unique = _as_unique(expr)
        if unique is not None:
            return "恰好有一条满足：" + _clif(unique[1], labels, roles)
        return "至少有一条" + "、".join(expr[1]) + "满足：" + _clif(expr[2], labels, roles)
    if op == "and":
        return "同时满足" + "，并且".join(_clif(part, labels, roles) for part in expr[1:])
    if op == "or":
        return "满足其中之一：" + "，或者".join(_clif(part, labels, roles) for part in expr[1:])
    if op == "not":
        return "除外：" + _clif(expr[1], labels, roles)
    if op == "if":
        return "如果" + _clif(expr[1], labels, roles) + "，则" + _clif(expr[2], labels, roles)
    if op in {"eq", "cmp"}:
        return _equation(expr, labels) or "比较"
    if op == "atom":
        role = roles.get(expr[1], "none")
        label = _label(expr[1], labels)
        if role == "empty":
            return f"「{label}」为空集合时的结果只按本句，不使用默认结论"
        if role == "missing":
            return f"确认缺失时的结果由「{label}」声明"
        if role == "unknown":
            return f"证据不足时的结果由「{label}」声明"
        if role == "column":
            return f"表头中有列「{label}」。这不表示数据行里有这个值"
        if role == "scope":
            return f"适用范围由「{label}」声明"
        if role == "duplicate":
            return f"重复记录是否违规由「{label}」声明，不使用默认重复政策"
        return f"「{label}」成立{_mentions(expr[2], labels)}"
    if isinstance(op, str) and op:
        return f"未覆盖的形式构造「{op}」。"
    return "未覆盖的形式构造。"


def _mentions(terms, labels: dict[str, str]) -> str:
    names = []
    for term in terms:
        if not isinstance(term, tuple) or term[0] != "var" or not isinstance(term[1], str) or ":" not in term[1]:
            continue
        local = term[1].rsplit(":", 1)[-1]
        label = next((text for iri, text in labels.items() if iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] == local), None)
        if label:
            names.append(label)
    if not names:
        return ""
    return "，涉及" + "、".join(f"「{name}」" for name in names)


def _policy_kinds(expr, roles: dict[str, str]) -> set[str]:
    found: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, tuple):
            return
        if node and node[0] == "atom":
            kind = {
                "empty": "empty_policy",
                "missing": "missing_policy",
                "unknown": "unknown_policy",
                "column": "column_presence",
                "scope": "scope",
                "duplicate": "duplicate_policy",
            }.get(roles.get(node[1], "none"))
            if kind:
                found.add(kind)
        for part in node:
            walk(part)

    walk(expr)
    return found


def _range_name(expr: tuple, labels: dict[str, str]) -> str:
    if isinstance(expr, tuple) and expr and expr[0] == "datatype":
        return _label(expr[1], labels)
    return "指定类型"


def _class_name(expr: tuple, labels: dict[str, str]) -> str:
    if isinstance(expr, tuple) and expr and expr[0] == "class":
        return _label(expr[1], labels)
    return "复合类"


def _label(iri: str, labels: dict[str, str]) -> str:
    return labels.get(iri) or iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
