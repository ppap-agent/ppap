"""Check clause ontologies with ROBOT: OWL 2 DL profile, consistency, unsatisfiable classes and probes.

    python3 check.py CLAUSE.ofn [MORE.ofn ...] [--out DIR]

Several inputs are merged first, so conflicts between clauses show up. A class whose local name starts
with `Probe_` names a situation that should not exist; a probe that is still satisfiable is a finding.
The full ROBOT reports stay in --out (default: next to the first input).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROBOT = Path(__file__).with_name("robot")
PROBE = re.compile(r"Declaration\(\s*Class\(\s*(\S+?Probe_[^\s)]+)\s*\)\s*\)")
UNSAT = re.compile(r"^## \[[^\]]*\]\(([^)]+)\) SubClassOf \[Nothing\]", re.MULTILINE)
PREFIX = re.compile(r"Prefix\(\s*([\w-]*):=<([^>]+)>\s*\)")


def robot(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROBOT), *args], capture_output=True, text=True)


def said(run: subprocess.CompletedProcess) -> str:
    """ROBOT prints its errors on stdout; drop the generic help lines."""
    lines = (run.stdout + run.stderr).splitlines()
    return "\n".join(line for line in lines if line.strip() and not line.startswith(("Use the -vvv", "Use the --help", "For details see")))


def expand(name: str, prefixes: dict[str, str]) -> str:
    if name.startswith("<") and name.endswith(">"):
        return name[1:-1]
    prefix, _, local = name.partition(":")
    return prefixes.get(prefix, prefix + ":") + local


def probes(files: list[Path]) -> dict[str, str]:
    """Full IRI -> name as written, for every declared probe class."""
    found: dict[str, str] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        prefixes = dict(PREFIX.findall(text))
        for name in PROBE.findall(text):
            found[expand(name, prefixes)] = name
    return found


def local(iri: str) -> str:
    return re.split(r"[#/]", iri)[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    files: list[Path] = args.inputs
    out: Path = args.out or files[0].with_suffix(".robot")
    out.mkdir(parents=True, exist_ok=True)

    if len(files) > 1:
        target = out / "merged.ofn"
        merged = robot("merge", *[part for path in files for part in ("--input", str(path))], "--output", str(target))
        if merged.returncode:
            print("合并失败（多半是某个文件语法错误）：\n" + said(merged))
            return 2
    else:
        target = files[0]

    findings = 0
    profile_report = out / "profile.txt"
    profile_report.unlink(missing_ok=True)
    profile = robot("validate-profile", "--profile", "DL", "--input", str(target), "--output", str(profile_report))
    if not profile_report.exists():
        print("读取失败（语法错误）：\n" + said(profile))
        return 2
    if profile.returncode:
        # HermiT only reasons over OWL 2 DL; results past this point would not mean anything.
        print("[超出 OWL 2 DL] 先改写到 DL 之内，或把这部分记为表达局限：")
        print(profile_report.read_text(encoding="utf-8").strip())
        return 1
    print("[OWL 2 DL] 符合")

    inconsistency = out / "inconsistency.md"
    inconsistency.unlink(missing_ok=True)
    checked = robot("explain", "--reasoner", "hermit", "--input", str(target), "-M", "inconsistency", "--explanation", str(inconsistency))
    if checked.returncode or not inconsistency.exists():
        print("推理失败：\n" + said(checked))
        return 2
    if "SubClassOf [Nothing]" in inconsistency.read_text(encoding="utf-8"):
        print(f"[整体矛盾] 本体不一致，无法继续推理。解释（{inconsistency}）：")
        print(inconsistency.read_text(encoding="utf-8").strip())
        return 1

    report = out / "unsatisfiable.md"
    report.unlink(missing_ok=True)
    explained = robot("explain", "--reasoner", "hermit", "--input", str(target), "-M", "unsatisfiability", "--unsatisfiable", "all", "--explanation", str(report))
    if explained.returncode:
        print("推理失败：\n" + said(explained))
        return 2
    unsat = set(UNSAT.findall(report.read_text(encoding="utf-8"))) if report.exists() else set()

    declared = probes(files)
    contradictions = sorted(iri for iri in unsat if iri not in declared)
    for iri in contradictions:
        findings += 1
        print(f"[不可满足] {local(iri)}：按条款写法，这个类不可能有成员。解释见 {report}")
    if not contradictions:
        print("[不可满足的类] 无")

    for iri, name in sorted(declared.items(), key=lambda item: item[1]):
        if iri in unsat:
            print(f"[探针 已排除] {name}")
        else:
            findings += 1
            print(f"[探针 仍可能] {name}：条款没有排除这种情况")
    if not declared:
        print("[探针] 没有声明 Probe_ 类")
    print(f"完整报告：{out}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
