"""Run the auditor over a rulebook revision's benchmark and read its verdicts against the reference.

    eval.py run --revision m4 --batch NAME [--batch NAME ...] [--name RUN] [--thinking medium] [--repeat 3]
    eval.py run --corpus li --all
    eval.py report RUN [RUN ...] [--deep bench/deep-audit/runs/NAME]

A rulebook revision's reference judgments are a model's: the report shows differences, not accuracy.
A corpus (``assets/corpus/<customer>-oracle.csv``) carries a person's judgments on that customer's samples.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RULEBOOK = ROOT / "assets" / "uaes" / "rulebook" / "v1.0"
CORPUS = ROOT / "assets" / "corpus"
PRICES = ROOT / "bench" / "deep-audit" / "prices.json"
STATUS = {"PASS": "通过", "NG": "不通过", "NA": "不适用", "UNABLE": "待确认"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _main_checkout() -> Path:
    """Where the untracked .pi and .env live: the main checkout, also when this runs from a worktree."""
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
    return Path(common).parent


def _revision(revision: str, wording: str | None = None) -> dict[str, Any]:
    """A rulebook revision's benchmark: signed clauses, batches of originals, a model's reference judgments.

    The clauses may be read from a later revision of the same version (same clause ids): a reference
    that was corrected by rulings written into the next revision is a reference for that wording.
    """
    wording = wording or revision
    base, clauses = RULEBOOK / "candidates" / revision / "benchmark", RULEBOOK / wording / "条文"
    if not (base / "reference-judgments.jsonl").is_file() or not clauses.is_dir():
        sys.exit(f"修订 {revision} 没有基准，或修订 {wording} 没有签署条文")
    rules = {}
    for path in sorted(clauses.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for rule in document["rules"]:
            rules[rule["id"]] = {"id": rule["id"], "category": rule.get("category", ""), "rule_to_check": rule["rule_to_check"], "title": document["title"]}
    files: dict[str, list[str]] = defaultdict(list)
    for row in _jsonl(base / "file-inventory.jsonl"):
        files[row["batch"]].append(row["file"])
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _jsonl(base / "expected-rows.jsonl"):
        rule = rules[row["clause"]]
        target = targets.setdefault((row["batch"], row["file"]), {"document_type": row["document_type"], "title": rule["title"], "rules": []})
        target["rules"].append({key: rule[key] for key in ("id", "category", "rule_to_check")})
    return {
        "label": f"修订 {revision}" if wording == revision else f"修订 {revision} 的基准，条文取修订 {wording}",
        "human": False,
        "batches": [row["batch"] for row in _jsonl(base / "batches.jsonl")],
        "files": dict(files),
        "targets": targets,
        "reference": {(row["batch"], row["file"], row["clause"]): {"verdict": row["verdict"], "reason": row["reason"]} for row in _jsonl(base / "reference-judgments.jsonl")},
    }


def _corpus(customer: str) -> dict[str, Any]:
    """One customer's samples with a person's judgment on every clause: a batch is one submitted combination."""
    cases, oracle, reviewed = (CORPUS / f"{customer}-cases.csv", CORPUS / f"{customer}-oracle.csv", CORPUS / f"{customer}-reviewed-rules.json")
    if not cases.is_file() or not oracle.is_file() or not reviewed.is_file():
        sys.exit(f"没有 {customer} 的人工判定表")
    suites = json.loads(reviewed.read_text(encoding="utf-8"))
    files: dict[str, list[str]] = defaultdict(list)
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    sources = {}
    with cases.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            files[row["组合ID"]].append(row["原件"])
            sources[(row["组合ID"], row["文件ID"])] = row["原件"]
            if row["角色"] == "target":
                suite = suites[row["Suite版本"]]
                wanted = set(row["Binding"].split(";"))
                targets[(row["组合ID"], row["原件"])] = {
                    "document_type": suite["document_type"],
                    "title": suite["rules"]["title"],
                    "rules": [{key: rule.get(key, "") for key in ("id", "category", "rule_to_check")} for rule in suite["rules"]["rules"] if rule["id"] in wanted],
                }
    reference = {}
    judges: Counter[str] = Counter()
    with oracle.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            judges[row["判定人"]] += 1
            reference[(row["组合ID"], sources[(row["组合ID"], row["文件ID"])], row["条款ID"])] = {"verdict": row["人工真值"], "reason": row["人工判定依据"], "gap": row["已知差距"]}
    return {
        "label": f"{customer} 人工判定表（判定人：{'、'.join(judges)}）",
        "human": not any("LLM" in judge for judge in judges),
        "batches": list(files),
        "files": dict(files),
        "targets": targets,
        "reference": reference,
    }


def compared(meta: dict[str, Any]) -> dict[str, Any]:
    given = _corpus(meta["corpus"]) if meta.get("corpus") else _revision(meta["revision"], meta.get("clauses"))
    if only := set(meta.get("only") or []):
        # A run of a few clauses: the other clauses of the same files are neither audited nor compared.
        for target in given["targets"].values():
            target["rules"] = [rule for rule in target["rules"] if rule["id"] in only]
        given["reference"] = {key: row for key, row in given["reference"].items() if key[2] in only}
    return given


def prepare(given: dict[str, Any], batch: str, out: Path) -> dict[str, str]:
    """Lay out one batch for the auditor. Returns the auditor's file name → repository path.

    Files get neutral names: sample file and folder names describe planted defects, and a name is
    not evidence. A target's clauses and document type come from the source, since recognition is
    not what this bench measures.
    """
    if batch not in given["files"]:
        sys.exit(f"没有批次 {batch}")
    (out / "files").mkdir(parents=True)
    names, files = {}, []
    for number, source_path in enumerate(sorted(given["files"][batch]), 1):
        name = f"{number:02d}{Path(source_path).suffix.lower()}"
        shutil.copyfile(ROOT / source_path, out / "files" / name)
        (out / "files" / name).chmod(0o444)
        names[name] = source_path
        files.append({"name": name, "path": f"files/{name}"})
    targets = []
    for name, source_path in names.items():
        target = given["targets"].get((batch, source_path))
        if target and target["rules"]:
            targets.append({"name": name, "document_type": target["document_type"], "rules": {"title": target["title"], "rules": target["rules"]}})
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
    if bool(args.revision) == bool(args.corpus):
        sys.exit("用 --revision 或 --corpus 指定对照的来源，二者取一")
    if args.clauses and not args.revision:
        sys.exit("--clauses 只和 --revision 一起用")
    given = compared({"revision": args.revision, "corpus": args.corpus, "clauses": args.clauses, "only": args.only})
    if args.all:
        args.batch = given["batches"]
    if not args.batch:
        sys.exit("用 --batch 指定批次，或 --all 跑全部")
    width = max(2, len(str(len(args.batch))))
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
        if out.exists() and not args.resume:
            sys.exit(f"已有同名运行：{out}（接着跑用 --resume）")
        if (out / "meta.json").is_file() and json.loads((out / "meta.json").read_text(encoding="utf-8")).get("clauses") != args.clauses:
            sys.exit(f"{out} 是按另一版条文跑的，不能接着跑；换一个 --name")
        out.mkdir(parents=True, exist_ok=True)
        meta = {
            "name": name,
            "revision": args.revision,
            "clauses": args.clauses,
            "only": args.only,
            "corpus": args.corpus,
            "batches": args.batch,
            "model": args.model,
            "thinking": args.thinking,
            "hard_thinking": args.hard_thinking,
            "seconds": args.seconds,
            "concurrency": args.concurrency,
            "read": not args.no_read,
            "challenge": args.challenge,
            "experience": bool(args.experience),
            "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        }
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        names = {}
        for number, batch in enumerate(args.batch, 1):
            work = out / f"batch-{number:0{width}d}"
            if (work / "names.json").is_file():
                names[batch] = json.loads((work / "names.json").read_text(encoding="utf-8"))
            else:
                names[batch] = prepare(given, batch, work)
                (work / "names.json").write_text(json.dumps(names[batch], ensure_ascii=False, indent=1), encoding="utf-8")
        # One container for the whole run: every batch draws on the same pool of auditors.
        env_file = _environment(main)
        command = ["docker", "run", "--rm", "--user", "10001:10001", "--env-file", env_file, "-e", "AUDITOR_PI_SOURCE=/pi"]
        command += ["-v", f"{main / '.pi' / 'agent'}:/pi:ro", "-v", f"{out}:/work/run"]
        if args.source:
            command += ["-v", f"{ROOT / 'packages' / 'auditor' / 'src'}:/opt/auditor:ro"]
        options = ["--model", args.model, "--thinking", args.thinking, "--hard-thinking", args.hard_thinking, "--seconds", str(args.seconds), "--concurrency", str(args.concurrency)]
        options += ["--read-seconds", str(args.read_seconds)]
        options += [flag for flag, on in (("--no-read", args.no_read), ("--challenge", args.challenge), ("--no-focus", args.no_focus), ("--no-transcribe", args.no_transcribe)) if on]
        if args.experience:
            experience = Path(args.experience).resolve()
            experience.mkdir(parents=True, exist_ok=True)
            command += ["-v", f"{experience}:/work/experience"]
            options += ["--experience", "/work/experience"]
        started = time.time()
        try:
            done = subprocess.run([*command, image, *(f"/work/run/batch-{number:0{width}d}" for number in range(1, len(args.batch) + 1)), *options], capture_output=True, text=True)
        finally:
            os.unlink(env_file)
        with (out / "container.log").open("a", encoding="utf-8") as stream:
            stream.write(done.stdout + done.stderr)
        earlier = json.loads((out / "wall.json").read_text(encoding="utf-8")) if (out / "wall.json").is_file() else []
        (out / "wall.json").write_text(json.dumps([*earlier, round(time.time() - started)]), encoding="utf-8")
        rows: list[dict[str, Any]] = [{"kind": "run", "seconds": sum(earlier) + round(time.time() - started), "sittings": len(earlier) + 1}]
        for number, batch in enumerate(args.batch, 1):
            work = out / f"batch-{number:0{width}d}"
            if not (work / "results.json").is_file():
                print(f"{batch}：没有跑完，见 {out / 'container.log'}", file=sys.stderr)
                rows.append({"kind": "failed", "batch": batch})
                continue
            result = json.loads((work / "results.json").read_text(encoding="utf-8"))
            rows.append({"kind": "batch", "batch": batch, "wall": result["wall"], "prepare": result["prepare"], "notes": result["notes"], "steps": result["other_steps"]})
            for target in result["targets"]:
                source = names[batch][target["name"]]
                rows.append(
                    {"kind": "file", "batch": batch, "file": source, "document_type": target["document_type"], "steps": target["steps"], "refused": target["refused"], "looks": target["looks"]}
                )
                rows += [{"kind": "verdict", "batch": batch, "file": source, **row} for row in target["rows"]]
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
    given = compared(meta)
    ran = {row["batch"] for row in rows if row["kind"] == "verdict"}
    reference = {key: row for key, row in given["reference"].items() if key[0] in ran}
    verdicts = {(row["batch"], row["file"], row["clause"]): row for row in rows if row["kind"] == "verdict"}
    prices = json.loads(PRICES.read_text(encoding="utf-8"))["models"]
    files = [row for row in rows if row["kind"] == "file"]
    batches = [row for row in rows if row["kind"] == "batch"]
    steps = [step for row in [*files, *batches] for step in row["steps"]]
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
        phase["look_seconds"] += step.get("look_seconds", 0)
        phase["cached"] += step["cached_input_tokens"]
        phase["output"] += step["output_tokens"]
        phase["cents"] += round(_cost([step], prices, meta["model"]) * 100)
    whole = next((row for row in rows if row["kind"] == "run"), None)
    wall = {key: sum(row["wall"][key] for row in batches) for key in ("total", "prepare", "read", "audit")} if batches else {}
    if wall and whole:
        # Batches run side by side: the run's own clock is the time that passed.
        wall["total"] = whole["seconds"]
    errors = Counter(str(step["error"])[:80] for step in steps if step.get("error"))
    spent = sorted(verdict["seconds"] for verdict in verdicts.values() if "seconds" in verdict)
    # By document type: where the auditor and the reference part ways, a type at a time.
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    types = {(row["batch"], row["file"]): row["document_type"] for row in files}
    for key, expected in reference.items():
        verdict = verdicts.get(key, {"status": "无输出"})
        kind = by_type[types.get(key[:2], "?")]
        kind["rows"] += 1
        kind["agree"] += verdict["status"] == expected["verdict"]
        kind["unaudited"] += bool(verdict.get("unaudited")) or verdict["status"] == "无输出"
        kind["pass_ng"] += {verdict["status"], expected["verdict"]} == {"PASS", "NG"}
    per_file = []
    for row in files:
        mine = {key: verdict for key, verdict in verdicts.items() if key[:2] == (row["batch"], row["file"])}
        own = list(mine.values())
        done = [verdict for verdict in own if not verdict.get("unaudited")]
        agree = sum(1 for key, verdict in mine.items() if reference.get(key, {}).get("verdict") == verdict["status"])
        dollars = round(_cost(row["steps"], prices, meta["model"]), 2)
        per_file.append(f"| {Path(row['file']).name[:40]} | {row['document_type']} | {len(own)} | {len(done)} | {agree} | {sum(step['seconds'] for step in row['steps'])} | {dollars} |")
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
        "session_errors": dict(errors),
        "seconds_per_request": round(sum(step["seconds"] for step in steps) / max(1, sum(step["requests"] for step in steps)), 1),
        "wall": wall,
        "prepare": {key: sum(row["prepare"].get(key, 0) for row in batches) for key in ("pages", "pictures", "transcriptions")},
        "notes": sum(row["notes"] for row in batches),
        "clause_seconds": {"median": spent[len(spent) // 2], "max": spent[-1]} if spent else {},
        "cost_usd": round(_cost(steps, prices, meta["model"]), 3),
        "tokens": {key: sum(step[key] for step in steps) for key in ("input_tokens", "cached_input_tokens", "output_tokens")},
        "phases": {phase: dict(counts) for phase, counts in by_phase.items()},
        "table": {f"{expected}→{got}": count for (expected, got), count in sorted(table.items())},
        "human": given["human"],
        "types": {kind: dict(counts) for kind, counts in sorted(by_type.items())},
    }
    lines = [
        f"# {meta['name']}",
        "",
        f"{given['label']}，模型 {meta['model']}，推理强度 {meta['thinking']}（专审与裁决 {meta['hard_thinking']}），每条条款审核时限 {meta['seconds']} 秒，"
        f"同时 {meta.get('concurrency', '?')} 名审核员。"
        f"阅卷{'开' if meta.get('read', meta.get('survey')) else '关'}，质证{'开' if meta['challenge'] else '关'}，经验{'开' if meta['experience'] else '关'}。",
        "对照的是人工判定。" if given["human"] else "参考判定是模型判定，不是人工真值：下面只是差异，不是准确率。",
        "",
        *(
            [
                f"- 耗时：全程 {wall['total']} 秒（准备 {wall['prepare']}，阅卷 {wall['read']}，审核到裁决 {wall['audit']}）；"
                f"一条条款从开审到定论中位 {summary['clause_seconds'].get('median', '?')} 秒，最长 {summary['clause_seconds'].get('max', '?')} 秒",
                f"- 准备：分析 PDF {summary['prepare']['pages']} 页，取出图片 {summary['prepare']['pictures']} 张，预先转写 {summary['prepare']['transcriptions']} 处；案卷 {summary['notes']} 条",
            ]
            if wall
            else []
        ),
        f"- 对照行数：{summary['rows']}；与参考一致：{final_agree}（第一遍审核后为 {first_agree}）",
        f"- 结论分布：{'、'.join(f'{STATUS.get(key, key)} {value}' for key, value in summary['statuses'].items())}；"
        f"参考：{'、'.join(f'{STATUS.get(key, key)} {value}' for key, value in summary['reference_statuses'].items())}",
        f"- 未在时限内给出结论：{summary['unaudited']} 条；质证到 {summary['challenged']}/{summary['challengeable']} 条，改变结论 {summary['changed_after_first']} 条，其中交裁决 {summary['disputed']} 条",  # noqa: E501
        f"- 提交被退回 {summary['refused_submissions']} 次；查看原件 {summary['looks']} 次",
        f"- 模型请求平均 {summary['seconds_per_request']} 秒一次；会话报错 {sum(errors.values())} 次"
        + ("：" + "；".join(f"{text} ×{count}" for text, count in errors.most_common(5)) if errors else ""),
        f"- 证据：逐字核对通过 {summary['citations']['text']} 项；所见类 {summary['citations']['seen']} 项，其中经视觉模型转写印证 {summary['citations']['seen_transcription_agrees']}/{summary['citations']['seen_checked']} 项",  # noqa: E501
        f"- 用量：输入 {summary['tokens']['input_tokens']}（另缓存命中 {summary['tokens']['cached_input_tokens']}），输出 {summary['tokens']['output_tokens']}；按公开价约 {summary['cost_usd']} 美元（不含转写）",  # noqa: E501
        "",
        "| 步骤 | 次数 | 合计秒 | 其中工具秒 | 超时 | 模型请求 | 工具调用 | 缓存输入 | 输出 | 约美元 |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {phase} | {counts['runs']} | {counts['seconds']} | {counts['look_seconds']} | {counts['timed_out']} | {counts['requests']} | {counts['tool_calls']} | {counts['cached']} | {counts['output']} | {counts['cents'] / 100:.2f} |"  # noqa: E501
            for phase, counts in by_phase.items()
        ),
        "",
        "| 文件类型 | 对照行数 | 与参考一致 | 通过与不通过相反 | 没有结论 |",
        "|---|---|---|---|---|",
        *(f"| {kind} | {counts['rows']} | {counts['agree']} | {counts['pass_ng']} | {counts['unaudited']} |" for kind, counts in sorted(by_type.items())),
        "",
        "| 文件 | 类型 | 条款 | 有结论 | 与参考一致 | 会话秒 | 约美元 |",
        "|---|---|---|---|---|---|---|",
        *per_file,
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
            f"- 参考 {STATUS.get(expected['verdict'], expected['verdict'])}：{expected['reason']}" + (f"（已知差距：{expected['gap']}）" if expected.get("gap") else ""),
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
    sub.add_argument("--revision", help="规则库修订，如 m4：对照它的基准")
    sub.add_argument("--clauses", help="条文取自同一版本的另一修订，如 m5；不写就是 --revision 那一修订")
    sub.add_argument("--corpus", help="客户，如 li、nio：对照 assets/corpus 里它的人工判定表")
    sub.add_argument("--batch", action="append", default=[], help="批次名（基准的 batches.jsonl，或人工判定表的组合ID）；可重复")
    sub.add_argument("--only", action="append", default=[], help="只审这条条款（如 PCE:2）；可重复。同一文件的其他条款不审也不对照")
    sub.add_argument("--name")
    sub.add_argument("--model", default="local/grok-4.7")
    sub.add_argument("--thinking", default="medium")
    sub.add_argument("--hard-thinking", default="high")
    sub.add_argument("--seconds", type=int, default=600, help="审一条条款的时限")
    sub.add_argument("--read-seconds", type=int, default=360, help="阅卷一份文件的时限")
    sub.add_argument("--concurrency", type=int, default=12, help="同时工作的审核员数，所有批次合计")
    sub.add_argument("--all", action="store_true", help="基准的全部批次")
    sub.add_argument("--resume", action="store_true", help="接着跑同名运行：已有结论的条款不再审")
    sub.add_argument("--repeat", type=int, default=1)
    sub.add_argument("--no-read", action="store_true")
    sub.add_argument("--no-transcribe", action="store_true")
    sub.add_argument("--no-focus", action="store_true")
    sub.add_argument("--challenge", action="store_true", help="打开质证和裁决")
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
