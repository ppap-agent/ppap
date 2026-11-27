"""Read every auditor session of a run and say where the work went: what to look at when improving tools and prompts.

    sessions.py RUN [--worst 25]

Writes ``sessions.md`` and ``sessions.jsonl`` into the run directory. It counts and lists; it judges nothing.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

LOOK = re.compile(r"\blook\s+([a-z]+)((?:\s+(?:'[^']*'|\"[^\"]*\"|[^\s;&|<>]+))*)")


def _events(path: Path) -> list[dict[str, Any]]:
    found = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def session(log: Path) -> dict[str, Any]:
    """One Pi session as numbers: requests, tokens, what it ran, what came back, when it first submitted."""
    work = log.parents[1]
    row: dict[str, Any] = {"log": str(log), "phase": log.stem, "work": work.name, "batch": next((part for part in log.parts if part.startswith("batch-")), "")}
    counts: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    looks: Counter[str] = Counter()
    outside: list[str] = []
    problems: list[str] = []
    first = last = submitted = None
    longest = 0.0
    for event in _events(log):
        kind = event.get("type")
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if kind == "message_end" and message.get("role") == "assistant":
            stamp = message.get("timestamp")
            if isinstance(stamp, (int, float)):
                first = first if first is not None else stamp / 1000
                if last is not None:
                    longest = max(longest, stamp / 1000 - last)
                last = stamp / 1000
            usage = message.get("usage") or {}
            counts["requests"] += 1
            counts["fresh"] += usage.get("input") or 0
            counts["cached"] += usage.get("cacheRead") or 0
            counts["output"] += usage.get("output") or 0
            counts["uncached_requests"] += (usage.get("cacheRead") or 0) < 2000 and counts["requests"] > 1
            if message.get("errorMessage"):
                problems.append("模型报错：" + str(message["errorMessage"])[:160])
            for part in message.get("content") or []:
                if part.get("type") != "toolCall":
                    continue
                arguments = part.get("arguments") or {}
                counts["tool_calls"] += 1
                if part.get("name") != "bash":
                    counts[f"tool_{part.get('name')}"] += 1
                    continue
                text = str(arguments.get("command") or "")
                found = LOOK.findall(text)
                for command, rest in found:
                    commands[command] += 1
                    looks[f"{command}{rest}"[:160]] += 1
                    if command == "submit" and submitted is None and last is not None and first is not None:
                        submitted = last - first
                # What the auditor ran that was not `look`: a sign the tools did not give it what it needed.
                # A verdict handed to `look submit` on standard input, and reading one's own task files, are not that.
                stripped = re.sub(r"<<'?(\w+)'?.*?^\1$", "", text, flags=re.S | re.M)
                stripped = re.sub(r"\bcat\s+(files|rules|clauses)\.json\b|\|\s*(head|tail)\b[^|;&]*", "", LOOK.sub("", stripped))
                if re.search(r"\b(python3?|fitz|openpyxl|xlrd|pdftotext|unzip|strings|grep|rg|sed|awk|find|cat|head|ls)\b", stripped):
                    outside.append(text[:240])
                # Another auditor's directory, the run's logs or its records: nothing an auditor should open.
                if re.search(r"/work/|\.\./|\blogs/|\.audit/", stripped):
                    counts["strayed"] += 1
        elif kind == "tool_execution_end":
            content = (event.get("result") or {}).get("content") or []
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            counts["tool_chars"] += len(text)
            counts["pictures_opened"] += sum(1 for part in content if isinstance(part, dict) and part.get("type") == "image")
            for marker, label in (
                ("读不了：", "读不了"),
                ("被退回", "提交被退回"),
                ("没有记下", "没有记下"),
                ("Traceback", "工具崩溃"),
                ("command not found", "命令不存在"),
                ("usage: look", "用法不对"),
            ):
                if marker in text:
                    at = text.index(marker)
                    problems.append(f"{label}：{text[at : at + 220]}".replace("\n", " "))
    repeated = sum(count - 1 for count in looks.values() if count > 1)
    row.update(
        counts,
        seconds=round((last or 0) - (first or 0)),
        longest_gap=round(longest),
        first_submit=round(submitted) if submitted is not None else None,
        commands=dict(commands),
        repeated_looks=repeated,
        outside=outside,
        problems=problems,
        verdict=(work / "result.json").is_file() and not json.loads((work / "result.json").read_text(encoding="utf-8"))["row"].get("unaudited"),
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--worst", type=int, default=25)
    args = parser.parse_args()
    rows = [session(log) for log in sorted(args.run.glob("batch-*/**/logs/*.jsonl"))]
    (args.run / "sessions.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_phase[row["phase"]].append(row)

    def middle(values: list[float]) -> float:
        ordered = sorted(values)
        return ordered[len(ordered) // 2] if ordered else 0

    lines = [f"# 会话一览：{args.run.name}", "", f"共 {len(rows)} 个会话。", ""]
    lines += [
        "| 步骤 | 会话 | 秒（中位/最长） | 请求（中位/最多） | 到首次提交的秒数（中位） | 输出 token（中位） | 没命中缓存的请求 | 打开图片 | 重复的查看 | 绕开 look 的命令 | 走出自己目录 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for phase, group in by_phase.items():
        submits = [row["first_submit"] for row in group if row["first_submit"] is not None]
        lines.append(
            f"| {phase} | {len(group)} | {middle([row['seconds'] for row in group])}/{max(row['seconds'] for row in group)} | "
            f"{middle([row.get('requests', 0) for row in group])}/{max(row.get('requests', 0) for row in group)} | {middle(submits) if submits else '—'} | "
            f"{middle([row.get('output', 0) for row in group])} | {sum(row.get('uncached_requests', 0) for row in group)}/{sum(row.get('requests', 0) for row in group)} | "
            f"{sum(row.get('pictures_opened', 0) for row in group)} | {sum(row['repeated_looks'] for row in group)} | {sum(len(row['outside']) for row in group)} | "
            f"{sum(row.get('strayed', 0) for row in group)} |"
        )
    commands: Counter[str] = Counter()
    for row in rows:
        commands.update(row["commands"])
    lines += ["", "look 命令次数：" + "、".join(f"{name} {count}" for name, count in commands.most_common()), ""]
    problems: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for row in rows:
        for problem in row["problems"]:
            # The same kind of trouble with different files and cells counts as one kind.
            label = re.sub(r"\d+", "N", problem)[:70]
            problems[label] += 1
            examples.setdefault(label, f"{problem}（{row['batch']}/{row['work']}/{row['phase']}）")
    lines += ["## 工具给审核员的坏消息（按种类）", ""]
    lines += [f"- ×{count} {examples[label]}" for label, count in problems.most_common(40)] or ["（没有）"]
    outside = [(row, text) for row in rows for text in row["outside"]]
    lines += ["", f"## 绕开 look 的命令（{len(outside)} 条，列前 40）", ""]
    lines += [f"- {row['batch']}/{row['work']}/{row['phase']}：`{text.replace(chr(10), ' ')[:200]}`" for row, text in outside[:40]] or ["（没有）"]
    lines += ["", f"## 最费时的 {args.worst} 个会话", "", "| 会话 | 秒 | 请求 | 最长一次间隔 | 首次提交 | 有结论 | 输出 token | 查看最多的命令 |", "|---|---|---|---|---|---|---|---|"]
    for row in sorted(rows, key=lambda row: -row["seconds"])[: args.worst]:
        top = "、".join(f"{name} {count}" for name, count in Counter(row["commands"]).most_common(3))
        lines.append(
            f"| {row['batch']}/{row['work']}/{row['phase']} | {row['seconds']} | {row.get('requests', 0)} | {row['longest_gap']} | {row['first_submit'] if row['first_submit'] is not None else '—'} | "
            f"{'是' if row['verdict'] else '否'} | {row.get('output', 0)} | {top} |"
        )
    (args.run / "sessions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} 个会话 → {args.run / 'sessions.md'}")


if __name__ == "__main__":
    main()
