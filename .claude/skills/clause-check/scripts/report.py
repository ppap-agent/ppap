"""Render the plain-language report.md from what check.py found.

The report is for the product owner: it uses only the clause's own words and the plain-language annotations
written in the ontology (`:issue`, `:question`, `rdfs:label`, `:says`, `:open`), never class names, reading
codes or reasoning vocabulary. A missing annotation shows as MISSING and is listed by check.py. Answers written
back (`:chosen` + `:answered` on an ambiguity, `:answered` on an `:open`) and a proposed rewrite (`:rewrite`)
turn the report into a check that the answers close every gap.
"""

from __future__ import annotations

import itertools
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
    unaligned: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)  # checks that failed: the conclusions below are not usable
    ids: list[str] = field(default_factory=list)
    letters: list[list[str]] = field(default_factory=list)  # readings still in play (one when answered)
    offered: list[list[str]] = field(default_factory=list)  # every reading written for each ambiguity
    combos: list[tuple] = field(default_factory=list)
    # ambiguity position -> plain texts
    issue: dict[int, str] = field(default_factory=dict)
    question: dict[int, str] = field(default_factory=dict)
    example: dict[int, str] = field(default_factory=dict)
    example_verdicts: dict[int, dict] = field(default_factory=dict)  # position -> {combo: verdict}
    answered: dict[int, str] = field(default_factory=dict)  # the user's own words
    # (position, letter) -> short label / full sentence
    label: dict[tuple, str] = field(default_factory=dict)
    says: dict[tuple, str] = field(default_factory=dict)
    # position -> {(reading a, reading b, verdict under a, verdict under b): combinations (with reading a) where it happens}
    divergences: dict[int, dict[tuple, set]] = field(default_factory=dict)
    # (quote, [(question, answer or None)]) for every quote with :open, and open groups found by completeness
    undefined: list[tuple[str, list[tuple[str, str | None]]]] = field(default_factory=list)
    rewrites: list[str] = field(default_factory=list)
    # (heading, question) for value groups the clause never says are complete
    groups: list[tuple[str, str]] = field(default_factory=list)
    # (situation in words, combinations where it holds)
    gaps: list[tuple[str, set]] = field(default_factory=list)
    conflicts: list[tuple[str, set]] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    # file name -> {combo: verdict}
    files: dict[str, dict] = field(default_factory=dict)


def compress(combos: set[tuple], letters: list[list[str]]) -> list[tuple]:
    """Merge combinations that differ in one position until nothing merges; each result is a tuple of letter sets."""
    patterns = {tuple(frozenset([letter]) for letter in combo) for combo in combos}
    merged = True
    while merged:
        merged = False
        for left, right in itertools.combinations(sorted(patterns, key=str), 2):
            differ = [i for i in range(len(left)) if left[i] != right[i]]
            if len(differ) == 1:
                i = differ[0]
                patterns -= {left, right}
                patterns.add(left[:i] + (left[i] | right[i],) + left[i + 1 :])
                merged = True
                break
    return sorted(patterns, key=lambda pattern: [sorted(part) for part in pattern])


def ordinal(found: Found, position: int, letter: str) -> str:
    return "理解" + ORDINALS[found.offered[position].index(letter)]


def condition(found: Found, combos: set, skip: int | None = None) -> str:
    """When something happens, in words: which readings it needs. `skip` leaves out one ambiguity's own reading."""
    if not found.ids:
        return ""
    sentences = []
    for pattern in compress(combos, found.letters):
        parts = []
        for position, chosen in enumerate(pattern):
            if position == skip or len(chosen) == len(found.letters[position]):
                continue
            names = "或".join(f"“{found.label.get((position, letter), MISSING)}”" for letter in sorted(chosen))
            parts.append(f"第 {position + 1} 处按{names}理解")
        sentences.append("，且".join(parts) if parts else "")
    if any(not sentence for sentence in sentences):
        if skip is not None:
            return "不论其他各处怎么理解" if len(found.ids) > 1 else ""
        return "不论各处怎么理解" if found.ids else ""
    return "；或者".join(sentences)


def when(condition_text: str, sentence: str) -> str:
    """“当……时，sentence。” or “不论……，sentence。”"""
    if not condition_text:
        return f"{sentence}。"
    if condition_text.startswith("不论"):
        return f"{condition_text}，{sentence}。"
    return f"当{condition_text}时，{sentence}。"


