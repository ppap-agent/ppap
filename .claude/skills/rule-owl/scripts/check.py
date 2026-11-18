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

ROBOT = Path(__file__).with_name("robot")
UNSAT = re.compile(r"unsatisfiable: (\S+)")
UNSAT_COUNT = re.compile(r"There are (\d+) unsatisfiable classes")
READING = re.compile(r"^(.+)__(\d+)([A-Z])$")
PUNCTUATION = set("，。；;？?、：:,.!！（）()“”\"'‘’《》【】[] 　")
GEN = "urn:rule-owl#"


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


def alignment(path: Path, ontology) -> list[str]:
    """Problems with how the ontology quotes its clause; empty when every part of the clause is quoted."""
    sources = [text for _, text in annotation_values(ontology.annotations, "clause")]
    if not sources:
        return ['没有 Annotation(:clause "<rules.json 路径>#<条款 id>")，无法对齐原文']
    file_name, _, rule_id = sources[0].partition("#")
    rules = next((parent / file_name for parent in [Path.cwd(), *path.resolve().parents] if (parent / file_name).exists()), None)
    if rules is None:
        return [f"找不到规则文件 {file_name}"]
    clause = next((rule["rule_to_check"] for rule in json.loads(rules.read_text(encoding="utf-8"))["rules"] if rule["id"] == rule_id), None)
    if clause is None:
        return [f"{file_name} 里没有条款 {rule_id}"]
    text = re.sub(r"\s+", "", clause)
    covered = [False] * len(text)
    problems = []
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
    return problems


LIMIT_KINDS = {"predicate": "写不出的谓词", "open-world": "开放世界", "output": "对检查程序的要求"}


def limits(base: list[Node]) -> list[tuple[str, str, str | None, list[str]]]:
    """(kind, quoted fragment, subject class or None, undefined situations) for every quote marked with :limit."""
    found = []
    for item in (sub for node in base for sub in walk(node)):
        if not isinstance(item, Node) or item.head not in ("Annotation", "AnnotationAssertion"):
            continue
        assertion = item.head == "AnnotationAssertion"
        if local(item.args[-3 if assertion else -2]) != "quote":
            continue
        meta = [arg for arg in item.args if isinstance(arg, Node) and arg.head == "Annotation"]
        kinds = [literal_text(arg.args[-1]) for arg in meta if local(arg.args[-2]) == "limit"]
        if kinds:
            situations = [literal_text(arg.args[-1]) for arg in meta if local(arg.args[-2]) == "open"]
            found.append((kinds[0], literal_text(item.args[-1]), item.args[-2] if assertion else None, situations))
    return found


def mentions(axiom: Node, iris: set[str]) -> bool:
    return any(isinstance(item, str) and item in iris for item in walk(axiom))


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


