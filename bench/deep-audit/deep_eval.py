"""Deep mode against the human corpus oracle, through the production deep execution path.

    run     inside the deep image, as root (bench/deep-audit/run.sh starts it); one JSON line per case
    report  anywhere; summarizes one run directory into summary.json and summary.md

`run` builds each case's frozen round as the corpus does, freezes the platform model selection
as a tenant without its own key does, and executes it the way the Controller does: the bound
local gateway, ContainerRuntime (the guest runs as uid 10001 under prlimit), then
CoreRuntime.execute_snapshot in deep mode: one guest for the round, each file checked and then
reviewed with its own time; a review that does not finish leaves that file with its program result.

References are recognized as an upload does, answered from the corpus recordings (live only on a
miss, nothing saved), so two runs differ only in the audit itself.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import statistics
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

# ---- run (inside the deep image) ------------------------------------------------------------


def _cases(dataset: str):
    from corpus_contracts import oracle

    return oracle.load_oracle(dataset)


class _Usage(logging.Handler):
    """deep_review logs each target's model usage; nothing else returns it to the host."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.by_target: dict[str, dict] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == "deep_review_usage %s %s" and len(record.args or ()) == 2:
            version, usage = record.args  # type: ignore[misc]
            try:
                self.by_target[str(version)] = json.loads(str(usage))
            except ValueError:
                pass


class _Patch:
    """The one MonkeyPatch method replay.install uses; undone after each case."""

    def __init__(self) -> None:
        self.saved: list[tuple[Any, str, Any]] = []

    def setattr(self, owner: Any, name: str, value: Any) -> None:
        self.saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def undo(self) -> None:
        for owner, name, value in reversed(self.saved):
            setattr(owner, name, value)


class _Timeline:
    """Each task the guest starts arrives as a progress line; a task runs until the next one starts."""

    def __init__(self) -> None:
        self.marks: list[tuple[float, str | None, str | None]] = []

    def __call__(self, progress: dict) -> None:
        self.marks.append((time.monotonic(), progress.get("stage"), progress.get("target")))

    def seconds(self, finished: float) -> dict[tuple[str, str], float]:
        spans: dict[tuple[str, str], float] = {}
        for (at, stage, target), following in zip(self.marks, [*self.marks[1:], (finished, None, None)]):
            if stage and target:
                spans[(stage, target)] = round(following[0] - at, 1)
        return spans


def _targets(case, deep: dict, spans: dict, usage: dict) -> list[dict]:
    return [
        {
            "file": file.filename,
            "document_type": file.suite().document_type,
            "state": deep[file.file_id].get("state"),
            "reason": deep[file.file_id].get("reason", ""),
            "check_seconds": spans.get(("check", file.file_id)),
            "review_seconds": spans.get(("review", file.file_id)),
            "usage": usage.get(file.file_id),
        }
        for file in case.files
        if file.role == "target"
    ]


def _rows(case, result: dict, deep: dict, reviews: dict) -> list[dict]:
    final = {(item["file_version_id"], str(item["clause_id"])): item for item in result["findings"]}
    rows = []
    for file in case.files:
        if file.role != "target":
            continue
        state = deep[file.file_id]
        for clause, expected in file.expected.items():
            key = (file.file_id, clause)
            record = reviews.get(key, {})
            deep_status = final.get(key, {}).get("status")
            rows.append(
                {
                    "case": case.case_id,
                    "file": file.filename,
                    "document_type": file.suite().document_type,
                    "clause": clause,
                    "truth": expected.status,
                    # A clause the reviewer did not record kept the program verdict.
                    "program": record.get("program_status") or deep_status,
                    "deep": deep_status,
                    "target_state": state.get("state"),
                    "target_reason": state.get("reason", ""),
                    "decision": record.get("decision"),
                    "proposed": record.get("proposed_status") or record.get("status"),
                    "rejection": record.get("rejection"),
                    "reason": str(record.get("reason") or "")[:400],
                }
            )
    return rows


