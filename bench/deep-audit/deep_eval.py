"""Deep mode against the UAES human oracle, through the production deep execution path.

    run     inside the deep image, as root (bench/deep-audit/run.sh starts it); one JSON line per case
    report  anywhere; summarizes one run directory into summary.json and summary.md

`run` builds each case's frozen round as the corpus does, freezes the platform model selection
as a tenant without its own key does, and executes it the way the Controller does: the bound
local gateway, ContainerRuntime (the guest runs as uid 10001 under prlimit), then
CoreRuntime.execute_snapshot in deep mode: one guest for the round, each file checked and then
reviewed with its own time; a review that does not finish leaves that file with its program result.

References are recognized as an upload does, answered from the corpus recordings (live only on a
miss, nothing saved), so two runs differ only in the audit itself. Every model request of the
audit passes the gateway this process hosts; each is recorded with its use, model, effort and
provider usage, and charged to the file and stage (check or review) under way when it was sent.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

DATASET = "uaes"
PRICES = Path(__file__).with_name("prices.json")

# ---- run (inside the deep image) ------------------------------------------------------------


def _cases():
    from corpus_contracts import oracle

    return oracle.load_oracle(DATASET)


class _Patch:
    """The one MonkeyPatch method replay.install uses; undone when the scope ends."""

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
        self.marks: list[tuple[float, str, str]] = []

    def __call__(self, progress: dict) -> None:
        if progress.get("stage") and progress.get("target"):
            self.marks.append((time.monotonic(), progress["stage"], progress["target"]))
        else:
            self.marks.append((time.monotonic(), "", ""))

    def task_at(self, moment: float) -> tuple[str, str]:
        """The task under way at that moment; a request sent before the first line belongs to the first task."""
        current = next(((stage, target) for _at, stage, target in self.marks if stage), ("", ""))
        for at, stage, target in self.marks:
            if at > moment:
                break
            current = (stage, target)
        return current

    def seconds(self, finished: float) -> dict[tuple[str, str], float]:
        spans: dict[tuple[str, str], float] = {}
        for (at, stage, target), following in zip(self.marks, [*self.marks[1:], (finished, "", "")]):
            if stage:
                spans[(stage, target)] = round(following[0] - at, 1)
        return spans


class _Requests:
    """Every request the gateway sends upstream: its use, model, effort and the provider's usage."""

    def __init__(self) -> None:
        self.sent: dict[int, dict] = {}
        self.done: list[dict] = []

    def install(self, patch: _Patch) -> None:
        from agent.execution import gateway

        authorize, record_usage, finish_failed = gateway.Grants.authorize, gateway.Grants.record_usage, gateway.Grants.finish_failed
        requests = self

        def on_authorize(grants, token, body, size):
            lease, purpose = authorize(grants, token, body, size)
            # The handler adds the deployment's options to this same body before it is sent.
            requests.sent[id(lease)] = {"at": time.monotonic(), "purpose": purpose, "body": body}
            return lease, purpose

        def finished(lease, usage):
            sent = requests.sent.pop(id(lease), None)
            if sent is not None:
                body = sent.pop("body")
                reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
                requests.done.append({**sent, "model": body.get("model"), "effort": reasoning.get("effort"), "usage": usage})

        def on_usage(grants, lease, usage):
            finished(lease, usage if isinstance(usage, dict) else None)
            return record_usage(grants, lease, usage)

        def on_failed(grants, lease, failure):
            finished(lease, None)
            return finish_failed(grants, lease, failure)

        patch.setattr(gateway.Grants, "authorize", on_authorize)
        patch.setattr(gateway.Grants, "record_usage", on_usage)
        patch.setattr(gateway.Grants, "finish_failed", on_failed)

    def take(self, timeline: _Timeline) -> list[dict]:
        done, self.done = self.done, []
        self.sent.clear()
        out = []
        for request in done:
            usage = request["usage"] or {}
            details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
            output_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
            stage, target = timeline.task_at(request["at"])
            out.append(
                {
                    "stage": stage,
                    "target": target,
                    "purpose": request["purpose"],
                    "model": request["model"],
                    "effort": request["effort"],
                    # Unknown when the request failed or the provider gave no counts.
                    "input_tokens": usage.get("input_tokens"),
                    "cached_tokens": details.get("cached_tokens") or 0,
                    "output_tokens": usage.get("output_tokens"),
                    "reasoning_tokens": output_details.get("reasoning_tokens") or 0,
                }
            )
        return out


