"""Check clause ontologies with ROBOT: source alignment, OWL 2 DL profile, consistency, unsatisfiable classes,
probes, reading combinations, completeness of disjoint groups and real-file verdicts.

    python3 check.py CLAUSE.ofn [MORE.ofn ...] [--out DIR]

Several inputs are merged first, so conflicts between clauses show up. A class whose local name starts
with `Probe_` names a situation that should not exist; a probe that is still satisfiable is a finding.
Conventions the script understands (see SKILL.md):

- `Annotation(:clause "path/rules.json#ID")` on the ontology and `:quote` annotations holding verbatim
  clause fragments: every part of the clause text must be quoted somewhere.
- `Point__<n><L>` classes are reading L of ambiguity n for the undefined class `Point`; every combination
  of readings becomes its own copy of the ontology, compared through `:Pass` / `:Fail`.
- `DisjointClasses` whose members share a named parent gets a probe for "in the parent but in none of them".
- A `:quote` may carry `Annotation(:limit "predicate"|"open-world"|"output")` for a fragment the ontology cannot
  state as written, and `Annotation(:open "...")` for each situation the clause leaves undefined in that
  predicate (`"无"` when it defines all). A predicate's class must sit in an exhaustive or probed disjoint group.
- Classes carrying `:case "file name"` (and optionally `:truth "PASS"|"NG"`) describe a real file's closed facts;
  each is classified per combination. Cases are classes, not individuals: an ABox makes HermiT minutes slower.

The full ROBOT reports and the expanded ontology stay in --out (default: next to the first input).
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
        for start in starts:
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
TECH_WORDS = ("本体", "公理", "探针", "推理", "可满足", "读法", "类名", "类型表达式", "OWL", "ROBOT", "HermiT", "Probe", "__", ":")


def lint(text: str, kind: str, allowed: set[str]) -> list[str]:
    """Plain-language problems in a text that goes into the report."""
    problems = [f"有技术词“{word}”" for word in TECH_WORDS if word in text]
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path, help="plain-language report (default: <clause>.report.md for one input, report.md beside merged inputs)")
    args = parser.parse_args(argv)
    report_path: Path = args.report or (args.inputs[0].with_suffix(".report.md") if len(args.inputs) == 1 else args.inputs[0].parent / "report.md")
    files: list[Path] = args.inputs
    out: Path = args.out or files[0].with_suffix(".robot")
    out.mkdir(parents=True, exist_ok=True)

    try:
        ontologies = [parse(path) for path in files]
    except (SyntaxError, IndexError) as error:
        print(f"读取失败（语法错误）：{error}")
        return 2

    findings = 0
    found = Found()
    for path, ontology in zip(files, ontologies):
        problems, clause, spans = alignment(path, ontology)
        if clause:
            found.clauses.append(clause)
        found.unaligned += spans
        findings += len(problems)
        for problem in problems:
            print(f"[原文对齐] {path.name}：{problem}")
        if not problems:
            print(f"[原文对齐] {path.name}：原文全部写进本体")

    base = [axiom for ontology in ontologies for axiom in ontology.axioms]
    classes = {node.args[0].args[0] for node in base if node.head == "Declaration" and node.args[0].head == "Class"} - {THING, NOTHING}
    original_probes = {iri for iri in classes if local(iri).startswith("Probe_")}

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
        missing = set(letters[ids.index(ambiguity_of[point])]) - set(readings)
        if missing:
            print(f"[读法] {local(point)} 缺少读法 {'、'.join(sorted(missing))}：同一处歧义的每个类都要有全部读法")
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
    plain = {name: dict(annotation_values(base, name)) for name in ("issue", "question", "label", "says", "chosen", "answered", "contradiction")}
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
    found.rewrites = [text for ontology in ontologies for _, text in annotation_values(ontology.annotations, "rewrite")]

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

    # Each ambiguity is shown by an example: a concrete file (class Example__<n>, closed facts like a real file)
    # on which its readings must give opposite verdicts. That check is what catches a broken formalization.
    examples: dict[str, str] = {}
    for iri in classes:
        match = EXAMPLE.search(iri.strip("<>"))
        if match:
            examples[match.group(1)] = iri
    for position, i in enumerate(ids):
        if i in examples and examples[i] in plain["says"]:
            found.example[position] = plain["says"][examples[i]]

    # What the report needs from the writer, checked before reasoning: every ambiguity and reading described.
    texts: list[tuple[str, str, str, str | None]] = []  # (where, annotation, kind, text)
    for position, i in enumerate(ids):
        where = f"歧义 {i}（{'、'.join(local(p) for p in points if ambiguity_of[p] == i)}）"
        texts += [(where, ":issue", "title", found.issue.get(position)), (where, ":question", "question", found.question.get(position))]
        if position not in found.answered:
            texts.append((f"{where} 的例子 Example__{i}", ":says", "sentence", found.example.get(position)))
        for letter in found.offered[position]:
            texts += [(f"{where} 读法 {letter}", "rdfs:label", "label", found.label.get((position, letter))), (f"{where} 读法 {letter}", ":says", "sentence", found.says.get((position, letter)))]
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

    def define(probe: str, expression: Node) -> list[Node]:
        return [Node("Declaration", [Node("Class", [probe])]), Node("EquivalentClasses", [probe, expression])]

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

    verdicts = {name: [iri for iri in classes if local(iri) == name] for name in ("Pass", "Fail")}
    divergent: dict[str, tuple] = {}
    entail: dict[str, tuple] = {}
    cases = {subject: text for subject, text in annotation_values(base, "case")}
    classified = [*cases, *examples.values()]
    truths = {subject: text for subject, text in annotation_values(base, "truth")}
    if len(verdicts["Pass"]) == 1 and len(verdicts["Fail"]) == 1:
        passed, failed = verdicts["Pass"][0], verdicts["Fail"][0]
        for group in [group for group in groups if len(group) == 2]:
            position = next(i for i in range(len(ids)) if group[0][i] != group[1][i])
            for combo, other in (group, group[::-1]):
                probe = f"<{GEN}Diverge{len(divergent) + 1}>"
                divergent[probe] = (position, combo[position], other[position], combo)
                groups[group] += define(probe, Node("ObjectIntersectionOf", [names[combo][passed], names[other][failed]]))
        for subject, combo, verdict in itertools.product(classified, combos, (passed, failed)):
            probe = f"<{GEN}Entail{len(entail) + 1}>"
            entail[probe] = (subject, combo, verdict == passed)
            groups[home[combo]] += define(probe, Node("ObjectIntersectionOf", [names[combo][subject], Node("ObjectComplementOf", [names[combo][verdict]])]))
    elif ids or cases:
        print("[读法/文件] 需要恰好一个 :Pass 和一个 :Fail 类才能比较读法和给文件分类，已跳过")

    targets = []
    for number, (group, extra) in enumerate(groups.items(), 1):
        target = out / (f"expanded-{number}.ofn" if len(groups) > 1 else "expanded.ofn")
        write(target, "<https://ppap.example/rules/expanded>", shared + [axiom for combo in group for axiom in copies[combo]] + extra)
        targets.append(target)

    profile_report = out / "profile.txt"
    profile_report.unlink(missing_ok=True)
    profile = robot("validate-profile", "--profile", "DL", "--input", str(targets[0]), "--output", str(profile_report))
    if not profile_report.exists():
        print("读取失败（语法错误）：\n" + said(profile))
        return 2
    if profile.returncode:
        # HermiT only reasons over OWL 2 DL; results past this point would not mean anything.
        print("[超出 OWL 2 DL] 先改写到 DL 之内，或把这部分记为表达局限：")
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
                print(f"[整体矛盾] {target.name} 不一致，无法继续推理（条款或真实文件的描述自相矛盾）。解释用：")
                print(f"{ROBOT} explain --reasoner hermit --input {target} -M inconsistency --explanation <输出.md>")
                return 1
            listed = {f"<{iri}>" for iri in UNSAT.findall(log)}
            counted = UNSAT_COUNT.search(log)
            if (reasoned.returncode and not counted) or (counted and int(counted.group(1)) != len(listed)):
                print(f"推理失败（{target.name}）：\n" + said(reasoned))
                return 2
            unsat |= listed
    explain = f"{ROBOT} explain --reasoner hermit --input {targets[0]} -M entailment --axiom '<类 IRI> SubClassOf owl:Nothing' --explanation <输出.md>"

    def where(combo_set: set[tuple]) -> str:
        if not ids:
            return ""
        return "所有组合" if len(combo_set) == len(combos) else show(compress(combo_set, letters), ids, letters)

    generated = set(outside) | set(divergent) | set(entail) | set(cases) | set(examples.values())
    generated |= set(points) | {reading for readings in points.values() for reading in readings.values()}
    contradictions: dict[str, set[tuple]] = {}
    for combo, mapping in names.items():
        for iri in classes - original_probes - generated:
            if mapping[iri] in unsat:
                contradictions.setdefault(iri, set()).add(combo)
    unchecked = 0
    for iri, combo_set in sorted(contradictions.items()):
        findings += 1
        confirmed = plain["contradiction"].get(iri)
        if confirmed:
            found.contradictions.append((confirmed, combo_set))
            texts.append((local(iri), ":contradiction", "phrase", confirmed))
            print(f"[自相矛盾] {local(iri)}：已确认是条款本身的矛盾" + (f"（{where(combo_set)}）" if ids else ""))
        else:
            unchecked += 1
            scope = f"（{where(combo_set)}）" if ids else ""
            print(f"[待查不可满足] {local(iri)}：这个类不可能有成员{scope}。用 {explain} 查清：写法错了就改；是条款本身的矛盾就在这个类上写 :contradiction")
    if unchecked:
        found.incomplete.append(f"有 {unchecked} 处推理结果还没查清是条款本身的矛盾还是分析写错了")
    if not contradictions:
        print("[不可满足的类] 无")

    for iri in sorted(original_probes, key=local):
        open_in = {combo for combo in combos if names[combo][iri] not in unsat}
        if open_in:
            findings += 1
            found.gaps.append((plain["says"].get(iri, MISSING), open_in))
            texts.append((local(iri), ":says", "phrase", plain["says"].get(iri)))
            print(f"[探针 仍可能] :{local(iri)}：条款没有排除这种情况" + (f"（{where(open_in)}）" if ids else ""))
        else:
            print(f"[探针 已排除] :{local(iri)}")
    if not original_probes:
        print("[探针] 没有声明 Probe_ 类")

    for probe, (parent, members) in outside.items():
        open_in = {combo for combo in combos if names[combo][probe] not in unsat}
        group = "、".join(local(member) for member in members)
        if open_in:
            findings += 1
            labels = "、".join(f"“{plain['label'].get(member, MISSING)}”" for member in members)
            found.gaps.append((f"原文没说{plain['label'].get(parent, MISSING)}只有{labels}这几种，其他情况", open_in))
            texts += [(local(member), "rdfs:label", "phrase", plain["label"].get(member)) for member in [parent, *members]]
            print(f"[完备性] {local(parent)} 中可能有不属于 {group} 任何一类的情况" + (f"（{where(open_in)}）" if ids else "") + "：原文是否穷尽？穷尽就写成 DisjointUnion，否则记缺陷")
        else:
            print(f"[完备性] {group} 穷尽 {local(parent)}")

    for position, i in enumerate(ids):
        for a, b in itertools.permutations(letters[position], 2):
            contexts = {combo for probe, (p, x, y, combo) in divergent.items() if (p, x, y) == (position, a, b) and probe not in unsat}
            found.divergences[(position, a, b)] = contexts
            if contexts:
                findings += 1
                print(f"[歧义 {i}] {a} 判通过、{b} 判不通过：可能（{where(contexts)}）")
            else:
                print(f"[歧义 {i}] {a} 判通过、{b} 判不通过：已排除")

    def verdicts_of(subject: str) -> dict[tuple, str]:
        result = {}
        for combo in combos:
            if names[combo][subject] in unsat:
                result[combo] = "事实与条款矛盾"
                continue
            passes = next(probe for probe, value in entail.items() if value == (subject, combo, True)) in unsat
            fails = next(probe for probe, value in entail.items() if value == (subject, combo, False)) in unsat
            result[combo] = "矛盾" if passes and fails else "PASS" if passes else "NG" if fails else "无结论"
        return result

    for position, i in enumerate(ids):
        if position in found.answered or not entail:
            continue
        if i not in examples:
            findings += 1
            found.incomplete.append(f"第 {position + 1} 处歧义还没有例子来验证")
            print(f"[例子缺项] 歧义 {i}：缺 Example__{i}，写一个让几种理解结论相反的具体文件情况")
            continue
        example = verdicts_of(examples[i])
        flips = any({example[combo], example[combo[:position] + (other,) + combo[position + 1 :]]} == {"PASS", "NG"} for combo in combos for other in letters[position] if other != combo[position])
        if not flips:
            findings += 1
            found.incomplete.append(f"第 {position + 1} 处歧义的例子在几种理解下没有得出相反的结论，分析可能写错了")
            states = "；".join(f"{verdict} {where({c for c, v in example.items() if v == verdict})}" for verdict in sorted(set(example.values())))
            print(f"[例子不分歧] 歧义 {i}：Example__{i} 在各读法下 {states}。多半是形式化写错了（例子的事实没写全、读法没接到判定上），也可能这不是会改变结论的歧义")

    for subject, name in cases.items() if entail else []:
        by_combo = verdicts_of(subject)
        verdict_of: dict[str, set[tuple]] = {}
        for combo, verdict in by_combo.items():
            verdict_of.setdefault(verdict, set()).add(combo)
        found.files[name] = (truths.get(subject), by_combo)
        parts = [f"{verdict} {where(combo_set)}".strip() for verdict, combo_set in sorted(verdict_of.items())]
        truth = truths.get(subject)
        tail = ""
        if truth:
            agree = verdict_of.get(truth, set())
            tail = f"；真值 {truth}，" + (f"与 {where(agree) or '本体'} 一致" if agree else "没有读法与之一致")
        print(f"[文件] {name}：{'；'.join(parts)}{tail}")

    # One issue is written once: either as an ambiguity with readings, or as an undefined situation (:open).
    ambiguity_quotes = {re.sub(r"\s+", "", text) for subject, text in annotation_values(base, "quote") if subject in points or any(subject in readings.values() for readings in points.values())}
    for quote, pairs in found.undefined:
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

    text = render(found)
    report_path.write_text(text, encoding="utf-8")
    allowed = {word.casefold() for clause in found.clauses for word in ASCII_WORD.findall(clause.text)}
    allowed |= {word.casefold() for name in found.files for word in ASCII_WORD.findall(name)}
    for where, annotation, kind, value in texts:
        if value is None:
            findings += 1
            print(f"[白话缺项] {where}：缺 {annotation}")
            continue
        for problem in lint(value, kind, allowed):
            findings += 1
            print(f"[白话质量] {where} {annotation}「{value}」：{problem}")
    if found.answered and any(answer == MISSING for answer in found.answered.values()):
        findings += 1
        print("[白话缺项] 有 :chosen 的歧义没写 :answered（用户的原话）")
    print(f"报告：{report_path}")
    print(f"完整推理文件：{out}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
