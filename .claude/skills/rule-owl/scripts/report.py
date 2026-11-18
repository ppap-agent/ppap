"""Render the plain-language report.md from what check.py found.

The report is for the product owner: it uses only the clause's own words and the plain-language annotations
written in the ontology (`:issue`, `:question`, `:example`, `rdfs:label`, `:says`, `:open`), never class names,
reading codes or reasoning vocabulary. A missing annotation shows as MISSING and is listed by check.py.
Answers written back (`:chosen` + `:answered` on an ambiguity, `:answered` on an `:open`) and a proposed
rewrite (`:rewrite`) turn the report into a check that the answers close every gap.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

MISSING = "【待补白话说明】"
ORDINALS = "一二三四五六七八九十"
VERDICT_WORDS = {"PASS": "通过", "NG": "不通过", "无结论": "没有结论", "矛盾": "既通过又不通过", "事实与条款矛盾": "文件描述与条款矛盾"}


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
    ids: list[str] = field(default_factory=list)
    letters: list[list[str]] = field(default_factory=list)  # readings still in play (one when answered)
    offered: list[list[str]] = field(default_factory=list)  # every reading written for each ambiguity
    combos: list[tuple] = field(default_factory=list)
    # ambiguity position -> plain texts
    issue: dict[int, str] = field(default_factory=dict)
    question: dict[int, str] = field(default_factory=dict)
    example: dict[int, str] = field(default_factory=dict)
    answered: dict[int, str] = field(default_factory=dict)  # the user's own words
    # (position, letter) -> short label / full sentence
    label: dict[tuple, str] = field(default_factory=dict)
    says: dict[tuple, str] = field(default_factory=dict)
    # (position, a, b) -> combinations where reading a passes and b fails the same file
    divergences: dict[tuple, set] = field(default_factory=dict)
    # (quote, [(question, answer or None)]) for every quote with :open
    undefined: list[tuple[str, list[tuple[str, str | None]]]] = field(default_factory=list)
    rewrites: list[str] = field(default_factory=list)
    # (sentence, combinations where it is still possible)
    gaps: list[tuple[str, set]] = field(default_factory=list)
    contradictions: list[tuple[str, set]] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    # file name -> (truth or None, {combo: verdict})
    files: dict[str, tuple[str | None, dict]] = field(default_factory=dict)


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
        return "不论其他各处怎么理解" if skip is not None else "不论各处怎么理解"
    return "；或者".join(sentences)


def when(condition_text: str, sentence: str) -> str:
    """“当……时，sentence。” or “不论……，sentence。”"""
    if not condition_text:
        return f"{sentence}。"
    if condition_text.startswith("不论"):
        return f"{condition_text}，{sentence}。"
    return f"当{condition_text}时，{sentence}。"


def check_answers(found: Found, gaps: list) -> list[str]:
    """Whether the answers written back close the clause: what is still open, and what the current verdicts become."""
    lines = ["## 答复核对\n"]
    still = [found.question.get(p, MISSING) for p in range(len(found.ids)) if p not in found.answered]
    still += [question for _, pairs in found.undefined for question, answer in pairs if not answer]
    lines.append(f"- 还有 {len(still)} 个问题待答复。" if still else "- 所有问题都已答复。")
    lines.append(f"- 按目前的答复，仍有 {len(gaps)} 种情况原文没有给出结论，见上文。" if gaps else "- 按目前的答复，没有原文不给结论的情况。")
    if found.contradictions:
        lines.append("- 按目前的答复，原文有自相矛盾的地方，见上文。")
    for name, (truth, verdicts) in found.files.items():
        outcomes = set(verdicts.values())
        if len(outcomes) > 1:
            lines.append(f"- `{name}`：结论还取决于待答复的问题。")
            continue
        outcome = next(iter(outcomes))
        words = VERDICT_WORDS.get(outcome, outcome)
        if truth and outcome != truth:
            lines.append(f"- `{name}`：按答复{words}，与现有判定（{VERDICT_WORDS.get(truth, truth)}）不一致，需要改现有判定或复查答复。")
        else:
            lines.append(f"- `{name}`：按答复{words}" + ("，与现有判定一致。" if truth else "。"))
    if not still and not found.rewrites:
        lines.append("- 所有问题都已答复，还没有按答复写出建议改写的条款原文。")
    lines.append("")
    return lines


def render(found: Found) -> str:
    lines: list[str] = []
    rule_ids = "、".join(clause.rule_id for clause in found.clauses) or "条款"
    lines.append(f"# {rule_ids} 条款检查报告\n")
    if found.unaligned:
        lines.append("> 注意：原文下面这些部分还没有分析，报告不完整：" + "、".join(f"「{span}」" for span in found.unaligned) + "\n")
    for clause in found.clauses:
        lines.append(f"- **规则文件**：{clause.title}：{clause.category}（{clause.rule_id}）")
        lines.append(f"- **规则原文**：{' '.join(clause.text.split())}")
    unclear = len(found.ids) + sum(len(questions) for _, questions in found.undefined)
    answers = len(found.answered) + sum(1 for _, questions in found.undefined for _, answer in questions if answer)
    confirmed = sum(1 for position in range(len(found.ids)) if any(found.divergences.get((position, a, b)) for a, b in itertools.permutations(found.letters[position], 2)))
    gaps = [gap for gap in found.gaps if gap[1]]
    summary = f"有 {unclear} 处原文没有说清楚"
    if answers:
        summary += f"，已答复 {answers} 处、还有 {unclear - answers} 处待答复"
    if found.ids and confirmed:
        summary += f"，{'待答复的歧义中' if answers else '其中'}有 {confirmed} 处已确认会让同一份文件得出相反的结论"
    if gaps:
        summary += f"；另有 {len(gaps)} 种情况原文没有给出结论"
    if found.contradictions:
        summary += f"；有 {len(found.contradictions)} 处原文自相矛盾"
    lines.append(f"- **结论**：{summary if unclear or gaps or found.contradictions else '没有发现原文说不清楚的地方'}。\n")

    lines.append("## 歧义分析\n")
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
        any_divergence = False
        for a, b in itertools.permutations(found.letters[position], 2):
            contexts = found.divergences.get((position, a, b), set())
            if contexts:
                any_divergence = True
                lines.append(when(condition(found, contexts, skip=position), f"同一份文件可能按{ordinal(found, position, a)}通过、按{ordinal(found, position, b)}不通过"))
        if not any_divergence:
            lines.append("这几种理解对任何文件都给出相同的结论。")
        if position in found.example:
            lines.append(f"\n例如：{found.example[position]}")
        affected = []
        for name, (_, verdicts) in found.files.items():
            flips = []
            for a, b in itertools.permutations(found.letters[position], 2):
                if any(verdicts.get(combo) == "PASS" and verdicts.get(combo[:position] + (b,) + combo[position + 1 :]) == "NG" for combo in found.combos if combo[position] == a):
                    flips.append(f"按{ordinal(found, position, a)}通过、按{ordinal(found, position, b)}不通过")
            if flips:
                affected.append(f"`{name}`（{'；'.join(flips)}）")
        lines.append(f"\n受影响的现有文件：{'、'.join(affected)}。\n" if affected else "\n现有文件不受这一处影响。\n")

    for quote, questions in found.undefined:
        number += 1
        lines.append(f"### {number}. 「{quote}」\n")
        lines.append("原文没有说：\n")
        lines += [f"- {question}" + (f"\n  你的答复：「{answer}」" if answer else "") for question, answer in questions]
        lines.append("")

    if gaps:
        lines.append("### 原文没有给出结论的情况\n")
        lines += ["- " + when(condition(found, combos), f"{sentence}，原文没有给出结论") for sentence, combos in gaps]
        lines.append("")
    if found.contradictions:
        lines.append("### 原文自相矛盾的地方\n")
        lines += ["- " + when(condition(found, combos), f"{sentence}不可能成立") for sentence, combos in found.contradictions]
        lines.append("")

    if found.files:
        lines.append("## 现有文件按各种理解的结论\n")
        lines.append("| 文件 | 通过 | 不通过 | 没有结论 | 现有判定 |")
        lines.append("|---|---|---|---|---|")
        for name, (truth, verdicts) in found.files.items():
            by_verdict: dict[str, set] = {}
            for combo, verdict in verdicts.items():
                by_verdict.setdefault(verdict, set()).add(combo)

            def cell(verdict: str) -> str:
                return condition(found, by_verdict[verdict]) or "是" if verdict in by_verdict else "—"

            current = "—"
            if truth:
                agree = by_verdict.get(truth)
                current = f"{VERDICT_WORDS.get(truth, truth)}，" + (f"对应：{condition(found, agree)}" if agree else "没有任何理解与之一致")
            lines.append(f"| `{name}` | {cell('PASS')} | {cell('NG')} | {cell('无结论')} | {current} |")
            odd = [verdict for verdict in by_verdict if verdict not in ("PASS", "NG", "无结论")]
            for verdict in odd:
                lines.append(f"\n`{name}`：{VERDICT_WORDS[verdict]}（{condition(found, by_verdict[verdict])}），需要检查对它的描述。")
        lines.append("")

    if answers:
        lines += check_answers(found, gaps)
    if found.rewrites:
        lines.append("## 建议改写的条款原文\n")
        lines += [f"> {rewrite}\n" for rewrite in found.rewrites]
    questions = [found.question.get(position, MISSING) for position in range(len(found.ids)) if position not in found.answered]
    questions += [question for _, pairs in found.undefined for question, answer in pairs if not answer]
    if questions:
        lines.append("## 需要回答的问题\n" if not answers else "## 还需要回答的问题\n")
        lines += [f"{index}. {question}" for index, question in enumerate(questions, 1)]
        lines.append("")
    if found.requirements:
        lines.append("## 对检查程序的要求\n")
        lines += [f"- {requirement}" for requirement in found.requirements]
        lines.append("")
    return "\n".join(lines)