def differences(found: Found, position: int, verdicts: dict) -> list[str]:
    """How a file's verdict changes between readings of one ambiguity, in words."""
    changes = []
    for a, b in itertools.combinations(found.letters[position], 2):
        pairs = {
            (verdicts[combo], verdicts[combo[:position] + (b,) + combo[position + 1 :]])
            for combo in found.combos
            if combo[position] == a and verdicts[combo] != verdicts[combo[:position] + (b,) + combo[position + 1 :]]
        }
        changes += [f"按{ordinal(found, position, a)}{VERDICT_WORDS[x]}、按{ordinal(found, position, b)}{VERDICT_WORDS[y]}" for x, y in sorted(pairs)]
    return changes


def example_outcome(found: Found, position: int) -> str:
    """The example's verdict under each reading of its ambiguity, with the other readings it depends on."""
    verdicts = found.example_verdicts[position]
    parts = []
    for letter in found.letters[position]:
        by_verdict: dict[str, set] = {}
        for combo, verdict in verdicts.items():
            if combo[position] == letter:
                by_verdict.setdefault(verdict, set()).add(combo)
        name = ordinal(found, position, letter)
        if len(by_verdict) == 1:
            parts.append(f"按{name}{VERDICT_WORDS.get(next(iter(by_verdict)), '推不出结论')}")
        else:
            parts += [f"按{name}、且{condition(found, combos, skip=position)}时{VERDICT_WORDS.get(verdict, '推不出结论')}" for verdict, combos in sorted(by_verdict.items())]
    return "：" + "；".join(parts)


def check_answers(found: Found) -> list[str]:
    """Whether the answers written back close the clause: what is still open, and what the verdicts become."""
    lines = ["## 答复核对\n"]
    still = [found.question.get(p, MISSING) for p in range(len(found.ids)) if p not in found.answered]
    still += [question for _, pairs in found.undefined for question, answer in pairs if not answer]
    still += [question for _, question in found.groups]
    lines.append(f"- 还有 {len(still)} 个问题待答复。" if still else "- 所有问题都已答复。")
    lines.append(f"- 按目前的答复，仍有 {len(found.gaps)} 种情况原文没有给出结论，见上文。" if found.gaps else "- 按目前的答复，没有原文不给结论的情况。")
    if found.conflicts:
        lines.append("- 按目前的答复，原文仍对有的文件同时给出通过和不通过，见上文。")
    for name, verdicts in found.files.items():
        outcomes = set(verdicts.values())
        lines.append(f"- `{name}`：结论还取决于待答复的问题。" if len(outcomes) > 1 else f"- `{name}`：按答复{VERDICT_WORDS.get(next(iter(outcomes)), '推不出结论')}。")
    if not still and not found.rewrites:
        lines.append("- 所有问题都已答复，还没有按答复写出建议改写的条款原文。")
    lines.append("")
    return lines


