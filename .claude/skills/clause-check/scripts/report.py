"""Render the plain-language report.md from what check.py found.

The report is for the product owner: it lists where the clause is unclear, each with a concrete example the
script confirmed, in the clause's own words and the plain-language annotations written in the ontology
(`:issue`, `:question`, `rdfs:label`, `:says`, `:open`), never class names, reading codes or reasoning
vocabulary. It makes no claim about all files: only the examples and the given real files are judged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MISSING = "【待补白话说明】"
ORDINALS = "一二三四五六七八九十"
VERDICT_WORDS = {"PASS": "通过", "NG": "不通过", "NONE": "没有结论", "BOTH": "既通过又不通过"}


@dataclass
class Clause:
    title: str
    rule_id: str
    category: str
    text: str


@dataclass
class Found:
    clauses: list[Clause] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)  # findings not confirmed: the report is not usable yet
    ids: list[str] = field(default_factory=list)
    offered: list[list[str]] = field(default_factory=list)  # every reading written for each ambiguity
    chosen: dict[int, str] = field(default_factory=dict)  # position -> reading the user chose
    answered: dict[int, str] = field(default_factory=dict)  # the user's own words
    # ambiguity position -> plain texts
    issue: dict[int, str] = field(default_factory=dict)
    question: dict[int, str] = field(default_factory=dict)
    example: dict[int, str] = field(default_factory=dict)
    example_verdicts: dict[int, dict[str, str]] = field(default_factory=dict)  # position -> {letter: verdict}
    example_under: dict[int, list] = field(default_factory=dict)  # position -> [(position, letter)] the example assumes
    # (position, letter) -> short label / full sentence
    label: dict[tuple, str] = field(default_factory=dict)
    says: dict[tuple, str] = field(default_factory=dict)
    # (quote, [(question, answer or None)]) for every quote with :open
    undefined: list[tuple[str, list[tuple[str, str | None]]]] = field(default_factory=list)
    rewrites: list[str] = field(default_factory=list)
    # (situation in words, [(position, letter)] it assumes)
    gaps: list[tuple[str, list]] = field(default_factory=list)
    conflicts: list[tuple[str, list]] = field(default_factory=list)
    # file name -> (verdict with every open ambiguity undecided, {position: {letter: verdict}})
    files: dict[str, tuple[str, dict]] = field(default_factory=dict)


def ordinal(found: Found, position: int, letter: str) -> str:
    return "理解" + ORDINALS[found.offered[position].index(letter)]


def word(verdict: str) -> str:
    return VERDICT_WORDS.get(verdict, "推不出结论")


def assuming(found: Found, assumed: list) -> str:
    parts = [f"第 {position + 1} 处按“{found.label.get((position, letter), MISSING)}”理解" for position, letter in assumed]
    return f"当{'，且'.join(parts)}时，" if parts else ""


def affects(verdicts: dict[str, str]) -> bool:
    values = set(verdicts.values())
    return len(values) > 1 and not values & {"OPEN", "IMPOSSIBLE"}


def render(found: Found) -> str:
    lines: list[str] = []
    rule_ids = "、".join(clause.rule_id for clause in found.clauses) or "条款"
    lines.append(f"# {rule_ids} 条款检查报告\n")
    if found.incomplete:
        lines.append("> **有发现还没确认，下面的内容不能直接使用：**")
        lines += [f"> - {problem}" for problem in found.incomplete]
        lines.append("")
    for clause in found.clauses:
        lines.append(f"- **规则文件**：{clause.title}：{clause.category}（{clause.rule_id}）")
        lines.append(f"- **规则原文**：{' '.join(clause.text.split())}")
    unclear = len(found.ids) + len(found.undefined)
    parts = []
    if unclear:
        parts.append(f"有 {unclear} 处原文没有说清楚")
    if found.gaps:
        parts.append(f"有 {len(found.gaps)} 种文件原文没有给出结论")
    if found.conflicts:
        parts.append(f"有 {len(found.conflicts)} 种文件原文同时给出通过和不通过")
    lines.append(f"- **结论**：{'；'.join(parts) or '没有发现原文说不清楚的地方'}。\n")

    if unclear:
        lines.append("## 原文没说清的地方\n")
    number = 0
    for position in range(len(found.ids)):
        number += 1
        lines.append(f"### {number}. {found.issue.get(position, MISSING)}\n")
        for letter in found.offered[position]:
            mark = "【已按答复选定】" if found.chosen.get(position) == letter else ""
            lines.append(f"- {ordinal(found, position, letter)}（{found.label.get((position, letter), MISSING)}）{mark}：{found.says.get((position, letter), MISSING)}")
        lines.append("")
        if position in found.chosen:
            lines.append(f"答复：「{found.answered.get(position, MISSING)}」\n")
            continue
        if position in found.example:
            lines.append(f"例如{'（' + assuming(found, found.example_under[position])[1:-1] + '）' if found.example_under.get(position) else ''}：{found.example[position]}")
            verdicts = found.example_verdicts.get(position)
            if verdicts:
                lines.append("这份文件" + "，".join(f"按{ordinal(found, position, letter)}{word(v)}" for letter, v in verdicts.items()) + "。")
        affected = [
            f"`{name}`（" + "、".join(f"按{ordinal(found, position, letter)}{word(v)}" for letter, v in per[position].items()) + "）"
            for name, (_, per) in found.files.items()
            if position in per and affects(per[position])
        ]
        if affected:
            lines.append(f"\n受影响的现有文件：{'、'.join(affected)}。")
        lines.append("")

    for quote, questions in found.undefined:
        number += 1
        lines.append(f"### {number}. 「{quote}」\n")
        lines.append("原文没有说：\n")
        lines += [f"- {question}" + (f"\n  答复：「{answer}」" if answer else "") for question, answer in questions]
        lines.append("")

    if found.gaps:
        lines.append("## 原文没有给出结论的文件\n")
        lines += [f"- {assuming(found, assumed)}{situation}，原文既没说通过，也没说不通过。" for situation, assumed in found.gaps]
        lines.append("")
    if found.conflicts:
        lines.append("## 原文同时给出通过和不通过的文件\n")
        lines += [f"- {assuming(found, assumed)}{situation}，原文既说通过，又说不通过。" for situation, assumed in found.conflicts]
        lines.append("")

    if found.files:
        lines.append("## 现有文件\n")
        lines.append("| 文件 | 按原文的结论 |")
        lines.append("|---|---|")
        for name, (whole, per) in found.files.items():
            if whole not in ("OPEN", "IMPOSSIBLE"):
                result = word(whole)
            else:
                depends = [f"第 {position + 1} 处" for position, verdicts in per.items() if affects(verdicts)]
                result = f"取决于{'、'.join(depends)}的理解" if depends else "要等几处问题都答复后才能确定"
            lines.append(f"| `{name}` | {result} |")
        lines.append("")

    if found.rewrites:
        lines.append("## 建议改写的条款原文\n")
        lines += [f"> {rewrite}\n" for rewrite in found.rewrites]
    questions = [found.question.get(position, MISSING) for position in range(len(found.ids)) if position not in found.chosen]
    questions += [question for _, pairs in found.undefined for question, answer in pairs if not answer]
    answers = len(found.chosen) + sum(1 for _, pairs in found.undefined for _, answer in pairs if answer)
    if questions:
        lines.append("## 需要回答的问题\n" if not answers else f"## 还需要回答的问题（已答复 {answers} 个）\n")
        lines += [f"{index}. {question}" for index, question in enumerate(questions, 1)]
        lines.append("")
    elif answers and not found.rewrites:
        lines.append("所有问题都已答复，还没有按答复写出建议改写的条款原文。\n")
    return "\n".join(lines)