def run_cases(dataset: str, indexes: list[int], out: Path) -> int:
    from agent.core_program import CoreRuntime
    from agent.execution.isolation import ContainerRuntime, RuntimeConfig, local_gateway
    from agent.run import ExecutionContext
    from corpus_contracts import oracle
    from corpus_contracts.replay import Recorder, install
    from documents.model_access import ModelUnavailable, model_access_scope
    from product.settings import load_settings
    from product.store.model_credentials import platform_selection

    settings = load_settings()
    selection = platform_selection()
    identity = selection.identity
    policy = settings.deep_execution_policy(identity)
    usage = _Usage()
    logging.getLogger("agent.deep_review").addHandler(usage)
    logging.getLogger("agent.deep_review").setLevel(logging.INFO)
    cases = _cases(dataset)
    out.mkdir(parents=True, exist_ok=True)
    state = Path("/run/ppap-isolation")

    def unavailable():
        raise ModelUnavailable()

    failures = 0
    # As the Controller: an inert default that sends nothing until an execution is bound.
    with local_gateway(
        state / "gateway.sock", upstream="https://unconfigured.invalid", model="unconfigured", provider_key=unavailable, require_binding=True, requests=settings.deep_model_max_requests
    ) as gateway:
        for index in indexes:
            case = cases[index]
            started = time.monotonic()
            line: dict[str, Any] = {"index": index, "case": case.case_id, "engine_revision": policy.engine_revision()}
            try:
                # References get their pipeline type and facts as an upload freezes them, from the recordings.
                patch = _Patch()
                recorder = Recorder(case.id, output=out / "unsaved-replay")
                try:
                    install(patch, recorder)
                    with model_access_scope(selection.access):
                        recognized = oracle.recognize_references(case)
                finally:
                    patch.undo()
                line["recognition_live_calls"] = len(recorder.pending)
                recorder.finish(keep=False)
                snapshot, sources, _totals = oracle.build_round(case, recognized)
                snapshot = dataclasses.replace(snapshot, model_identity=identity)
                programs = oracle.programs_for(snapshot)
                runtime = ContainerRuntime(
                    RuntimeConfig(state=state, gateway_socket=gateway.socket_path, gateway_host="127.0.0.1", **policy.model_dump()),
                    model_grant_binding=lambda execution_id: gateway.http.grants.bind(execution_id, identity, selection.access),
                )
                core = CoreRuntime(runtime, None, None)
                usage.by_target.clear()
                timeline = _Timeline()
                audit_started = time.monotonic()
                try:
                    result, deep, reviews = core.execute_snapshot(snapshot, sources, programs, ExecutionContext(on_progress=timeline), analysis_mode="deep")
                finally:
                    gateway.http.grants.revoke_all()
                line.update(
                    seconds=round(time.monotonic() - started, 1),
                    audit_seconds=round(time.monotonic() - audit_started, 1),
                    model_requests=result.get("model_requests"),
                    input_tokens=result.get("input_tokens"),
                    output_tokens=result.get("output_tokens"),
                    targets=_targets(case, deep or {}, timeline.seconds(time.monotonic()), dict(usage.by_target)),
                    rows=_rows(case, result, deep or {}, reviews),
                )
            except Exception as exc:  # noqa: BLE001 — one case never stops the others.
                failures += 1
                line.update(seconds=round(time.monotonic() - started, 1), error=f"{type(exc).__name__}: {str(exc)[:300]}", trace=traceback.format_exc()[-2000:], rows=[])
            with (out / "cases.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(line, ensure_ascii=False) + "\n")
            print(f"case {index} {case.case_id}: {line.get('error') or 'ok'} ({line['seconds']}s)", flush=True)
    return 1 if failures else 0


# ---- report -------------------------------------------------------------------------------


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))] if ordered else 0.0


def _spread(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values) if values else 0.0, "p90": _percentile(values, 0.9), "max": max(values, default=0.0)}