def render(found: Found) -> str:
    lines: list[str] = []
    rule_ids = "、".join(clause.rule_id for clause in found.clauses) or "条款"
    lines.append(f"# {rule_ids} 条款检查报告\n")
    problems = list(found.incomplete)
    if found.unaligned:
        problems.insert(0, "原文这些部分还没有分析：" + "、".join(f"「{span}」" for span in found.unaligned))
    if problems:
        lines.append("> **检查还没完成，下面的结论不能直接使用：**")
        lines += [f"> - {problem}" for problem in problems]
        lines.append("")
    for clause in found.clauses:
        lines.append(f"- **规则文件**：{clause.title}：{clause.category}（{clause.rule_id}）")
        lines.append(f"- **规则原文**：{' '.join(clause.text.split())}")
    unclear = len(found.ids) + len(found.undefined) + len(found.groups)
    asked = len(found.ids) + sum(len(questions) for _, questions in found.undefined) + len(found.groups)
    answers = len(found.answered) + sum(1 for _, questions in found.undefined for _, answer in questions if answer)
    confirmed = sum(1 for position in range(len(found.ids)) if position not in found.answered and found.divergences.get(position))
    parts = []
    if unclear:
        part = f"有 {unclear} 处原文没有说清楚" + (f"（共 {asked} 个问题）" if asked != unclear else "")
        if answers:
            part += f"，已答复 {answers} 个问题、还有 {asked - answers} 个待答复"
        if confirmed:
            part += f"，{'待答复的歧义中' if answers else '其中'}有 {confirmed} 处会让同一份文件得出不同结论"
        parts.append(part)
    if found.gaps:
        parts.append(f"有 {len(found.gaps)} 种情况原文没有给出结论")
    if found.conflicts:
        parts.append(f"有 {len(found.conflicts)} 种情况原文同时给出通过和不通过")
    lines.append(f"- **结论**：{'；'.join(parts) or '没有发现原文说不清楚的地方'}。\n")

    if unclear:
        lines.append("## 原文没说清的地方\n")
    number = 0
    for position in range(len(found.ids)):
        number += 1
        lines.append(f"### {number}. {found.issue.get(position, MISSING)}\n")
        for letter in found.offered[position]:
            short = found.label.get((position, letter), MISSING)
            mark = "【已按你的答复选定】" if position in found.answered and letter in found.letters[position] else ""
            lines.append(f"- {ordinal(found, position, letter)}（{short}）{mark}：{found.says.get((position, letter), MISSING)}")
        lines.append("")
        if position in found.answered:
            lines.append(f"你的答复：「{found.answered[position]}」\n")
            continue
        kinds = found.divergences.get(position, {})
        for a, b in itertools.combinations(found.letters[position], 2):
            by_condition: dict[str, list[str]] = {}
            for (x, y, first, second), contexts in sorted(kinds.items()):
                if (x, y) == (a, b):
                    sentence = f"按{ordinal(found, position, a)}{VERDICT_WORDS[first]}、按{ordinal(found, position, b)}{VERDICT_WORDS[second]}"
                    by_condition.setdefault(condition(found, contexts, skip=position), []).append(sentence)
            for text, sentences in by_condition.items():
                lines.append(when(text, "总有文件会" + "，或".join(sentences)))
        if not kinds:
            lines.append("这几种理解对任何文件都给出相同的结论。")
        if position in found.example:
            lines.append(f"\n例如：{found.example[position]}")
            if position in found.example_verdicts:
                lines.append(f"这个例子{example_outcome(found, position)}。")
        affected = [f"`{name}`（{'；'.join(changes)}）" for name, verdicts in found.files.items() if (changes := differences(found, position, verdicts))]
        lines.append(f"\n受影响的现有文件：{'、'.join(affected)}。\n" if affected else "\n现有文件不受这一处影响。\n")

    for quote, questions in found.undefined:
        number += 1
        lines.append(f"### {number}. 「{quote}」\n")
        lines.append("原文没有说：\n")
        lines += [f"- {question}" + (f"\n  你的答复：「{answer}」" if answer else "") for question, answer in questions]
        lines.append("")

    for heading, question in found.groups:
        number += 1
        lines.append(f"### {number}. {heading}\n")
        lines.append(f"原文没有说：\n\n- {question}\n")

    if found.gaps:
        lines.append("## 原文没有给出结论的情况\n")
        lines += ["- " + when(condition(found, combos), f"{situation}，原文既没说通过，也没说不通过") for situation, combos in found.gaps]
        lines.append("")
    if found.conflicts:
        lines.append("## 原文同时给出通过和不通过的情况\n")
        lines += ["- " + when(condition(found, combos), f"{situation}，原文既说通过，又说不通过") for situation, combos in found.conflicts]
        lines.append("")

    if found.files:
        lines.append("## 现有文件\n")
        lines.append("| 文件 | 按原文的结论 | 结论会随哪几处改变 |")
        lines.append("|---|---|---|")
        for name, verdicts in found.files.items():
            outcomes = {verdicts[combo] for combo in found.combos}
            changes = [f"第 {position + 1} 处" for position in range(len(found.ids)) if differences(found, position, verdicts)]
            result = VERDICT_WORDS.get(next(iter(outcomes)), "推不出结论") if len(outcomes) == 1 else "取决于怎么理解"
            lines.append(f"| `{name}` | {result} | {'、'.join(changes) or '—'} |")
        lines.append("\n每份文件在各处按不同理解时怎么变，见上面各处歧义下的“受影响的现有文件”。\n")

    if answers:
        lines += check_answers(found)
    if found.rewrites:
        lines.append("## 建议改写的条款原文\n")
        lines += [f"> {rewrite}\n" for rewrite in found.rewrites]
    questions = [found.question.get(position, MISSING) for position in range(len(found.ids)) if position not in found.answered]
    questions += [question for _, pairs in found.undefined for question, answer in pairs if not answer]
    questions += [question for _, question in found.groups]
    if questions:
        lines.append("## 需要回答的问题\n" if not answers else "## 还需要回答的问题\n")
        lines += [f"{index}. {question}" for index, question in enumerate(questions, 1)]
        lines.append("")
    if found.requirements:
        lines.append("## 对检查程序的要求\n")
        lines += [f"- {requirement}" for requirement in found.requirements]
        lines.append("")
    return "\n".join(lines)