def _targets(case, deep: dict, spans: dict) -> list[dict]:
    return [
        {
            "file_id": file.file_id,
            "file": file.filename,
            "document_type": file.suite().document_type,
            "state": deep[file.file_id].get("state"),
            "reason": deep[file.file_id].get("reason", ""),
            "check_seconds": spans.get(("check", file.file_id)),
            "review_seconds": spans.get(("review", file.file_id)),
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


def run_cases(indexes: list[int], out: Path) -> int:
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
    cases = _cases()
    out.mkdir(parents=True, exist_ok=True)
    state = Path("/run/ppap-isolation")
    requests = _Requests()
    observing = _Patch()
    requests.install(observing)

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
                timeline = _Timeline()
                audit_started = time.monotonic()
                try:
                    result, deep, reviews = core.execute_snapshot(snapshot, sources, programs, ExecutionContext(on_progress=timeline), analysis_mode="deep")
                finally:
                    gateway.http.grants.revoke_all()
                line.update(
                    seconds=round(time.monotonic() - started, 1),
                    audit_seconds=round(time.monotonic() - audit_started, 1),
                    targets=_targets(case, deep or {}, timeline.seconds(time.monotonic())),
                    requests=requests.take(timeline),
                    rows=_rows(case, result, deep or {}, reviews),
                )
            except Exception as exc:  # noqa: BLE001 — one case never stops the others.
                failures += 1
                requests.take(_Timeline())
                line.update(seconds=round(time.monotonic() - started, 1), error=f"{type(exc).__name__}: {str(exc)[:300]}", trace=traceback.format_exc()[-2000:], rows=[])
            with (out / "cases.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(line, ensure_ascii=False) + "\n")
            print(f"case {index} {case.case_id}: {line.get('error') or 'ok'} ({line['seconds']}s)", flush=True)
    observing.undo()
    return 1 if failures else 0


# ---- report -------------------------------------------------------------------------------


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))] if ordered else 0.0


def _spread(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values) if values else 0.0, "p90": _percentile(values, 0.9), "max": max(values, default=0.0)}


def cost(request: dict, prices: dict) -> float | None:
    """US dollars at the public list price; None when the model has no price or the counts are unknown."""
    price = prices.get(request.get("model") or "")
    if price is None or request.get("input_tokens") is None or request.get("output_tokens") is None:
        return None
    if price.get("long_prompt_tokens") and request["input_tokens"] > price["long_prompt_tokens"]:
        price = price["long"]
    cached = min(request["cached_tokens"], request["input_tokens"])
    return ((request["input_tokens"] - cached) * price["input"] + cached * price["cached_input"] + request["output_tokens"] * price["output"]) / 1_000_000


def _usage(requests: list[dict], prices: dict) -> dict:
    priced = [cost(request, prices) for request in requests]
    known = [request for request in requests if request.get("input_tokens") is not None]
    return {
        "requests": len(requests),
        "requests_without_usage": len(requests) - len(known),
        "unpriced_requests": sum(value is None for value in priced),
        "input_tokens": sum(request["input_tokens"] for request in known),
        "cached_tokens": sum(request["cached_tokens"] for request in known),
        "output_tokens": sum(request["output_tokens"] or 0 for request in known),
        "reasoning_tokens": sum(request["reasoning_tokens"] for request in known),
        "usd": round(sum(value or 0.0 for value in priced), 4),
    }


def summarize(lines: list[dict], prices: dict) -> dict:
    """Scores, time and spend of one run. A row is right when its verdict equals the human truth."""
    rows = [row for line in lines for row in line.get("rows", [])]
    targets = [target for line in lines for target in line.get("targets", [])]
    requests = [request for line in lines for request in line.get("requests", [])]
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
    # Spend per file and stage: what one target file costs to check and to review.
    per_file: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for line in lines:
        for request in line.get("requests", []):
            per_file.setdefault((line["case"], request["target"]), {"check": [], "review": []}).setdefault(request["stage"] or "check", []).append(request)
    file_usd = {stage: [_usage(stages.get(stage, []), prices)["usd"] for stages in per_file.values()] for stage in ("check", "review")}
    file_requests = [len(stages.get("check", [])) + len(stages.get("review", [])) for stages in per_file.values()]
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
        "usage": {
            "all": _usage(requests, prices),
            "by_stage": {stage: _usage([request for request in requests if (request["stage"] or "check") == stage], prices) for stage in ("check", "review")},
            "by_model": {
                f"{model} / {effort or '默认'}": _usage([r for r in requests if (r["model"], r["effort"]) == (model, effort)], prices)
                for model, effort in sorted({(r["model"] or "", r["effort"] or "") for r in requests})
            },
        },
        "usd_per_file": {stage: _spread(values) for stage, values in file_usd.items()},
        "requests_per_file": _spread([float(value) for value in file_requests]),
        "requests_per_case_max": max((len(line.get("requests", [])) for line in lines), default=0),
        "details": details,
    }


