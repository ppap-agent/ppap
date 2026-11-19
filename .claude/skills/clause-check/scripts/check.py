"""Check one clause's formalization with ROBOT (HermiT) and write its plain-language report.

    python3 check.py CLAUSE.ofn [--out DIR] [--report FILE]

The writer formalizes what the clause says; the script derives everything else (see SKILL.md, 约定速查):

- `:Judged` is what the clause judges; `:PassWhen` / `:FailWhen` are the conditions under which the clause itself
  says pass / fail (`owl:Nothing` when it says none). Pass and fail are NOT declared disjoint: a thing meeting both
  is the clause's own conflict. Every verdict is derived: pass, fail, no verdict (a gap) or both (a conflict).
- `Point__<n><L>` classes are reading L of ambiguity n for the undefined class `Point`; every combination of
  readings gets its own copy of the classes that depend on a point. Two readings are a real ambiguity only when
  some thing gets different verdicts under them; `Example__<n>` is a concrete thing that shows it.
- Gaps and conflicts are searched for by the script in every combination; the writer describes them as
  `Gap__<k>` / `Conflict__<k>`, and the script checks each description really is one and that together they
  cover all of them.
- `:clause` / `:quote` align the ontology with the clause text; `:limit` / `:open` / `:says` mark what OWL cannot
  state; classes carrying `:case "file name"` describe real files with closed facts (classes, not individuals:
  an ABox makes HermiT minutes slower).

Exit status: 0 when the check is complete (findings are in the report), 1 when it is not (the report says so at
the top), 2 when the input could not be used.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ofn import NOTHING, THING, Node, literal_text, local, parse, rename, walk, write
from report import MISSING, Clause, Found, compress, render

ROBOT = Path(__file__).resolve().with_name("robot")
UNSAT = re.compile(r"unsatisfiable: (\S+)")
UNSAT_COUNT = re.compile(r"There are (\d+) unsatisfiable classes")
READING = re.compile(r"^(.+)__(\d+)([A-Z])$")
EXAMPLE = re.compile(r"[#/]Example__(\d+)$")
GAP = re.compile(r"[#/]Gap__(\d+)$")
CONFLICT = re.compile(r"[#/]Conflict__(\d+)$")
PUNCTUATION = set("，。；;？?、：:,.!！（）()“”\"'‘’《》【】[] 　")
GEN = "urn:clause-check#"


def robot(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROBOT), *args], capture_output=True, text=True)


def said(run: subprocess.CompletedProcess) -> str:
    """ROBOT prints its errors on stdout; drop the generic help lines."""
    lines = (run.stdout + run.stderr).splitlines()
    return "\n".join(line for line in lines if line.strip() and not line.startswith(("Use the -vvv", "Use the --help", "For details see")))


def annotation_values(nodes, name: str):
    """(subject or None, text) for every annotation whose property's local name is `name`."""
    for item in (sub for node in nodes for sub in walk(node)):
        if not isinstance(item, Node):
            continue
        if item.head == "Annotation" and local(item.args[-2]) == name:
            yield None, literal_text(item.args[-1])
        elif item.head == "AnnotationAssertion" and local(item.args[-3]) == name:
            yield item.args[-2], literal_text(item.args[-1])


def alignment(path: Path, ontology) -> tuple[list[str], Clause | None, list[str]]:
    """(problems, the clause, unquoted spans) for how the ontology quotes its clause."""
    sources = [text for _, text in annotation_values(ontology.annotations, "clause")]
    if not sources:
        return ['没有 Annotation(:clause "<rules.json 路径>#<条款 id>")，无法对齐原文'], None, []
    file_name, _, rule_id = sources[0].partition("#")
    rules = next((parent / file_name for parent in [Path.cwd(), *path.resolve().parents] if (parent / file_name).exists()), None)
    if rules is None:
        return [f"找不到规则文件 {file_name}"], None, []
    document = json.loads(rules.read_text(encoding="utf-8"))
    rule = next((rule for rule in document["rules"] if rule["id"] == rule_id), None)
    if rule is None:
        return [f"{file_name} 里没有条款 {rule_id}"], None, []
    info = Clause(document.get("title", ""), rule_id, rule.get("category", ""), rule["rule_to_check"])
    text = re.sub(r"\s+", "", rule["rule_to_check"])
    covered = [False] * len(text)
    problems, spans = [], []
    for _, quote in annotation_values(ontology.axioms + ontology.annotations, "quote"):
        fragment = re.sub(r"\s+", "", quote)
        starts = [match.start() for match in re.finditer(re.escape(fragment), text)] if fragment else []
        if not starts:
            problems.append(f"引用与原文不符：「{quote}」")
            continue
        # One quote covers one occurrence: a fragment the clause repeats must be quoted once per occurrence.
        free = [start for start in starts if not all(covered[start : start + len(fragment)])]
        start = (free or starts)[0]
        covered[start : start + len(fragment)] = [True] * len(fragment)
    for is_covered, group in itertools.groupby(enumerate(text), key=lambda pair: covered[pair[0]]):
        span = "".join(char for _, char in group)
        if not is_covered and set(span) - PUNCTUATION:
            problems.append(f"原文未写进本体：「{span}」")
            spans.append(span)
    return problems, info, spans