def summarize(lines: list[dict]) -> dict:
    """Scores of one run. A row is right when its verdict equals the human truth."""
    rows = [row for line in lines for row in line.get("rows", [])]
    targets = [target for line in lines for target in line.get("targets", [])]
    transitions: Counter[str] = Counter()
    by_truth: dict[str, Counter[str]] = {}
    by_type: dict[str, Counter[str]] = {}
    totals: Counter[str] = Counter()
    details: dict[str, list[dict]] = {"new_pass_against_truth": [], "broke": [], "rejected": []}
    for row in rows:
        truth, program, deep = row["truth"], row["program"], row["deep"]
        program_right, deep_right = program == truth, deep == truth
        for counter in (totals, by_truth.setdefault(truth, Counter()), by_type.setdefault(row.get("document_type") or "?", Counter())):
            counter["rows"] += 1
            counter["program_right"] += program_right
            counter["deep_right"] += deep_right
        if program != deep:
            kind = "fixed" if deep_right else "broke" if program_right else "wrong_to_wrong"
            totals[kind] += 1
            transitions[f"{program}->{deep} {kind}"] += 1
            if kind == "broke":
                details["broke"].append(row)
            # The one direction deep mode must never take: a new PASS the human did not give.
            if deep == "PASS" and truth != "PASS":
                totals["new_pass_against_truth"] += 1
                details["new_pass_against_truth"].append(row)
        # A clause the program could not decide: did the review settle it, and settle it right?
        if program == "UNABLE":
            totals["program_unable"] += 1
            totals["unable_settled"] += deep != "UNABLE"
            totals["unable_settled_right"] += deep != "UNABLE" and deep_right
        if row["target_state"] != "completed":
            totals["rows_not_reviewed"] += 1
        if row["decision"] == "rejected":
            totals["corrections_rejected"] += 1
            details["rejected"].append(row)
    seconds = [float(line["seconds"]) for line in lines if not line.get("error")]
    return {
        "cases": len(lines),
        "case_errors": {line["case"]: line["error"] for line in lines if line.get("error")},
        "targets": len(targets),
        "targets_not_reviewed": dict(Counter(target["reason"] for target in targets if target["state"] != "completed")),
        **{
            key: totals[key]
            for key in (
                "rows",
                "program_right",
                "deep_right",
                "fixed",
                "broke",
                "wrong_to_wrong",
                "new_pass_against_truth",
                "program_unable",
                "unable_settled",
                "unable_settled_right",
                "rows_not_reviewed",
                "corrections_rejected",
            )
        },
        "transitions": dict(sorted(transitions.items())),
        "by_truth": {truth: dict(counter) for truth, counter in sorted(by_truth.items())},
        "by_document_type": {kind: dict(counter) for kind, counter in sorted(by_type.items())},
        "seconds_per_case": _spread(seconds),
        "check_seconds_per_file": _spread([target["check_seconds"] for target in targets if target.get("check_seconds") is not None]),
        "review_seconds_per_file": _spread([target["review_seconds"] for target in targets if target.get("review_seconds") is not None]),
        "recognition_live_calls": sum(line.get("recognition_live_calls") or 0 for line in lines),
        "model_requests": sum(line.get("model_requests") or 0 for line in lines),
        "input_tokens": sum(line.get("input_tokens") or 0 for line in lines),
        "output_tokens": sum(line.get("output_tokens") or 0 for line in lines),
        "details": details,
    }


def _detail(row: dict) -> str:
    proposed = f"，提议 {row['proposed']}（{row['rejection']}）" if row.get("decision") == "rejected" else ""
    return f"- {row['case']} 《{row['file']}》 {row['clause']}：真值 {row['truth']}，程序 {row['program']}，复核后 {row['deep']}{proposed}。{row['reason']}"