def _detail(row: dict) -> str:
    proposed = f"，提议 {row['proposed']}（{row['rejection']}）" if row.get("decision") == "rejected" else ""
    return f"- {row['case']} 《{row['file']}》 {row['clause']}：真值 {row['truth']}，程序 {row['program']}，复核后 {row['deep']}{proposed}。{row['reason']}"


def _usage_row(name: str, usage: dict) -> str:
    cached = usage["cached_tokens"] / usage["input_tokens"] if usage["input_tokens"] else 0.0
    return (
        f"| {name} | {usage['requests']} | {usage['input_tokens']:,} | {cached:.0%} | {usage['output_tokens']:,} | {usage['reasoning_tokens']:,} | "
        f"${usage['usd']:.2f} | {usage['requests_without_usage']} / {usage['unpriced_requests']} |"
    )


def render(summary: dict, meta: dict) -> str:
    lines = [f"# 深度审核评估 {meta.get('name', '')}", ""]
    lines += [f"- {key}：{value}" for key, value in meta.items() if key != "name"]
    rows = summary["rows"] or 1
    check, review, case = summary["check_seconds_per_file"], summary["review_seconds_per_file"], summary["seconds_per_case"]
    usage, per_file = summary["usage"], summary["usd_per_file"]
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
        "## 时间",
        "",
        "| 中位 / P90 / 最大（秒） | |",
        "|---|---|",
        f"| 每份检查（时限 180） | {check['median']:.0f} / {check['p90']:.0f} / {check['max']:.0f} |",
        f"| 每份复核（时限 360） | {review['median']:.0f} / {review['p90']:.0f} / {review['max']:.0f} |",
        f"| 每例（含引用识别） | {case['median']:.0f} / {case['p90']:.0f} / {case['max']:.0f} |",
        "",
        f"未复核目标：{json.dumps(summary['targets_not_reviewed'], ensure_ascii=False)}；引用识别未命中录音、现调模型 {summary['recognition_live_calls']} 次。",
        "",
        "## 费用（公开价，美元）",
        "",
        "| | 请求 | 输入 token | 缓存命中 | 输出 token | 其中推理 | 费用 | 无用量 / 无价格 |",
        "|---|---|---|---|---|---|---|---|",
        _usage_row("合计", usage["all"]),
        _usage_row("检查", usage["by_stage"]["check"]),
        _usage_row("复核", usage["by_stage"]["review"]),
        *[_usage_row(name, item) for name, item in usage["by_model"].items()],
        "",
        f"每份文件（中位 / P90 / 最大）：检查 ${per_file['check']['median']:.3f} / ${per_file['check']['p90']:.3f} / ${per_file['check']['max']:.3f}，"
        f"复核 ${per_file['review']['median']:.3f} / ${per_file['review']['p90']:.3f} / ${per_file['review']['max']:.3f}；"
        f"请求数 {summary['requests_per_file']['median']:.0f} / {summary['requests_per_file']['p90']:.0f} / {summary['requests_per_file']['max']:.0f}，"
        f"单轮最多 {summary['requests_per_case_max']} 次。",
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
    if summary["case_errors"]:
        lines += ["", "## 用例错误", ""] + [f"- {case}：{error}" for case, error in summary["case_errors"].items()]
    return "\n".join(lines) + "\n"


def report(run: Path) -> dict:
    lines = [json.loads(line) for line in (run / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8")) if (run / "meta.json").is_file() else {}
    summary = summarize(lines, json.loads(PRICES.read_text(encoding="utf-8"))["models"])
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "summary.md").write_text(render(summary, meta), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="execute cases inside the deep image")
    run.add_argument("--out", type=Path, required=True)
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
    cases = _cases()
    if args.list:
        for index, case in enumerate(cases):
            print(index, case.id)
        return 0
    shard, shards = (int(part) for part in args.shard.split("/"))
    indexes = [index for index in (args.case or range(len(cases))) if index % shards == shard]
    return run_cases(indexes, args.out)


if __name__ == "__main__":
    sys.exit(main())