LIMIT_KINDS = {"predicate": "写不出的谓词", "open-world": "开放世界", "output": "对检查程序的要求"}


def limits(base: list[Node]) -> list[tuple[str | None, str, str | None, list[str], str | None]]:
    """(kind or None, quoted fragment, subject class or None, undefined situations, plain sentence or None)
    for every quote marked with :limit or :open."""
    found = []
    for item in (sub for node in base for sub in walk(node)):
        if not isinstance(item, Node) or item.head not in ("Annotation", "AnnotationAssertion"):
            continue
        assertion = item.head == "AnnotationAssertion"
        if local(item.args[-3 if assertion else -2]) != "quote":
            continue
        meta = [arg for arg in item.args if isinstance(arg, Node) and arg.head == "Annotation"]
        kinds = [literal_text(arg.args[-1]) for arg in meta if local(arg.args[-2]) == "limit"]
        situations = []
        for arg in meta:
            if local(arg.args[-2]) == "open":
                answers = [literal_text(n.args[-1]) for n in arg.args if isinstance(n, Node) and n.head == "Annotation" and local(n.args[-2]) == "answered"]
                situations.append((literal_text(arg.args[-1]), answers[0] if answers else None))
        says = next((literal_text(arg.args[-1]) for arg in meta if local(arg.args[-2]) == "says"), None)
        if kinds or situations:
            found.append((kinds[0] if kinds else None, literal_text(item.args[-1]), item.args[-2] if assertion else None, situations, says))
    return found


ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
TECH_WORDS = ("本体", "公理", "探针", "推理", "可满足", "读法", "谓词", "开放世界", "形式化", "类名", "类型表达式", "OWL", "ROBOT", "HermiT", "Probe", "__")
NAME_LIKE = re.compile(r":[A-Za-z_]")  # a prefixed name such as :Signed; "10:30" is fine


def lint(text: str, kind: str, allowed: set[str]) -> list[str]:
    """Plain-language problems in a text that goes into the report."""
    problems = [f"有技术词“{word}”" for word in TECH_WORDS if word in text]
    if NAME_LIKE.search(text):
        problems.append("有本体里的名字写法（如 :Signed）")
    problems += [f"有英文标识符“{word}”" for word in ASCII_WORD.findall(text) if word.casefold() not in allowed]
    if kind == "rewrite":
        return problems
    ending = text.rstrip()[-1:] if text.strip() else ""
    if kind == "question" and ending not in "？?":
        problems.append("问题要以“？”结尾")
    if kind == "sentence" and ending != "。":
        problems.append("要写成以“。”结尾的完整句子")
    if kind in ("title", "label", "phrase") and ending in "。？?":
        problems.append("是短语，不加句末标点")
    if kind == "label" and len(text) > 12:
        problems.append("短名超过 12 个字，放进条件句会难读")
    return problems


def mentions(axiom: Node, iris: set[str]) -> bool:
    return any(isinstance(item, str) and item in iris for item in walk(axiom))


def show(patterns: list[tuple], ids: list[str], letters: list[list[str]]) -> str:
    def part(i: int, chosen) -> str:
        return f"{ids[i]}*" if len(chosen) == len(letters[i]) else ids[i] + "|".join(sorted(chosen))

    return "；".join("·".join(part(i, chosen) for i, chosen in enumerate(pattern)) for pattern in patterns)