def render(summary: dict, meta: dict) -> str:
    lines = [f"# 深度审核评估 {meta.get('name', '')}", ""]
    lines += [f"- {key}：{value}" for key, value in meta.items() if key != "name"]
    rows = summary["rows"] or 1
    check, review, case = summary["check_seconds_per_file"], summary["review_seconds_per_file"], summary["seconds_per_case"]
    lines += [
        "",
        "| 用例 | 目标文件 | 条款 | 程序正确 | 复核后正确 | 改对 | 改错 | 错改错 | 新增与真值不符的 PASS | 未复核条款 |",
        "|---|---|---|---|---|---|---|---|---|---|",
        f"| {summary['cases']} | {summary['targets']} | {summary['rows']} | {summary['program_right']}（{summary['program_right'] / rows:.1%}） | "
        f"{summary['deep_right']}（{summary['deep_right'] / rows:.1%}） | {summary['fixed']} | {summary['broke']} | {summary['wrong_to_wrong']} | "
        f"{summary['new_pass_against_truth']} | {summary['rows_not_reviewed']} |",
        "",
        f"程序无法判断 {summary['program_unable']} 条，复核后给出结论 {summary['unable_settled']} 条，"
        f"其中与真值一致 {summary['unable_settled_right']} 条；订正被拒 {summary['corrections_rejected']} 条。",
        "",
        "耗时（秒，中位 / P90 / 最大）：",
        f"- 每份检查：{check['median']:.0f} / {check['p90']:.0f} / {check['max']:.0f}",
        f"- 每份复核：{review['median']:.0f} / {review['p90']:.0f} / {review['max']:.0f}",
        f"- 每例（含引用识别）：{case['median']:.0f} / {case['p90']:.0f} / {case['max']:.0f}",
        "",
        f"模型请求 {summary['model_requests']}，输入 token {summary['input_tokens']}，输出 token {summary['output_tokens']}；引用识别未命中录音、现调模型 {summary['recognition_live_calls']} 次。",
        "",
        "## 按人工真值",
        "",
        "| 真值 | 条款 | 程序正确 | 复核后正确 |",
        "|---|---|---|---|",
    ]
    lines += [f"| {truth} | {item.get('rows', 0)} | {item.get('program_right', 0)} | {item.get('deep_right', 0)} |" for truth, item in summary["by_truth"].items()]
    lines += ["", "## 按文档类型", "", "| 类型 | 条款 | 程序正确 | 复核后正确 |", "|---|---|---|---|"]
    lines += [f"| {kind} | {item.get('rows', 0)} | {item.get('program_right', 0)} | {item.get('deep_right', 0)} |" for kind, item in summary["by_document_type"].items()]
    lines += ["", "## 结论变化", ""] + ([f"- {key}：{value}" for key, value in summary["transitions"].items()] or ["- 无"])
    for key, title in (("new_pass_against_truth", "新增与真值不符的 PASS"), ("broke", "改错"), ("rejected", "订正被拒")):
        lines += ["", f"## {title}", ""] + ([_detail(row) for row in summary["details"][key]] or ["- 无"])
    lines += ["", "## 未完成", ""]
    lines += [f"- 未复核目标：{json.dumps(summary['targets_not_reviewed'], ensure_ascii=False)}"]
    lines += [f"- 用例错误 {case}：{error}" for case, error in summary["case_errors"].items()]
    return "\n".join(lines) + "\n"


def report(run: Path) -> dict:
    lines = [json.loads(line) for line in (run / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8")) if (run / "meta.json").is_file() else {}
    summary = summarize(lines)
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "summary.md").write_text(render(summary, meta), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="execute cases inside the deep image")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--dataset", choices=("li", "nio", "uaes"), default="uaes", help="one customer's corpus; customers are evaluated apart")
    run.add_argument("--shard", default="0/1", help="k/n: run cases whose position modulo n is k")
    run.add_argument("--case", type=int, action="append", help="only this case index (repeatable)")
    run.add_argument("--list", action="store_true", help="print the selected cases and stop")
    summary = commands.add_parser("report", help="summarize one run directory")
    summary.add_argument("run", type=Path)
    args = parser.parse_args(argv)
    if args.command == "report":
        summary = report(args.run)
        print(json.dumps({key: value for key, value in summary.items() if key != "details"}, ensure_ascii=False, indent=1))
        return 0
    cases = _cases(args.dataset)
    if args.list:
        for index, case in enumerate(cases):
            print(index, case.id)
        return 0
    shard, shards = (int(part) for part in args.shard.split("/"))
    indexes = [index for index in (args.case or range(len(cases))) if index % shards == shard]
    return run_cases(args.dataset, indexes, args.out)


if __name__ == "__main__":
    sys.exit(main())