def show(patterns: list[tuple], ids: list[str], letters: list[list[str]]) -> str:
    def part(i: int, chosen) -> str:
        return f"{ids[i]}*" if len(chosen) == len(letters[i]) else ids[i] + "|".join(sorted(chosen))

    return "；".join("·".join(part(i, chosen) for i, chosen in enumerate(pattern)) for pattern in patterns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    files: list[Path] = args.inputs
    out: Path = args.out or files[0].with_suffix(".robot")
    out.mkdir(parents=True, exist_ok=True)

    try:
        ontologies = [parse(path) for path in files]
    except (SyntaxError, IndexError) as error:
        print(f"读取失败（语法错误）：{error}")
        return 2

    findings = 0
    for path, ontology in zip(files, ontologies):
        problems = alignment(path, ontology)
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
    for kind, quote, subject, situations in limits(base):
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
            undefined = [text for text in situations if text != "无"]
            if not situations:
                findings += 1
                line += '；没有列出原文没定义的情况（全部有定义就写 :open "无"）'
            elif undefined:
                findings += len(undefined)
                line += "；原文没定义：" + "；".join(undefined)
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

    combos = list(itertools.product(*letters))
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
    truths = {subject: text for subject, text in annotation_values(base, "truth")}
    if len(verdicts["Pass"]) == 1 and len(verdicts["Fail"]) == 1:
        passed, failed = verdicts["Pass"][0], verdicts["Fail"][0]
        for group in [group for group in groups if len(group) == 2]:
            position = next(i for i in range(len(ids)) if group[0][i] != group[1][i])
            for combo, other in (group, group[::-1]):
                probe = f"<{GEN}Diverge{len(divergent) + 1}>"
                divergent[probe] = (position, combo[position], other[position], combo)
                groups[group] += define(probe, Node("ObjectIntersectionOf", [names[combo][passed], names[other][failed]]))
        for subject, combo, verdict in itertools.product(cases, combos, (passed, failed)):
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
                print(f"scripts/robot explain --reasoner hermit --input {target} -M inconsistency --explanation <输出.md>")
                return 1
            found = {f"<{iri}>" for iri in UNSAT.findall(log)}
            counted = UNSAT_COUNT.search(log)
            if (reasoned.returncode and not counted) or (counted and int(counted.group(1)) != len(found)):
                print(f"推理失败（{target.name}）：\n" + said(reasoned))
                return 2
            unsat |= found
    explain = f"scripts/robot explain --reasoner hermit --input {targets[0]} -M entailment --axiom '<类 IRI> SubClassOf owl:Nothing' --explanation <输出.md>"

    def where(combo_set: set[tuple]) -> str:
        if not ids:
            return ""
        return "所有组合" if len(combo_set) == len(combos) else show(compress(combo_set, letters), ids, letters)

    generated = set(outside) | set(divergent) | set(entail) | set(cases)
    generated |= set(points) | {reading for readings in points.values() for reading in readings.values()}
    contradictions: dict[str, set[tuple]] = {}
    for combo, mapping in names.items():
        for iri in classes - original_probes - generated:
            if mapping[iri] in unsat:
                contradictions.setdefault(iri, set()).add(combo)
    for iri, combo_set in sorted(contradictions.items()):
        findings += 1
        print(f"[不可满足] {local(iri)}：按条款写法，这个类不可能有成员" + (f"（{where(combo_set)}）" if ids else "") + f"。解释用 {explain}")
    if not contradictions:
        print("[不可满足的类] 无")

    for iri in sorted(original_probes, key=local):
        open_in = {combo for combo in combos if names[combo][iri] not in unsat}
        if open_in:
            findings += 1
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
            print(f"[完备性] {local(parent)} 中可能有不属于 {group} 任何一类的情况" + (f"（{where(open_in)}）" if ids else "") + "：原文是否穷尽？穷尽就写成 DisjointUnion，否则记缺陷")
        else:
            print(f"[完备性] {group} 穷尽 {local(parent)}")

    for position, i in enumerate(ids):
        for a, b in itertools.permutations(letters[position], 2):
            contexts = {combo for probe, (p, x, y, combo) in divergent.items() if (p, x, y) == (position, a, b) and probe not in unsat}
            if contexts:
                findings += 1
                print(f"[歧义 {i}] {a} 判通过、{b} 判不通过：可能（{where(contexts)}）")
            else:
                print(f"[歧义 {i}] {a} 判通过、{b} 判不通过：已排除")

    for subject, name in cases.items():
        verdict_of: dict[str, set[tuple]] = {}
        for combo in combos:
            if names[combo][subject] in unsat:
                verdict_of.setdefault("事实与条款矛盾", set()).add(combo)
                continue
            passes = next(probe for probe, value in entail.items() if value == (subject, combo, True)) in unsat
            fails = next(probe for probe, value in entail.items() if value == (subject, combo, False)) in unsat
            verdict = "矛盾" if passes and fails else "PASS" if passes else "NG" if fails else "无结论"
            verdict_of.setdefault(verdict, set()).add(combo)
        parts = [f"{verdict} {where(combo_set)}".strip() for verdict, combo_set in sorted(verdict_of.items())]
        truth = truths.get(subject)
        tail = ""
        if truth:
            agree = verdict_of.get(truth, set())
            tail = f"；真值 {truth}，" + (f"与 {where(agree) or '本体'} 一致" if agree else "没有读法与之一致")
        print(f"[文件] {name}：{'；'.join(parts)}{tail}")

    print(f"完整报告：{out}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