VERDICTS = ("PASS", "NG", "NONE", "BOTH")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clause", type=Path, help="the clause's .ofn file")
    parser.add_argument("--out", type=Path, help="reasoning files (default: <clause>.robot/)")
    parser.add_argument("--report", type=Path, help="plain-language report (default: <clause>.report.md)")
    args = parser.parse_args(argv)
    path: Path = args.clause
    report_path: Path = args.report or path.with_suffix(".report.md")
    out: Path = args.out or path.with_suffix(".robot")
    out.mkdir(parents=True, exist_ok=True)
    # A run that stops early must not leave an earlier report behind as if it were current.
    report_path.unlink(missing_ok=True)

    try:
        ontology = parse(path)
    except (SyntaxError, IndexError) as error:
        print(f"读取失败（语法错误）：{error}")
        return 2

    findings = 0
    found = Found()
    problems, clause, spans = alignment(path, ontology)
    if clause:
        found.clauses.append(clause)
    found.unaligned += spans
    findings += len(problems)
    for problem in problems:
        print(f"[原文对齐] {problem}")
    if not problems:
        print("[原文对齐] 原文全部写进本体")

    base = list(ontology.axioms)
    # The skill's own annotation properties (:quote, :says, ...) are declared here for the writer. Any other name
    # used without a declaration is reported by name; the DL check would only call it an undeclared entity.
    declared = {node.args[0].args[0] for node in base if node.head == "Declaration"}
    annotation_properties = {
        item.args[-3] if item.head == "AnnotationAssertion" else item.args[-2] for node in base for item in walk(node) if isinstance(item, Node) and item.head in ("Annotation", "AnnotationAssertion")
    }
    for name in sorted(annotation_properties - declared):
        if not name.startswith("<http://www.w3.org/"):
            base.append(Node("Declaration", [Node("AnnotationProperty", [name])]))
            declared.add(name)
    unknown = sorted({local(item) for node in base for item in walk(node) if isinstance(item, str) and item.startswith("<") and not item.startswith("<http://www.w3.org/") and item not in declared})
    if unknown:
        print(f"[约定] 用了没声明的名字：{'、'.join(unknown)}。每个类和属性都要写 Declaration")
        return 2
    classes = {node.args[0].args[0] for node in base if node.head == "Declaration" and node.args[0].head == "Class"} - {THING, NOTHING}
    special = {name: next((iri for iri in classes if local(iri) == name), None) for name in ("Judged", "PassWhen", "FailWhen")}
    missing = [name for name, iri in special.items() if iri is None]
    if missing:
        print(f"[约定] 缺少 {'、'.join(':' + name for name in missing)}：先写被判定的对象和原文给出的通过、不通过条件")
        return 2
    judged, pass_when, fail_when = special["Judged"], special["PassWhen"], special["FailWhen"]
    if any(local(iri).startswith("Probe_") for iri in classes):
        print("[约定] Probe_ 已不再使用：冲突和缺口由脚本自己查，查到后写成 Gap__<k> / Conflict__<k>")
        return 2

    # Completeness: a disjoint group under a named parent should either cover the parent or be probed.
    parents: dict[str, set[str]] = {}
    for node in base:
        if node.head == "SubClassOf" and all(isinstance(arg, str) for arg in node.args[-2:]):
            parents.setdefault(node.args[-2], set()).add(node.args[-1])
    outside: dict[str, tuple[str, list[str]]] = {}
    for node in base:
        members = [arg for arg in node.args if isinstance(arg, str)]
        if node.head != "DisjointClasses" or len(members) != len(node.args) or len(members) < 2:
            continue
        for parent in set.intersection(*(parents.get(member, set()) for member in members)):
            probe = f"<{GEN}Outside{len(outside) + 1}>"
            outside[probe] = (parent, members)
            classes.add(probe)
            base += [
                Node("Declaration", [Node("Class", [probe])]),
                Node("EquivalentClasses", [probe, Node("ObjectIntersectionOf", [parent, *(Node("ObjectComplementOf", [m]) for m in members)])]),
            ]

    exhaustive = {member for node in base if node.head == "DisjointUnion" for member in node.args[1:] if isinstance(member, str)}
    probed = {member for _, members in outside.values() for member in members}
    for kind, quote, subject, situations, says in limits(base):
        undefined = [(text, answer) for text, answer in situations if text != "无"]
        if undefined:
            found.undefined.append((quote, undefined))
        if kind is None:
            continue
        if kind == "output":
            found.requirements.append(says or f"原文要求：{quote}。")
        if kind == "open-world":
            found.requirements.append(says or f"判断「{quote}」时，要能确认文件里确实没有，才能判“不存在”。")
        if kind not in LIMIT_KINDS:
            findings += 1
            print(f"[表达局限] 「{quote}」：未知类别 {kind}，只能是 {'、'.join(LIMIT_KINDS)}")
            continue
        line = f"[表达局限] {LIMIT_KINDS[kind]}「{quote}」"
        if kind == "predicate":
            line += f" → {local(subject) if subject else '（未挂到类上）'}"
            if not subject or subject not in exhaustive | probed:
                findings += 1
                line += "；抽象出的类不在穷尽或受完备性检查的互斥组里"
            if not situations:
                findings += 1
                line += '；没有列出原文没定义的情况（全部有定义就写 :open "无"）'
            elif undefined:
                findings += sum(1 for _, answer in undefined if not answer)
                line += "；原文没定义：" + "；".join(text + ("（已答复）" if answer else "") for text, answer in undefined)
            else:
                line += "；原文定义了全部情况"
        print(line)

    # Readings: Point__<n><L>.
    points: dict[str, dict[str, str]] = {}
    ambiguity_of: dict[str, str] = {}
    for iri in sorted(classes):
        match = READING.match(iri.strip("<>"))
        if match:
            point = f"<{match.group(1)}>"
            points.setdefault(point, {})[match.group(3)] = iri
            if ambiguity_of.setdefault(point, match.group(2)) != match.group(2):
                print(f"[读法] {local(point)} 同时属于两处歧义，请拆成两个类")
                return 2
    ids = sorted(set(ambiguity_of.values()), key=int)
    letters = [sorted({letter for point, readings in points.items() if ambiguity_of[point] == i for letter in readings}) for i in ids]
    for point, readings in points.items():
        gap = set(letters[ids.index(ambiguity_of[point])]) - set(readings)
        if gap:
            print(f"[读法] {local(point)} 缺少读法 {'、'.join(sorted(gap))}：同一处歧义的每个歧义点都要有全部读法")
            return 2
    if ids:
        print("[读法] " + "；".join(f"歧义 {i}：{'、'.join(ls)}" for i, ls in zip(ids, letters)) + f"；共 {len(list(itertools.product(*letters)))} 种组合")

    # Only classes whose meaning depends on a reading are copied per combination; facts stay shared,
    # so the copies describe the same file and can be compared.
    dependent = set(points)
    changed = True
    while changed:
        changed = False
        for node in base:
            if node.head == "EquivalentClasses" and isinstance(node.args[-2], str) and mentions(node.args[-1], dependent):
                grown = {node.args[-2]}
            elif node.head == "SubClassOf":
                sub, sup = node.args[-2], node.args[-1]
                grown = {sub} if isinstance(sub, str) and mentions(sup, dependent) else set()
                grown |= {sup} if isinstance(sup, str) and mentions(sub, dependent) else set()
            else:
                continue
            if grown - dependent:
                dependent |= grown
                changed = True

    # Answers written back: `:chosen "A"` (and the user's words in `:answered`) on any point of an ambiguity.
    plain = {name: dict(annotation_values(base, name)) for name in ("issue", "question", "label", "says", "chosen", "answered")}
    found.offered = [list(ls) for ls in letters]
    for point in points:
        position = ids.index(ambiguity_of[point])
        chosen = plain["chosen"].get(point)
        if chosen is None:
            continue
        if chosen not in found.offered[position]:
            print(f"[答复] {local(point)} 选了不存在的读法 {chosen}")
            return 2
        letters[position] = [chosen]
        found.answered[position] = plain["answered"].get(point, MISSING)
    if found.answered:
        print("[答复] " + "；".join(f"歧义 {ids[p]} 取 {letters[p][0]}" for p in sorted(found.answered)))
    found.rewrites = [text for _, text in annotation_values(ontology.annotations, "rewrite")]

    combos = list(itertools.product(*letters))
    found.ids, found.letters, found.combos = ids, letters, combos
    for point, readings in points.items():
        position = ids.index(ambiguity_of[point])
        for name, target in (("issue", found.issue), ("question", found.question)):
            if point in plain[name]:
                target.setdefault(position, plain[name][point])
        for letter, reading in readings.items():
            for name, target in (("label", found.label), ("says", found.says)):
                if reading in plain[name]:
                    target.setdefault((position, letter), plain[name][reading])

    def numbered(pattern: re.Pattern) -> dict[str, str]:
        return {match.group(1): iri for iri in classes if (match := pattern.search(iri.strip("<>")))}

    examples, gaps, conflicts = numbered(EXAMPLE), numbered(GAP), numbered(CONFLICT)
    cases = {subject: text for subject, text in annotation_values(base, "case")}
    for position, i in enumerate(ids):
        if i in examples and examples[i] in plain["says"]:
            found.example[position] = plain["says"][examples[i]]

    # What the report needs from the writer: every ambiguity, reading, example and described situation in words.
    texts: list[tuple[str, str, str, str | None]] = []  # (where, annotation, kind, text)
    for position, i in enumerate(ids):
        where = f"歧义 {i}（{'、'.join(local(p) for p in points if ambiguity_of[p] == i)}）"
        texts += [(where, ":issue", "title", found.issue.get(position)), (where, ":question", "question", found.question.get(position))]
        if position not in found.answered:
            texts.append((f"{where} 的例子 Example__{i}", ":says", "sentence", found.example.get(position)))
        for letter in found.offered[position]:
            texts += [(f"{where} 读法 {letter}", "rdfs:label", "label", found.label.get((position, letter))), (f"{where} 读法 {letter}", ":says", "sentence", found.says.get((position, letter)))]
    for k, iri in sorted({**{f"Gap__{k}": v for k, v in gaps.items()}, **{f"Conflict__{k}": v for k, v in conflicts.items()}}.items()):
        texts.append((k, ":says", "phrase", plain["says"].get(iri)))
    for _, quote, _, situations, says in limits(base):
        texts += [(f"「{quote}」", ":open", "question", text) for text, _ in situations if text != "无"]
        if says:
            texts.append((f"「{quote}」", ":says", "sentence", says))
    texts += [("条款", ":rewrite", "rewrite", text) for text in found.rewrites]

    names: dict[tuple, dict[str, str]] = {}
    shared: list[Node] = [axiom for axiom in base if not mentions(axiom, dependent)]
    copies: dict[tuple, list[Node]] = {}
    for combo in combos:
        suffix = "__k" + "".join(i + letter for i, letter in zip(ids, combo)) if ids else ""
        mapping = {iri: f"{iri[:-1]}{suffix}>" if iri in dependent else iri for iri in classes}
        for point, readings in points.items():
            mapping[point] = mapping[readings[combo[ids.index(ambiguity_of[point])]]]
        names[combo] = mapping
        copies[combo] = [rename(axiom, mapping) for axiom in base if mentions(axiom, dependent)]

    def region(combo: tuple, verdict: str) -> Node:
        """The judged things that get `verdict` under this combination, from the clause's own conditions."""
        passes, fails = names[combo][pass_when], names[combo][fail_when]
        parts = {
            "PASS": [passes, Node("ObjectComplementOf", [fails])],
            "NG": [fails, Node("ObjectComplementOf", [passes])],
            "NONE": [Node("ObjectComplementOf", [passes]), Node("ObjectComplementOf", [fails])],
            "BOTH": [passes, fails],
        }[verdict]
        return Node("ObjectIntersectionOf", [names[combo][judged], *parts])

    # All copies in one ontology make every tableau node carry every copy's axioms (minutes).
    # So each comparison gets its own small ontology: a pair of combinations differing in one reading.
    groups: dict[tuple, list[Node]] = {}
    for left, right in itertools.combinations(combos, 2):
        if sum(a != b for a, b in zip(left, right)) == 1:
            groups[(left, right)] = []
    for combo in combos:
        if not any(combo in group for group in groups):
            groups[(combo,)] = []
    home = {combo: next(group for group in groups if combo in group) for combo in combos}
    probes: dict[str, tuple] = {}  # generated class -> what it asks

    def ask(group: tuple, meaning: tuple, expression: Node) -> None:
        probe = f"<{GEN}Q{len(probes) + 1}>"
        probes[probe] = meaning
        groups[group] += [Node("Declaration", [Node("Class", [probe])]), Node("EquivalentClasses", [probe, expression])]

    def union(items: list[str]) -> Node | str:
        return items[0] if len(items) == 1 else Node("ObjectUnionOf", items)

    for combo in combos:
        group, mapping = home[combo], names[combo]
        passes, fails = mapping[pass_when], mapping[fail_when]
        ask(group, ("region", combo, "NONE"), region(combo, "NONE"))
        ask(group, ("region", combo, "BOTH"), region(combo, "BOTH"))
        for kind, described, verdict in (("gap", gaps, "NONE"), ("conflict", conflicts, "BOTH")):
            if described:
                rest = [Node("ObjectComplementOf", [mapping[iri]]) for iri in described.values()]
                ask(group, ("uncovered", combo, kind), Node("ObjectIntersectionOf", [region(combo, verdict), *rest]))
            for number, iri in described.items():
                # A gap has no verdict at all; a conflict has both. Either must also be possible.
                wrong = union([passes, fails]) if kind == "gap" else Node("ObjectComplementOf", [Node("ObjectIntersectionOf", [passes, fails])])
                ask(group, ("possible", combo, kind, number), Node("ObjectIntersectionOf", [mapping[judged], mapping[iri]]))
                ask(group, ("misdescribed", combo, kind, number), Node("ObjectIntersectionOf", [mapping[iri], wrong]))
        for subject in [*cases, *examples.values()]:
            thing = mapping[subject]
            ask(group, ("subject", combo, subject), thing)
            for test, expression in (("pass", Node("ObjectComplementOf", [passes])), ("fail", Node("ObjectComplementOf", [fails])), ("nopass", passes), ("nofail", fails)):
                ask(group, ("entail", combo, subject, test), Node("ObjectIntersectionOf", [thing, expression]))
    for group in [group for group in groups if len(group) == 2]:
        position = next(i for i in range(len(ids)) if group[0][i] != group[1][i])
        left, right = group
        for first, second in itertools.permutations(VERDICTS, 2):
            ask(group, ("diverge", position, left, right, first, second), Node("ObjectIntersectionOf", [region(left, first), region(right, second)]))

    targets = []
    for number, (group, extra) in enumerate(groups.items(), 1):
        target = out / (f"expanded-{number}.ofn" if len(groups) > 1 else "expanded.ofn")
        write(target, "<https://ppap.example/rules/expanded>", shared + [axiom for combo in group for axiom in copies[combo]] + extra)
        targets.append(target)
    for stale in out.glob("expanded*.ofn"):
        if stale not in targets:
            stale.unlink()

    profile_report = out / "profile.txt"
    profile_report.unlink(missing_ok=True)
    profile = robot("validate-profile", "--profile", "DL", "--input", str(targets[0]), "--output", str(profile_report))
    if not profile_report.exists():
        print("读取失败（语法错误）：\n" + said(profile))
        return 2
    if profile.returncode:
        # HermiT only reasons over OWL 2 DL; results past this point would not mean anything.
        print("[超出 OWL 2 DL] 多半是用了没声明的名字（每个类、属性、注解属性都要 Declaration），或用了 DL 不允许的写法：")
        print(profile_report.read_text(encoding="utf-8").strip())
        return 1
    print("[OWL 2 DL] 符合")

    # Only list the unsatisfiable classes; explaining every expected-unsatisfiable generated probe takes minutes.
    def reason(target: Path) -> tuple[Path, subprocess.CompletedProcess]:
        return target, robot("reason", "--reasoner", "hermit", "--input", str(target), "--dump-unsatisfiable", str(target.with_suffix(".unsat.ofn")), "-vv")

    unsat: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) // 2)) as pool:
        for target, reasoned in pool.map(reason, targets):
            log = reasoned.stdout + reasoned.stderr
            if "inconsistent" in log.lower():
                print(f"[整体矛盾] {target.name} 不一致，无法推理：词汇或真实文件的描述自相矛盾（这是写法问题，不是条款的冲突）。解释用：")
                print(f"{ROBOT} explain --reasoner hermit --input {target} -M inconsistency --explanation <输出.md>")
                return 1
            listed = {f"<{iri}>" for iri in UNSAT.findall(log)}
            counted = UNSAT_COUNT.search(log)
            if (reasoned.returncode and not counted) or (counted and int(counted.group(1)) != len(listed)):
                print(f"推理失败（{target.name}）：\n" + said(reasoned))
                return 2
            unsat |= listed
    explain = f"{ROBOT} explain --reasoner hermit --input {targets[0]} -M entailment --axiom '<类 IRI> SubClassOf owl:Nothing' --explanation <输出.md>"
    possible = {meaning for probe, meaning in probes.items() if probe not in unsat}

    def where(combo_set: set[tuple]) -> str:
        if not ids:
            return ""
        return "所有组合" if len(combo_set) == len(combos) else show(compress(combo_set, letters), ids, letters)

    # Vocabulary that can never apply is a writing error: the clause's own conflicts show up as "both verdicts" instead.
    readings = {reading for group in points.values() for reading in group.values()}
    vocabulary = classes - dependent - readings - set(points) - {pass_when, fail_when} - set(outside) - set(cases) - {*examples.values(), *gaps.values(), *conflicts.values()}
    empty = sorted(iri for iri in vocabulary if iri in unsat)
    if judged in unsat or any(names[combo][judged] in unsat for combo in combos):
        empty.append(judged)
    for iri in empty:
        findings += 1
        print(f"[写法错误] {local(iri)} 不可能有成员：词汇写得自相矛盾。用 {explain} 查清后改写")
    if empty:
        found.incomplete.append(f"有 {len(empty)} 处分析写得自相矛盾，还没改好")

    for probe, (parent, members) in outside.items():
        group = "、".join(local(member) for member in members)
        if probe not in unsat:
            findings += 1
            labels = "、".join(f"“{plain['label'].get(member, MISSING)}”" for member in members)
            whole = plain["label"].get(parent, MISSING)
            found.groups.append((f"{whole}有哪几种", f"{whole}除了{labels}，还可能是别的样子吗？是的话怎么判？"))
            texts += [(local(member), "rdfs:label", "phrase", plain["label"].get(member)) for member in [parent, *members]]
            print(f"[完备性] {local(parent)} 中可能有不属于 {group} 任何一类的情况：原文是否穷尽？穷尽就写成 DisjointUnion，否则记成原文没定义的情况")
        else:
            print(f"[完备性] {group} 穷尽 {local(parent)}")

    # Gaps and conflicts: found by the script in every combination, then described by the writer.
    for kind, described, verdict, title in (("gap", gaps, "NONE", "原文没有给出结论"), ("conflict", conflicts, "BOTH", "原文同时给出通过和不通过")):
        present = {combo for combo in combos if ("region", combo, verdict) in possible}
        if present:
            print(f"[{title}] 存在" + (f"（{where(present)}）" if ids else ""))
        uncovered = {combo for combo in present if not described or ("uncovered", combo, kind) in possible}
        if uncovered:
            findings += 1
            found.incomplete.append(f"还有{title}的情况没有写出来")
            scope = f"（{where(uncovered)}）" if ids else ""
            print(f"[{title}] 还有情况没写出来{scope}：补写 {'Gap' if kind == 'gap' else 'Conflict'}__<k>，直到覆盖全部")
        for number, iri in sorted(described.items()):
            holds = {combo for combo in combos if ("possible", combo, kind, number) in possible and ("misdescribed", combo, kind, number) not in possible}
            label = f"{'Gap' if kind == 'gap' else 'Conflict'}__{number}"
            if not holds:
                findings += 1
                found.incomplete.append(f"有一处写出的“{title}”的情况与条款对不上")
                print(f"[情况写错] {label}「{plain['says'].get(iri, '')}」在任何组合下都不是{title}的情况")
                continue
            findings += 1
            (found.gaps if kind == "gap" else found.conflicts).append((plain["says"].get(iri, MISSING), holds))
            print(f"[{title}] {label}：成立" + (f"（{where(holds)}）" if ids else ""))

    def verdicts_of(subject: str) -> dict[tuple, str]:
        result = {}
        for combo in combos:
            if ("subject", combo, subject) not in possible:
                result[combo] = "IMPOSSIBLE"
                continue
            holds = {test: ("entail", combo, subject, test) not in possible for test in ("pass", "fail", "nopass", "nofail")}
            if holds["pass"] and holds["fail"]:
                result[combo] = "BOTH"
            elif holds["pass"] and holds["nofail"]:
                result[combo] = "PASS"
            elif holds["fail"] and holds["nopass"]:
                result[combo] = "NG"
            elif holds["nopass"] and holds["nofail"]:
                result[combo] = "NONE"
            else:
                result[combo] = "OPEN"
        return result

    for position, i in enumerate(ids):
        kinds: dict[tuple, set] = {}
        for meaning in possible:
            if meaning[0] == "diverge" and meaning[1] == position:
                _, _, left, right, first, second = meaning
                kinds.setdefault((left[position], right[position], first, second), set()).add(left)
                kinds.setdefault((right[position], left[position], second, first), set()).add(right)
        found.divergences[position] = kinds
        if position in found.answered:
            continue
        if kinds:
            findings += 1
            print(f"[歧义 {i}] 几种读法会让同一份文件结论不同：" + "；".join(f"{a}{first}/{b}{second}（{where(c)}）" for (a, b, first, second), c in sorted(kinds.items()) if a < b))
        else:
            findings += 1
            found.incomplete.append(f"第 {position + 1} 处歧义的几种理解对任何文件结论都相同，不是会改变结论的歧义")
            print(f"[歧义 {i}] 几种读法对任何文件结论都相同：不是会改变结论的歧义，改成原文没定义的情况或删掉")
        if i not in examples:
            findings += 1
            found.incomplete.append(f"第 {position + 1} 处歧义还没有例子")
            print(f"[例子缺项] 歧义 {i}：缺 Example__{i}，写一个让几种理解结论不同的具体文件")
            continue
        example = verdicts_of(examples[i])
        if any(value in ("OPEN", "IMPOSSIBLE") for value in example.values()):
            findings += 1
            found.incomplete.append(f"第 {position + 1} 处歧义的例子事实没写全")
            print(f"[事实没写全] Example__{i}：有的组合下推不出结论或例子不可能存在，把例子的事实写封闭")
            continue
        flips = any(example[combo] != example[combo[:position] + (other,) + combo[position + 1 :]] for combo in combos for other in letters[position])
        if not flips:
            findings += 1
            found.incomplete.append(f"第 {position + 1} 处歧义的例子在几种理解下结论相同")
            print(f"[例子不分歧] Example__{i} 在各读法下结论相同：换一个例子，或查读法是否接到了通过、不通过的条件上")

    for subject, name in cases.items():
        by_combo = verdicts_of(subject)
        if any(value in ("OPEN", "IMPOSSIBLE") for value in by_combo.values()):
            findings += 1
            found.incomplete.append(f"`{name}` 的事实没写全")
            print(f"[事实没写全] {name}：有的组合下推不出结论或描述不可能成立，把文件的事实写封闭")
        found.files[name] = by_combo
        print(f"[文件] {name}：" + "；".join(f"{verdict} {where({c for c, v in by_combo.items() if v == verdict})}".strip() for verdict in sorted(set(by_combo.values()))))

    # One issue is written once: either as an ambiguity with readings, or as an undefined situation (:open).
    ambiguity_quotes = {re.sub(r"\s+", "", text) for subject, text in annotation_values(base, "quote") if subject in points or subject in readings}
    for quote, _ in found.undefined:
        if re.sub(r"\s+", "", quote) in ambiguity_quotes:
            findings += 1
            print(f"[重复] 「{quote}」既写成了歧义，又写了原文没说的情况；同一处问题只写一次")
    asked: dict[str, str] = {}
    for question in [found.question.get(position) for position in range(len(ids))] + [q for _, pairs in found.undefined for q, _ in pairs]:
        key = re.sub(r"[\s，。？?、“”「」]", "", question or "")
        if key and key in asked:
            findings += 1
            print(f"[重复] 问题「{question}」问了两次")
        asked.setdefault(key, question or "")

    allowed = {word.casefold() for clause in found.clauses for word in ASCII_WORD.findall(clause.text)}
    allowed |= {word.casefold() for name in found.files for word in ASCII_WORD.findall(name)}
    wording = 0
    for place, annotation, kind, value in texts:
        if value is None:
            wording += 1
            print(f"[白话缺项] {place}：缺 {annotation}")
            continue
        for problem in lint(value, kind, allowed):
            wording += 1
            print(f"[白话质量] {place} {annotation}「{value}」：{problem}")
    if found.answered and any(answer == MISSING for answer in found.answered.values()):
        wording += 1
        print("[白话缺项] 有 :chosen 的歧义没写 :answered（用户的原话）")
    if wording:
        found.incomplete.append(f"有 {wording} 处白话说明缺项或不合格")

    text = render(found)
    report_path.write_text(text, encoding="utf-8")
    print(f"报告：{report_path}" + ("（检查未完成，报告顶部已标出）" if found.incomplete or found.unaligned else ""))
    print(f"完整推理文件：{out}")
    return 1 if found.incomplete or found.unaligned else 0


if __name__ == "__main__":
    sys.exit(main())
