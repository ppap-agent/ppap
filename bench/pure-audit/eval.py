"""Run the auditor over a rulebook revision's benchmark and read its verdicts against the reference.

    eval.py run --revision m4 --batch NAME [--batch NAME ...] [--name RUN] [--thinking medium] [--repeat 3]
    eval.py report RUN [RUN ...] [--deep bench/deep-audit/runs/NAME]

The reference judgments are a model's, not human truth: the report shows differences, not accuracy.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RULEBOOK = ROOT / "assets" / "uaes" / "rulebook" / "v1.0"
PRICES = ROOT / "bench" / "deep-audit" / "prices.json"
STATUS = {"PASS": "通过", "NG": "不通过", "NA": "不适用", "UNABLE": "待确认"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _main_checkout() -> Path:
    """Where the untracked .pi and .env live: the main checkout, also when this runs from a worktree."""
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
    return Path(common).parent


def benchmark(revision: str) -> tuple[Path, dict[str, dict[str, Any]]]:
    base, clauses = RULEBOOK / "candidates" / revision / "benchmark", RULEBOOK / revision / "条文"
    if not (base / "reference-judgments.jsonl").is_file() or not clauses.is_dir():
        sys.exit(f"修订 {revision} 没有基准或签署条文")
    rules = {}
    for path in sorted(clauses.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for rule in document["rules"]:
            rules[rule["id"]] = {"id": rule["id"], "category": rule.get("category", ""), "rule_to_check": rule["rule_to_check"], "title": document["title"]}
    return base, rules


def prepare(base: Path, rules: dict[str, dict[str, Any]], batch: str, out: Path) -> dict[str, str]:
    """Lay out one batch for the auditor. Returns the auditor's file name → repository path.

    Files get neutral names: benchmark file and folder names describe planted defects, and a name is
    not evidence. The target's clauses are the ones the benchmark fixes for it; the document type of
    a target comes from the benchmark, since recognition is not what this bench measures.
    """
    inventory = [row for row in _jsonl(base / "file-inventory.jsonl") if row["batch"] == batch]
    expected = [row for row in _jsonl(base / "expected-rows.jsonl") if row["batch"] == batch]
    if not inventory:
        sys.exit(f"基准里没有批次 {batch}")
    (out / "files").mkdir(parents=True)
    names, files = {}, []
    for number, row in enumerate(sorted(inventory, key=lambda row: row["file"]), 1):
        name = f"{number:02d}{Path(row['file']).suffix.lower()}"
        shutil.copyfile(ROOT / row["file"], out / "files" / name)
        (out / "files" / name).chmod(0o444)
        names[name] = row["file"]
        files.append({"name": name, "path": f"files/{name}"})
    by_file = defaultdict(list)
    for row in expected:
        by_file[row["file"]].append(row)
    targets = []
    for name, source in names.items():
        if source in by_file:
            wanted = [rules[row["clause"]] for row in by_file[source]]
            targets.append(
                {
                    "name": name,
                    "document_type": by_file[source][0]["document_type"],
                    "rules": {"title": wanted[0]["title"], "rules": [{"id": rule["id"], "category": rule["category"], "rule_to_check": rule["rule_to_check"]} for rule in wanted]},
                }
            )
    (out / "batch.json").write_text(json.dumps({"files": files, "targets": targets}, ensure_ascii=False, indent=1), encoding="utf-8")
    return names


def _environment(main: Path) -> str:
    """The vision model the tools use, as deployed: endpoint and key from .env, effort from the deployed configuration."""
    lines = [line for line in (main / ".env").read_text(encoding="utf-8").splitlines() if line.startswith(("PPAP_MODEL_VISION_API_KEY=", "PPAP_MODEL_VISION_BASE_URL=", "PPAP_MODEL_VISION_MODEL="))]
    for line in (ROOT / "deploy" / "tke" / "current.yaml").read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition(":")
        if key == "PPAP_MODEL_VISION_REASONING_EFFORT":
            lines.append(f"{key}={value.strip().strip(chr(34))}")
    handle, path = tempfile.mkstemp(prefix="auditor-env-")
    with os.fdopen(handle, "w") as stream:
        stream.write("\n".join(lines) + "\n")
    return path


def run(args: argparse.Namespace) -> None:
    base, rules = benchmark(args.revision)
    main = _main_checkout()
    if not (main / ".pi" / "agent" / "models.json").is_file():
        sys.exit(f"没有 Pi 配置：{main / '.pi' / 'agent'}")
    image = f"ppap-auditor:{args.image_tag}"
    if args.build or subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        subprocess.run(["docker", "build", "-f", "deploy/auditor/Dockerfile", "-t", image, "."], cwd=ROOT, check=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    for repeat in range(1, args.repeat + 1):
        name = (args.name or f"{stamp}-{args.thinking}") + (f"-r{repeat}" if args.repeat > 1 else "")
        out = HERE / "runs" / name
        if out.exists():
            sys.exit(f"已有同名运行：{out}")
        out.mkdir(parents=True)
        meta = {
            "name": name,
            "revision": args.revision,
            "batches": args.batch,
            "model": args.model,
            "thinking": args.thinking,
            "hard_thinking": args.hard_thinking,
            "seconds": args.seconds,
            "survey": not args.no_survey,
            "challenge": not args.no_challenge,
            "experience": bool(args.experience),
            "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        }
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        env_file = _environment(main)
        try:

            def one(numbered: tuple[int, str]) -> list[dict[str, Any]]:
                number, batch = numbered
                work = out / f"batch-{number:02d}"
                names = prepare(base, rules, batch, work)
                command = ["docker", "run", "--rm", "--user", "10001:10001", "--env-file", env_file, "-e", "AUDITOR_PI_SOURCE=/pi"]
                command += ["-v", f"{main / '.pi' / 'agent'}:/pi:ro", "-v", f"{work}:/work/batch"]
                if args.source:
                    command += ["-v", f"{ROOT / 'packages' / 'auditor' / 'src'}:/opt/auditor:ro"]
                options = ["--model", args.model, "--thinking", args.thinking, "--hard-thinking", args.hard_thinking, "--seconds", str(args.seconds), "--concurrency", str(args.concurrency)]
                options += ["--survey-seconds", str(args.survey_seconds)]
                options += [flag for flag, on in (("--no-survey", args.no_survey), ("--no-challenge", args.no_challenge), ("--no-focus", args.no_focus)) if on]
                if args.experience:
                    experience = Path(args.experience).resolve()
                    experience.mkdir(parents=True, exist_ok=True)
                    command += ["-v", f"{experience}:/work/experience"]
                    options += ["--experience", "/work/experience"]
                started = time.time()
                done = subprocess.run([*command, image, "/work/batch", *options], capture_output=True, text=True)
                (work / "container.log").write_text(done.stdout + done.stderr, encoding="utf-8")
                rows: list[dict[str, Any]] = []
                if not (work / "results.json").is_file():
                    print(f"{batch}：整批失败，见 {work / 'container.log'}", file=sys.stderr)
                    return [{"kind": "failed", "batch": batch, "seconds": round(time.time() - started)}]
                result = json.loads((work / "results.json").read_text(encoding="utf-8"))
                rows.append({"kind": "survey", "batch": batch, **result["survey"]})
                for target in result["targets"]:
                    source = names[target["name"]]
                    rows.append(
                        {"kind": "file", "batch": batch, "file": source, "document_type": target["document_type"], "steps": target["steps"], "refused": target["refused"], "looks": target["looks"]}
                    )
                    rows += [{"kind": "verdict", "batch": batch, "file": source, **row} for row in target["rows"]]
                print(f"{batch}：{sum(1 for row in rows if row['kind'] == 'verdict')} 条结论，{round(time.time() - started)} 秒")
                return rows

            with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
                rows = [row for part in pool.map(one, enumerate(args.batch, 1)) for row in part]
        finally:
            os.unlink(env_file)
        (out / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        report(argparse.Namespace(runs=[str(out)], deep=None))


def _cost(steps: list[dict[str, Any]], prices: dict[str, Any], model: str) -> float:
    price = prices.get(model.rsplit("/", 1)[-1])
    if not price:
        return 0.0
    total = 0.0
    for step in steps:
        fresh = step["input_tokens"]
        total += (fresh * price["input"] + step["cached_input_tokens"] * price["cached_input"] + step["output_tokens"] * price["output"]) / 1e6
    return total


def _summary(run_dir: Path) -> tuple[dict[str, Any], list[str], dict[tuple[str, str, str], dict[str, Any]]]:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    rows = _jsonl(run_dir / "results.jsonl")
    base, _rules = benchmark(meta["revision"])
    ran = {row["batch"] for row in rows if row["kind"] == "verdict"}
    reference = {(row["batch"], row["file"], row["clause"]): row for row in _jsonl(base / "reference-judgments.jsonl") if row["batch"] in ran}
    verdicts = {(row["batch"], row["file"], row["clause"]): row for row in rows if row["kind"] == "verdict"}
    prices = json.loads(PRICES.read_text(encoding="utf-8"))["models"]
    files = [row for row in rows if row["kind"] == "file"]
    steps = [step for row in files for step in row["steps"]] + [row["step"] for row in rows if row["kind"] == "survey" and row.get("step")]
    table: Counter[tuple[str, str]] = Counter()
    first_agree = final_agree = 0
    differences = []
    for key, expected in sorted(reference.items()):
        verdict = verdicts.get(key, {"status": "无输出", "reason": ""})
        table[(expected["verdict"], verdict["status"])] += 1
        final_agree += verdict["status"] == expected["verdict"]
        first_agree += verdict.get("first_status", verdict["status"]) == expected["verdict"]
        if verdict["status"] != expected["verdict"]:
            differences.append((key, expected, verdict))
    cited = [item for verdict in verdicts.values() for point in verdict.get("points", []) for item in point["evidence"]]
    seen = [item for item in cited if item["verified"] == "seen"]
    by_phase = defaultdict(lambda: Counter())
    for step in steps:
        phase = by_phase[step["phase"]]
        phase["runs"] += 1
        phase["seconds"] += step["seconds"]
        phase["timed_out"] += bool(step["timed_out"])
        phase["requests"] += step["requests"]
        phase["tool_calls"] += step["tool_calls"]
    summary = {
        "name": meta["name"],
        "rows": len(reference),
        "agree": final_agree,
        "agree_first": first_agree,
        "statuses": dict(Counter(verdict["status"] for verdict in verdicts.values())),
        "reference_statuses": dict(Counter(row["verdict"] for row in reference.values())),
        "unaudited": sum(1 for verdict in verdicts.values() if verdict.get("unaudited")),
        "disputed": sum(1 for verdict in verdicts.values() if verdict.get("disputed")),
        "challenged": sum(1 for verdict in verdicts.values() if verdict.get("challenged")),
        "challengeable": sum(1 for verdict in verdicts.values() if "challenged" in verdict),
        "changed_after_first": sum(1 for verdict in verdicts.values() if verdict.get("first_status") not in (None, verdict["status"])),
        "refused_submissions": sum(row["refused"] for row in files),
        "looks": sum(row["looks"] for row in files),
        "citations": {
            "text": len(cited) - len(seen),
            "seen": len(seen),
            "seen_transcription_agrees": sum(1 for item in seen if item.get("transcription_agrees")),
            "seen_checked": sum(1 for item in seen if "transcription_agrees" in item),
        },
        "failed_batches": [row["batch"] for row in rows if row["kind"] == "failed"],
        "cost_usd": round(_cost(steps, prices, meta["model"]), 3),
        "tokens": {key: sum(step[key] for step in steps) for key in ("input_tokens", "cached_input_tokens", "output_tokens")},
        "phases": {phase: dict(counts) for phase, counts in by_phase.items()},
        "table": {f"{expected}→{got}": count for (expected, got), count in sorted(table.items())},
    }
    lines = [
        f"# {meta['name']}",
        "",
        f"修订 {meta['revision']}，模型 {meta['model']}，推理强度 {meta['thinking']}（专审与裁决 {meta['hard_thinking']}），每份文件审核时限 {meta['seconds']} 秒。"
        f"勘查{'开' if meta['survey'] else '关'}，质证{'开' if meta['challenge'] else '关'}，经验{'开' if meta['experience'] else '关'}。",
        "参考判定是模型判定，不是人工真值：下面只是差异，不是准确率。",
        "",
        f"- 对照行数：{summary['rows']}；与参考一致：{final_agree}（第一遍审核后为 {first_agree}）",
        f"- 结论分布：{'、'.join(f'{STATUS.get(key, key)} {value}' for key, value in summary['statuses'].items())}；"
        f"参考：{'、'.join(f'{STATUS.get(key, key)} {value}' for key, value in summary['reference_statuses'].items())}",
        f"- 未在时限内给出结论：{summary['unaudited']} 条；质证到 {summary['challenged']}/{summary['challengeable']} 条，改变结论 {summary['changed_after_first']} 条，其中交裁决 {summary['disputed']} 条",  # noqa: E501
        f"- 提交被退回 {summary['refused_submissions']} 次；查看原件 {summary['looks']} 次",
        f"- 证据：逐字核对通过 {summary['citations']['text']} 项；所见类 {summary['citations']['seen']} 项，其中经视觉模型转写印证 {summary['citations']['seen_transcription_agrees']}/{summary['citations']['seen_checked']} 项",  # noqa: E501
        f"- 用量：输入 {summary['tokens']['input_tokens']}（另缓存命中 {summary['tokens']['cached_input_tokens']}），输出 {summary['tokens']['output_tokens']}；按公开价约 {summary['cost_usd']} 美元（不含转写）",  # noqa: E501
        "",
        "| 步骤 | 次数 | 合计秒 | 超时 | 模型请求 | 工具调用 |",
        "|---|---|---|---|---|---|",
        *(f"| {phase} | {counts['runs']} | {counts['seconds']} | {counts['timed_out']} | {counts['requests']} | {counts['tool_calls']} |" for phase, counts in by_phase.items()),
        "",
        "| 参考 → 本次 | 条数 |",
        "|---|---|",
        *(f"| {STATUS.get(expected, expected)} → {STATUS.get(got, got)} | {count} |" for (expected, got), count in sorted(table.items())),
        "",
        "## 与参考不同的条款",
        "",
    ]
    for (batch, file, clause), expected, verdict in differences:
        trail = f"（第一遍 {STATUS.get(verdict['first_status'], verdict['first_status'])}）" if verdict.get("first_status") not in (None, verdict["status"]) else ""
        lines += [
            f"### {clause}《{Path(file).name}》",
            f"- 批次：{batch}",
            f"- 参考 {STATUS.get(expected['verdict'], expected['verdict'])}：{expected['reason']}",
            f"- 本次 {STATUS.get(verdict['status'], verdict['status'])}{trail}：{verdict['reason']}",
            *(f"  - {point['result']}：{point['need']}{'。' + point['why'] if point['why'] else ''}" for point in verdict.get("points", [])),
            "",
        ]
    return summary, lines, verdicts


def report(args: argparse.Namespace) -> None:
    every = []
    for run_dir in map(Path, args.runs):
        summary, lines, verdicts = _summary(run_dir)
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{summary['name']}：{summary['agree']}/{summary['rows']} 与参考一致，未审 {summary['unaudited']}，约 {summary['cost_usd']} 美元 → {run_dir / 'summary.md'}")
        every.append((summary["name"], verdicts))
    if args.deep:
        deep = {(row["batch"], row["source"], row["clause"]): row["status"] for row in _jsonl(Path(args.deep) / "results.jsonl") if row.get("kind") == "finding"}
        name, verdicts = every[0]
        shared = [key for key in verdicts if key in deep]
        table = Counter((deep[key], verdicts[key]["status"]) for key in shared)
        print(f"与深度复核 {args.deep} 对照（{len(shared)} 行共有）：一致 {sum(count for (a, b), count in table.items() if a == b)}")
        for (a, b), count in sorted(table.items()):
            if a != b:
                print(f"  深度复核 {STATUS.get(a, a)} → 纯代理 {STATUS.get(b, b)}：{count}")
    if len(every) > 1:
        keys = set.intersection(*(set(verdicts) for _name, verdicts in every))
        flips = [key for key in sorted(keys) if len({verdicts[key]["status"] for _name, verdicts in every}) > 1]
        hard = [key for key in flips if {"PASS", "NG"} <= {verdicts[key]["status"] for _name, verdicts in every}]
        print(f"{len(every)} 次运行共有 {len(keys)} 行：结论不一致 {len(flips)} 行，其中通过与不通过互换 {len(hard)} 行")
        for batch, file, clause in flips:
            print(f"  {clause}《{Path(file).name}》：{' / '.join(verdicts[(batch, file, clause)]['status'] for _name, verdicts in every)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("run")
    sub.add_argument("--revision", required=True)
    sub.add_argument("--batch", action="append", required=True, help="批次名，见基准的 batches.jsonl；可重复")
    sub.add_argument("--name")
    sub.add_argument("--model", default="local/grok-4.7")
    sub.add_argument("--thinking", default="medium")
    sub.add_argument("--hard-thinking", default="high")
    sub.add_argument("--seconds", type=int, default=1200)
    sub.add_argument("--survey-seconds", type=int, default=600)
    sub.add_argument("--concurrency", type=int, default=3, help="一个批次里同时审核几份文件")
    sub.add_argument("--jobs", type=int, default=1, help="同时跑几个批次")
    sub.add_argument("--repeat", type=int, default=1)
    sub.add_argument("--no-survey", action="store_true")
    sub.add_argument("--no-focus", action="store_true")
    sub.add_argument("--no-challenge", action="store_true")
    sub.add_argument("--experience", help="读法经验目录；给了就审前读入、审后追加")
    sub.add_argument("--image-tag", default="dev")
    sub.add_argument("--build", action="store_true", help="先重建镜像")
    sub.add_argument("--source", action="store_true", help="把工作区的 packages/auditor/src 挂进容器，免重建")
    sub.set_defaults(run=run)
    sub = commands.add_parser("report")
    sub.add_argument("runs", nargs="+")
    sub.add_argument("--deep", help="深度复核基准的一次运行目录，用来对照")
    sub.set_defaults(run=report)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
