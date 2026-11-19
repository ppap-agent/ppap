"""Check one clause's formalization with ROBOT (HermiT) and write its plain-language report.

    python3 check.py CLAUSE.ofn [--out DIR] [--report FILE]

Formalizing a clause is how its flaws are found: wherever the writer cannot go on without choosing for the
clause, something is unclear. The script only confirms each such spot with a concrete example, it does not
try to prove the formalization complete (see SKILL.md, 约定速查):

- `:Judged` is what the clause judges; `:PassWhen` / `:FailWhen` are the conditions under which the clause itself
  says pass / fail (`owl:Nothing` when it says none). A thing in neither gets no verdict; a thing in both, both.
- `Point__<n><L>` is reading L of ambiguity n for the undefined class `Point`. `Example__<n>` must get different
  verdicts under the readings of ambiguity n while every other ambiguity stays undecided, so it shows this one.
- `Gap__<k>` / `Conflict__<k>` are concrete things the clause gives no verdict / both verdicts for; `:under "2B"`
  says which readings they assume.
- `:clause` / `:quote` tie the ontology to the clause text; `:open` on a quote asks what the clause leaves
  undefined; classes carrying `:case "file name"` describe real files with closed facts (classes, not individuals:
  an ABox makes HermiT minutes slower).

Exit status: 0 when every finding in the report is confirmed, 1 when one is not (the report says so at the top),
2 when the input could not be used.
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

from ofn import NOTHING, THING, Node, literal_text, local, parse, walk, write
from report import MISSING, Clause, Found, render

ROBOT = Path(__file__).resolve().with_name("robot")
UNSAT = re.compile(r"unsatisfiable: (\S+)")
UNSAT_COUNT = re.compile(r"There are (\d+) unsatisfiable classes")
READING = re.compile(r"^(.+)__(\d+)([A-Z])$")
EXAMPLE = re.compile(r"[#/]Example__(\d+)$")
GAP = re.compile(r"[#/]Gap__(\d+)$")
CONFLICT = re.compile(r"[#/]Conflict__(\d+)$")
UNDER = re.compile(r"^(\d+)([A-Z])$")
PUNCTUATION = set("，。；;？?、：:,.!！（）()“”\"'‘’《》【】[] 　")
GEN = "urn:clause-check#"
TESTS = ("pass", "fail", "nopass", "nofail")


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


def opens(base: list[Node]) -> list[tuple[str, list[tuple[str, str | None]]]]:
    """(quoted fragment, [(question, the user's answer or None)]) for every quote carrying :open."""
    found = []
    for item in (sub for node in base for sub in walk(node)):
        if not isinstance(item, Node) or item.head not in ("Annotation", "AnnotationAssertion"):
            continue
        if local(item.args[-3 if item.head == "AnnotationAssertion" else -2]) != "quote":
            continue
        questions = []
        for arg in item.args:
            if isinstance(arg, Node) and arg.head == "Annotation" and local(arg.args[-2]) == "open" and literal_text(arg.args[-1]) != "无":
                answers = [literal_text(n.args[-1]) for n in arg.args if isinstance(n, Node) and n.head == "Annotation" and local(n.args[-2]) == "answered"]
                questions.append((literal_text(arg.args[-1]), answers[0] if answers else None))
        if questions:
            found.append((literal_text(item.args[-1]), questions))
    return found


def verdict(holds: dict[str, bool], possible: bool) -> str:
    if not possible:
        return "IMPOSSIBLE"
    if holds["pass"] and holds["fail"]:
        return "BOTH"
    if holds["pass"] and holds["nofail"]:
        return "PASS"
    if holds["fail"] and holds["nopass"]:
        return "NG"
    if holds["nopass"] and holds["nofail"]:
        return "NONE"
    return "OPEN"


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

    found = Found()
    problems, clause, spans = alignment(path, ontology)
    if clause is None:
        print(f"[约定] {problems[0]}")
        return 2
    found.clauses.append(clause)
    # Not a gate: a sentence left unread is a hint to go back to it, not a flaw of the clause.
    for problem in problems:
        print(f"[原文对齐] {problem}（提示，不影响报告）")

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
        lacking = set(letters[ids.index(ambiguity_of[point])]) - set(readings)
        if lacking:
            print(f"[读法] {local(point)} 缺少读法 {'、'.join(sorted(lacking))}：同一处歧义的每个歧义点都要有全部读法")
            return 2
    found.ids, found.offered = ids, letters

    # Answers written back: `:chosen "A"` (and the user's words in `:answered`) on any point of an ambiguity.
    plain = {name: dict(annotation_values(base, name)) for name in ("issue", "question", "label", "says", "chosen", "answered")}
    for point in points:
        position = ids.index(ambiguity_of[point])
        chosen = plain["chosen"].get(point)
        if chosen is None:
            continue
        if chosen not in letters[position]:
            print(f"[答复] {local(point)} 选了不存在的读法 {chosen}")
            return 2
        found.chosen[position] = chosen
        found.answered[position] = plain["answered"].get(point, MISSING)
    found.rewrites = [text for _, text in annotation_values(ontology.annotations, "rewrite")]
    found.undefined = opens(base)
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
    under: dict[str, dict[int, str]] = {}
    for subject, text in annotation_values(base, "under"):
        match = UNDER.match(text.strip())
        if not match or match.group(1) not in ids or match.group(2) not in letters[ids.index(match.group(1))]:
            print(f'[约定] {local(subject or "")} 的 :under「{text}」不是已写出的理解（如 "2B"）')
            return 2
        under.setdefault(subject, {})[ids.index(match.group(1))] = match.group(2)

    # Each context fixes some ambiguities to one reading and leaves the rest undecided: the base context fixes
    # only answered ones, an ambiguity's own contexts add one of its readings, a gap or conflict adds what it assumes.
    contexts: list[dict[int, str]] = [dict(found.chosen)]

    def context(assign: dict[int, str]) -> int:
        full = {**found.chosen, **assign}
        if full not in contexts:
            contexts.append(full)
        return contexts.index(full)

    open_positions = [position for position in range(len(ids)) if position not in found.chosen]
    own = {(position, letter): context({position: letter}) for position in open_positions for letter in letters[position]}
    on_example = {(position, letter): context({**under.get(examples[ids[position]], {}), position: letter}) for position in open_positions if ids[position] in examples for letter in letters[position]}
    for position in open_positions:
        if ids[position] in examples:
            found.example_under[position] = sorted((p, letter) for p, letter in under.get(examples[ids[position]], {}).items() if p != position)
    situated = {iri: context(under.get(iri, {})) for iri in [*gaps.values(), *conflicts.values()]}

    probes: dict[str, tuple] = {}
    extra: list[list[Node]] = [[] for _ in contexts]

    def ask(index: int, meaning: tuple, expression: Node | str) -> None:
        probe = f"<{GEN}Q{len(probes) + 1}>"
        probes[probe] = meaning
        extra[index] += [Node("Declaration", [Node("Class", [probe])]), Node("EquivalentClasses", [probe, expression])]

    def judge(index: int, subject: str) -> None:
        ask(index, ("subject", index, subject), subject)
        for test, expression in zip(TESTS, (Node("ObjectComplementOf", [pass_when]), Node("ObjectComplementOf", [fail_when]), pass_when, fail_when)):
            ask(index, (test, index, subject), Node("ObjectIntersectionOf", [subject, expression]))

    for (position, letter), index in on_example.items():
        judge(index, examples[ids[position]])
    for iri, index in situated.items():
        judge(index, iri)
    for index in sorted({0, *own.values()}):
        for subject in cases:
            judge(index, subject)
    # A hint only: whether the clause, with every ambiguity still open, leaves room for no verdict or both.
    ask(0, ("region", "NONE"), Node("ObjectIntersectionOf", [judged, Node("ObjectComplementOf", [pass_when]), Node("ObjectComplementOf", [fail_when])]))
    ask(0, ("region", "BOTH"), Node("ObjectIntersectionOf", [judged, pass_when, fail_when]))

    targets = []
    for index, assign in enumerate(contexts):
        # A decided point is its reading. An undecided one lies between what all its readings share and what any
        # of them allows, so whatever follows here follows under every reading (sound, not complete).
        fixed = []
        for point, readings in points.items():
            position = ids.index(ambiguity_of[point])
            if position in assign:
                fixed.append(Node("EquivalentClasses", [point, readings[assign[position]]]))
            elif len(readings) > 1:
                fixed += [
                    Node("SubClassOf", [Node("ObjectIntersectionOf", list(readings.values())), point]),
                    Node("SubClassOf", [point, Node("ObjectUnionOf", list(readings.values()))]),
                ]
        target = out / f"context-{index + 1}.ofn"
        write(target, "<https://ppap.example/rules/context>", base + fixed + extra[index])
        targets.append(target)
    for stale in out.glob("*.ofn"):
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
        return 2

    def reason(target: Path) -> tuple[Path, subprocess.CompletedProcess]:
        return target, robot("reason", "--reasoner", "hermit", "--input", str(target), "--dump-unsatisfiable", str(target.with_suffix(".unsat")), "-vv")

    unsat: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) // 2)) as pool:
        for target, reasoned in pool.map(reason, targets):
            log = reasoned.stdout + reasoned.stderr
            if "inconsistent" in log.lower():
                print(f"[整体矛盾] {target.name} 不一致，无法推理：词汇或文件的描述自相矛盾（写法问题，不是条款的冲突）。解释用：")
                print(f"{ROBOT} explain --reasoner hermit --input {target} -M inconsistency --explanation <输出.md>")
                return 2
            listed = {f"<{iri}>" for iri in UNSAT.findall(log)}
            counted = UNSAT_COUNT.search(log)
            if (reasoned.returncode and not counted) or (counted and int(counted.group(1)) != len(listed)):
                print(f"推理失败（{target.name}）：\n" + said(reasoned))
                return 2
            unsat |= listed
    possible = {meaning for probe, meaning in probes.items() if probe not in unsat}

    def outcome(index: int, subject: str) -> str:
        return verdict({test: (test, index, subject) not in possible for test in TESTS}, ("subject", index, subject) in possible)

    # Vocabulary that can never apply is a writing error: the clause's own conflicts show up as "both verdicts".
    readings = {reading for group in points.values() for reading in group.values()}
    vocabulary = classes - readings - set(points) - {pass_when, fail_when} - set(cases) - {*examples.values(), *gaps.values(), *conflicts.values()}
    explain = f"{ROBOT} explain --reasoner hermit --input {targets[0]} -M entailment --axiom '<类 IRI> SubClassOf owl:Nothing' --explanation <输出.md>"
    for iri in sorted(vocabulary & unsat):
        found.incomplete.append(f"{local(iri)} 写得自相矛盾")
        print(f"[写法错误] {local(iri)} 不可能有成员：词汇写得自相矛盾。用 {explain} 查清后改写")

    if ids:
        print("[歧义] " + "；".join(f"{i}：{'、'.join(ls)}" + ("（已答复）" if p in found.chosen else "") for p, (i, ls) in enumerate(zip(ids, letters))))
    for position, i in enumerate(ids):
        if position in found.chosen:
            continue
        if i not in examples:
            found.incomplete.append(f"第 {position + 1} 处还没有例子")
            print(f"[例子缺项] 歧义 {i}：写 Example__{i}，一个在几种理解下结论不同的具体文件")
            continue
        by_letter = {letter: outcome(on_example[(position, letter)], examples[i]) for letter in letters[position]}
        shown = "、".join(f"{letter} {v}" for letter, v in by_letter.items())
        if any(v in ("OPEN", "IMPOSSIBLE") for v in by_letter.values()):
            found.incomplete.append(f"第 {position + 1} 处的例子推不出结论")
            print(f"[例子没写全] Example__{i}（{shown}）：把事实写封闭；结论还取决于别的歧义时，换一个只受这一处影响的例子，或用 :under 写明假定的理解")
        elif len(set(by_letter.values())) == 1:
            found.incomplete.append(f"第 {position + 1} 处的例子在几种理解下结论相同")
            print(f"[例子不分歧] Example__{i}（{shown}）：换一个例子；换不出就不是会改变结论的歧义，改成 :open 问题或删掉")
        else:
            found.example_verdicts[position] = by_letter
            print(f"[歧义 {i}] 例子确认：{shown}")

    for kind, described, want, title in (("Gap", gaps, "NONE", "原文没有给出结论"), ("Conflict", conflicts, "BOTH", "原文同时给出通过和不通过")):
        for number, iri in sorted(described.items(), key=lambda item: int(item[0])):
            got = outcome(situated[iri], iri)
            if got != want:
                found.incomplete.append(f"写出的“{title}”的例子 {kind}__{number} 不成立")
                print(f"[情况写错] {kind}__{number}：按条款是 {got}，不是{title}。OPEN 表示事实没写全或还取决于别的歧义（用 :under 写明）")
                continue
            (found.gaps if kind == "Gap" else found.conflicts).append((plain["says"].get(iri, MISSING), sorted(under.get(iri, {}).items())))
            print(f"[{title}] {kind}__{number} 确认")
        if ("region", want) in possible and not described:
            print(f"[提示] 各处都不定时，可能有{title}的文件：想一想是哪种文件；确有就写成 {kind}__<k>，没有就不用管")

    for subject, name in cases.items():
        whole = outcome(0, subject)
        per = {position: {letter: outcome(own[(position, letter)], subject) for letter in letters[position]} for position in range(len(ids)) if position not in found.chosen}
        found.files[name] = (whole, per)
        if whole == "IMPOSSIBLE":
            found.incomplete.append(f"`{name}` 的描述自相矛盾")
        print(f"[文件] {name}：{whole}" + "".join(f"；歧义 {ids[p]}：" + "、".join(f"{letter} {v}" for letter, v in vs.items()) for p, vs in per.items() if whole == "OPEN"))

    # The plain texts the report is made of.
    texts: list[tuple[str, str, str, str | None]] = []  # (where, annotation, kind, text)
    for position, i in enumerate(ids):
        where = f"歧义 {i}（{'、'.join(local(p) for p in points if ambiguity_of[p] == i)}）"
        texts += [(where, ":issue", "title", found.issue.get(position)), (where, ":question", "question", found.question.get(position))]
        if position not in found.chosen:
            texts.append((f"Example__{i}", ":says", "sentence", found.example.get(position)))
        for letter in letters[position]:
            texts += [(f"{where} 读法 {letter}", "rdfs:label", "label", found.label.get((position, letter))), (f"{where} 读法 {letter}", ":says", "sentence", found.says.get((position, letter)))]
    for kind, described in (("Gap", gaps), ("Conflict", conflicts)):
        texts += [(f"{kind}__{k}", ":says", "phrase", plain["says"].get(iri)) for k, iri in described.items()]
    texts += [(f"「{quote}」", ":open", "question", question) for quote, pairs in found.undefined for question, _ in pairs]
    texts += [("条款", ":rewrite", "rewrite", text) for text in found.rewrites]

    asked: set[str] = set()
    for question in [found.question.get(position) for position in range(len(ids))] + [q for _, pairs in found.undefined for q, _ in pairs]:
        key = re.sub(r"[\s，。？?、“”「」]", "", question or "")
        if key and key in asked:
            found.incomplete.append(f"问题「{question}」问了两次")
            print(f"[重复] 问题「{question}」问了两次")
        asked.add(key)

    allowed = {word.casefold() for clause in found.clauses for word in ASCII_WORD.findall(clause.text)}
    allowed |= {word.casefold() for name in cases.values() for word in ASCII_WORD.findall(name)}
    wording = 0
    for place, annotation, kind, value in texts:
        if value is None:
            wording += 1
            print(f"[白话缺项] {place}：缺 {annotation}")
            continue
        for problem in lint(value, kind, allowed):
            wording += 1
            print(f"[白话质量] {place} {annotation}「{value}」：{problem}")
    if any(answer == MISSING for answer in found.answered.values()):
        wording += 1
        print("[白话缺项] 有 :chosen 的歧义没写 :answered（用户的原话）")
    if wording:
        found.incomplete.append(f"有 {wording} 处白话说明缺项或不合格")

    report_path.write_text(render(found), encoding="utf-8")
    print(f"报告：{report_path}" + ("（有发现还没确认，报告顶部已标出）" if found.incomplete else ""))
    print(f"推理文件：{out}")
    return 1 if found.incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
