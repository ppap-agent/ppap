"""Deep mode on a UAES rulebook revision's benchmark, with the installed checkers, through the production deep path.

    run     inside the deep image, as root (bench/deep-audit/run.sh starts it); JSON lines per batch
    report  anywhere; summarizes one run directory into summary.json and summary.md

The checkers are always the installed leaves (packages/audit/src/audit/checkers/uaes), what customers run. The revision
names only what they are read against: the benchmark of that revision (candidates/m<N>/benchmark: its batches, originals
and model reference judgments) and its signed clause text (m<N>/条文). Installed clause text that differs from the
revision's is listed and its rows marked; nothing falls back to another revision.

Each benchmark batch is resubmitted from its originals and submission roles alone, through the product path
(recognition, precheck) as a sample batch is; only the audit runs the way the Controller does: the bound local gateway,
ContainerRuntime (the guest runs as uid 10001 under prlimit), then CoreRuntime.execute_snapshot in deep mode: one guest
for the round, each file checked and then reviewed with its own time. A review that does not finish leaves that file
with its program result. Every model request of the audit passes the gateway this process hosts; each is recorded with
its use, model, effort and provider usage, and charged to the file and stage (check or review) under way when it was
sent. The reference judgments are a model's, not human truth: the report lays differences out and computes no accuracy.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import statistics
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RULEBOOK = ROOT / "assets/uaes/rulebook/v1.0"
PRICES = Path(__file__).with_name("prices.json")


def revision_paths(revision: str) -> tuple[Path, Path]:
    """The revision's benchmark and signed clause text; a revision without either cannot be benched."""
    benchmark, clauses = RULEBOOK / "candidates" / revision / "benchmark", RULEBOOK / revision / "条文"
    for path, what in ((benchmark / "reference-judgments.jsonl", "基准"), (clauses, "签署条文")):
        if not path.exists():
            raise SystemExit(f"修订 {revision} 没有{what}：{path.relative_to(ROOT)}")
    return benchmark, clauses


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def clause_check(revision: str) -> dict[str, Any]:
    """Installed clause text against the revision's: clauses whose wording differs are listed, never hidden."""
    from audit.standard_suites import predefined_directory_suites

    _benchmark, clauses = revision_paths(revision)
    signed = {str(rule["id"]): _digest(rule["rule_to_check"]) for path in sorted(clauses.glob("*.json")) for rule in json.loads(path.read_text(encoding="utf-8"))["rules"]}
    installed: dict[str, str] = {}
    leaves: dict[str, str] = {}
    for suite in predefined_directory_suites():
        if suite.source_path.startswith("uaes/"):
            leaves[suite.source_path] = suite.revision_digest
            for rule in json.loads(suite.files["rules.json"])["rules"]:
                installed[str(rule["id"])] = _digest(rule["rule_to_check"])
    return {
        "revision": revision,
        "leaves": dict(sorted(leaves.items())),
        "changed": sorted(clause for clause in installed.keys() & signed.keys() if installed[clause] != signed[clause]),
        "not_in_revision": sorted(installed.keys() - signed.keys()),
        "not_installed": sorted(signed.keys() - installed.keys()),
    }


# ---- run (inside the deep image) ------------------------------------------------------------


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


def run_batches(revision: str, names: list[str], shard: tuple[int, int], out: Path) -> int:
    import agent.execution.audit_runtime as audit_runtime
    from agent.core_program import CoreRuntime
    from agent.execution.isolation import ContainerRuntime, RuntimeConfig, local_gateway
    from agent.run import ExecutionContext
    from audit.standard_suites import predefined_directory_suites
    from corpus_contracts.candidate_run import benchmark_batches
    from corpus_contracts.replay import Recorder, install
    from corpus_contracts.uaes_samples import run_batch
    from documents.model_access import ModelUnavailable, model_access_scope
    from product.settings import load_settings
    from product.store.model_credentials import platform_selection

    benchmark, _clauses = revision_paths(revision)
    settings = load_settings()
    selection = platform_selection()
    identity = selection.identity
    policy = settings.deep_execution_policy(identity)
    suites = {suite.source_path: suite for suite in predefined_directory_suites()}
    runs = benchmark_batches(benchmark)
    unknown = set(names) - {name for name, _ in runs}
    if unknown:
        raise SystemExit(f"基准里没有这些批次：{'、'.join(sorted(unknown))}")
    runs = [(name, batch) for index, (name, batch) in enumerate(runs) if (not names or name in names) and index % shard[1] == shard[0]]
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
        for name, batch in runs:
            started = time.monotonic()
            # run_batch names files f01, f02, … in this order; rows report each by its repository path.
            sources = {f"f{index:02d}": str(path.relative_to(ROOT)) for index, path in enumerate((*batch.others, *batch.files, *batch.references), 1)}
            current: dict[str, Any] = {}

            def deep_audit(snapshot, blobs, programs, *, on_task=None, **_options):
                """The production deep execution in place of the sample runner's standard one."""
                timeline = _Timeline()
                order = [target.file_version_id for target in snapshot.targets]
                begun: list[str] = []

                def progress(update: dict) -> None:
                    timeline(update)
                    # The sample runner times each file's check from these calls, one per target in order.
                    if update.get("stage") == "check" and update.get("target") in order and update["target"] not in begun:
                        begun.append(update["target"])
                        if on_task is not None:
                            on_task(order.index(update["target"]))

                runtime = ContainerRuntime(
                    RuntimeConfig(state=state, gateway_socket=gateway.socket_path, gateway_host="127.0.0.1", **policy.model_dump()),
                    model_grant_binding=lambda execution_id: gateway.http.grants.bind(execution_id, identity, selection.access),
                )
                audit_started = time.monotonic()
                try:
                    result, deep, reviews = CoreRuntime(runtime, None, None).execute_snapshot(
                        dataclasses.replace(snapshot, model_identity=identity), blobs, programs, ExecutionContext(on_progress=progress), analysis_mode="deep"
                    )
                finally:
                    gateway.http.grants.revoke_all()
                    for target in order:
                        if target not in begun and on_task is not None:
                            begun.append(target)
                            on_task(order.index(target))
                spans = timeline.seconds(time.monotonic())
                current.update(
                    audit_seconds=round(time.monotonic() - audit_started, 1),
                    deep=deep or {},
                    reviews=reviews,
                    targets=[
                        {
                            "source": sources[target],
                            "state": (deep or {}).get(target, {}).get("state"),
                            "reason": (deep or {}).get(target, {}).get("reason", ""),
                            "check_seconds": spans.get(("check", target)),
                            "review_seconds": spans.get(("review", target)),
                        }
                        for target in order
                    ],
                    requests=[{**request, "target": sources.get(request["target"], "")} for request in requests.take(timeline)],
                )
                return result

            line: dict[str, Any] = {"kind": "deep", "batch": name}
            rows: list[dict[str, Any]] = []
            patch = _Patch()
            recorder = Recorder(batch.id, output=out / "unsaved-replay")
            try:
                # Recognition and precheck answer from the corpus recordings where one matches, live otherwise; nothing is saved.
                install(patch, recorder)
                patch.setattr(audit_runtime, "run_audit", deep_audit)
                with model_access_scope(selection.access):
                    found = run_batch(batch, suites)
                for row in found:
                    row = {**row, "batch": name}
                    if row["kind"] == "finding":
                        version = next(key for key, path in sources.items() if path == row["source"])
                        record = current.get("reviews", {}).get((version, row["clause"]), {})
                        target = current.get("deep", {}).get(version, {})
                        row.update(
                            # A clause the reviewer did not record kept the program verdict.
                            program=record.get("program_status") or row["status"],
                            decision=record.get("decision"),
                            proposed=record.get("proposed_status") or record.get("status"),
                            rejection=record.get("rejection"),
                            review_reason=str(record.get("reason") or "")[:400],
                            target_state=target.get("state"),
                            target_reason=target.get("reason", ""),
                        )
                    if row["kind"] in {"finding", "recognition", "timing"}:
                        rows.append(row)
            except Exception as exc:  # noqa: BLE001 — one batch never stops the others; its rows stay "没有输出".
                failures += 1
                requests.take(_Timeline())
                line.update(error=f"{type(exc).__name__}: {str(exc)[:300]}", trace=traceback.format_exc()[-2000:])
            finally:
                patch.undo()
                line["recognition_live_calls"] = len(recorder.pending)
                recorder.finish(keep=False)
            line.update(seconds=round(time.monotonic() - started, 1), **{key: current[key] for key in ("audit_seconds", "targets", "requests") if key in current})
            with (out / "results.jsonl").open("a", encoding="utf-8") as stream:
                stream.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in [*rows, line])
            print(f"{name}: {line.get('error') or 'ok'} ({line['seconds']}s)", flush=True)
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


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(lines: list[dict], benchmark: Path, changed: set[str], prices: dict) -> dict:
    """The program's and the reviewed verdicts beside the reference judgments, over the benchmark's fixed expected rows."""
    from corpus_contracts.benchmark_diff import CATEGORIES, category

    expected = _jsonl(benchmark / "expected-rows.jsonl")
    reference = {(row["batch"], row["file"], row["clause"]): row for row in _jsonl(benchmark / "reference-judgments.jsonl")}
    findings = {(row["batch"], row["source"], row["clause"]): row for row in lines if row.get("kind") == "finding"}
    deep_lines = [line for line in lines if line.get("kind") == "deep"]
    ran = {line["batch"] for line in deep_lines}
    targets = [target for line in deep_lines for target in line.get("targets", [])]
    requests = [request for line in deep_lines for request in line.get("requests", [])]
    totals: Counter[str] = Counter()
    program_categories: Counter[str] = Counter()
    deep_categories: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = {}
    transitions: Counter[str] = Counter()
    details: dict[str, list[dict]] = {"new_pass_against_reference": [], "broke": [], "rejected": []}
    for row in expected:
        if row["batch"] not in ran:
            continue
        key = (row["batch"], row["file"], row["clause"])
        found = findings.get(key) or {}
        verdict = reference[key]["verdict"]
        deep = found.get("status")
        program = found.get("program") or deep
        detail = {**row, "reference": verdict, "program": program, "deep": deep, **{k: found.get(k) for k in ("decision", "proposed", "rejection", "review_reason", "target_state")}}
        program_categories[category(program, verdict)] += 1
        deep_categories[category(deep, verdict)] += 1
        for counter in (totals, by_type.setdefault(row.get("document_type") or "?", Counter())):
            counter["rows"] += 1
            counter["program_agrees"] += program == verdict
            counter["deep_agrees"] += deep == verdict
        totals["clause_changed"] += row["clause"] in changed
        if program != deep:
            kind = "fixed" if deep == verdict else "broke" if program == verdict else "wrong_to_wrong"
            totals[kind] += 1
            transitions[f"{program}->{deep} {kind}"] += 1
            if kind == "broke":
                details["broke"].append(detail)
            # The direction deep mode must not take: a new PASS the reference does not give.
            if deep == "PASS" and verdict != "PASS":
                totals["new_pass_against_reference"] += 1
                details["new_pass_against_reference"].append(detail)
        if program == "UNABLE":
            totals["program_unable"] += 1
            totals["unable_settled"] += deep not in {None, "UNABLE"}
            totals["unable_settled_agrees"] += deep not in {None, "UNABLE"} and deep == verdict
        if found and found.get("target_state") != "completed":
            totals["rows_not_reviewed"] += 1
        if found.get("decision") == "rejected":
            totals["corrections_rejected"] += 1
            details["rejected"].append(detail)
    # Spend per file and stage: what one target file costs to check and to review.
    per_file: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for line in deep_lines:
        for request in line.get("requests", []):
            per_file.setdefault((line["batch"], request["target"]), {"check": [], "review": []}).setdefault(request["stage"] or "check", []).append(request)
    seconds = [float(line["seconds"]) for line in deep_lines if not line.get("error")]
    return {
        "batches": len(deep_lines),
        "batch_errors": {line["batch"]: line["error"] for line in deep_lines if line.get("error")},
        "targets": len(targets),
        "targets_not_reviewed": dict(Counter(target["reason"] for target in targets if target["state"] != "completed")),
        **{
            key: totals[key]
            for key in (
                "rows",
                "program_agrees",
                "deep_agrees",
                "fixed",
                "broke",
                "wrong_to_wrong",
                "new_pass_against_reference",
                "program_unable",
                "unable_settled",
                "unable_settled_agrees",
                "rows_not_reviewed",
                "corrections_rejected",
                "clause_changed",
            )
        },
        "categories": {name: {"program": program_categories[name], "deep": deep_categories[name]} for name in CATEGORIES},
        "transitions": dict(sorted(transitions.items())),
        "by_document_type": {kind: dict(counter) for kind, counter in sorted(by_type.items())},
        "seconds_per_batch": _spread(seconds),
        "check_seconds_per_file": _spread([target["check_seconds"] for target in targets if target.get("check_seconds") is not None]),
        "review_seconds_per_file": _spread([target["review_seconds"] for target in targets if target.get("review_seconds") is not None]),
        "recognition_live_calls": sum(line.get("recognition_live_calls") or 0 for line in deep_lines),
        "usage": {
            "all": _usage(requests, prices),
            "by_stage": {stage: _usage([request for request in requests if (request["stage"] or "check") == stage], prices) for stage in ("check", "review")},
            "by_model": {
                f"{model} / {effort or '默认'}": _usage([r for r in requests if (r["model"], r["effort"]) == (model, effort)], prices)
                for model, effort in sorted({(r["model"] or "", r["effort"] or "") for r in requests})
            },
        },
        "usd_per_file": {stage: _spread([_usage(stages.get(stage, []), prices)["usd"] for stages in per_file.values()]) for stage in ("check", "review")},
        "requests_per_batch_max": max((len(line.get("requests", [])) for line in deep_lines), default=0),
        "details": details,
    }


def _detail(row: dict) -> str:
    proposed = f"，提议 {row.get('proposed')}（{row.get('rejection')}）" if row.get("decision") == "rejected" else ""
    return f"- {row['batch']} 《{Path(row['file']).name}》 {row['clause']}：参考 {row['reference']}，程序 {row['program']}，复核后 {row['deep']}{proposed}。{row.get('review_reason') or ''}"


def _usage_row(name: str, usage: dict) -> str:
    cached = usage["cached_tokens"] / usage["input_tokens"] if usage["input_tokens"] else 0.0
    return (
        f"| {name} | {usage['requests']} | {usage['input_tokens']:,} | {cached:.0%} | {usage['output_tokens']:,} | {usage['reasoning_tokens']:,} | "
        f"${usage['usd']:.2f} | {usage['requests_without_usage']} / {usage['unpriced_requests']} |"
    )


def render(summary: dict, meta: dict) -> str:
    clauses = meta.get("clauses") or {}
    lines = [f"# 深度审核评估 {meta.get('name', '')}", ""]
    lines += [f"- {key}：{value}" for key, value in meta.items() if key not in {"name", "clauses"}]
    lines += [
        "",
        f"检查程序：已安装的 UAES 叶子（{len(clauses.get('leaves', {}))} 个）。对照修订 {clauses.get('revision', '?')} 的基准与签署条文；参考判定是模型判定，不是人工真值，只看差异。",
    ]
    if clauses.get("changed"):
        lines.append(f"已安装条文与该修订写法不同的条款（{len(clauses['changed'])} 条，涉及 {summary['clause_changed']} 行，仍参与对照）：{'、'.join(clauses['changed'])}。")
    for key, label in (("not_in_revision", "该修订没有的已安装条款"), ("not_installed", "该修订有、未安装的条款")):
        if clauses.get(key):
            lines.append(f"{label}：{'、'.join(clauses[key])}。")
    rows = summary["rows"] or 1
    check, review, batch = summary["check_seconds_per_file"], summary["review_seconds_per_file"], summary["seconds_per_batch"]
    usage, per_file = summary["usage"], summary["usd_per_file"]
    lines += [
        "",
        "| 批次 | 目标文件 | 条款行 | 程序与参考一致 | 复核后与参考一致 | 改成一致 | 改成不一致 | 改了仍不一致 | 新增与参考不符的 PASS | 未复核条款 |",
        "|---|---|---|---|---|---|---|---|---|---|",
        f"| {summary['batches']} | {summary['targets']} | {summary['rows']} | {summary['program_agrees']}（{summary['program_agrees'] / rows:.1%}） | "
        f"{summary['deep_agrees']}（{summary['deep_agrees'] / rows:.1%}） | {summary['fixed']} | {summary['broke']} | {summary['wrong_to_wrong']} | "
        f"{summary['new_pass_against_reference']} | {summary['rows_not_reviewed']} |",
        "",
        f"程序待确认 {summary['program_unable']} 条，复核后给出结论 {summary['unable_settled']} 条，"
        f"其中与参考一致 {summary['unable_settled_agrees']} 条；订正被拒 {summary['corrections_rejected']} 条。",
        "",
        "## 与参考的分歧",
        "",
        "| 类别 | 程序 | 复核后 |",
        "|---|---|---|",
        *[f"| {name} | {item['program']} | {item['deep']} |" for name, item in summary["categories"].items()],
        "",
        "## 时间",
        "",
        "| 中位 / P90 / 最大（秒） | |",
        "|---|---|",
        f"| 每份检查 | {check['median']:.0f} / {check['p90']:.0f} / {check['max']:.0f} |",
        f"| 每份复核 | {review['median']:.0f} / {review['p90']:.0f} / {review['max']:.0f} |",
        f"| 每批（含识别与预检） | {batch['median']:.0f} / {batch['p90']:.0f} / {batch['max']:.0f} |",
        "",
        f"未复核目标：{json.dumps(summary['targets_not_reviewed'], ensure_ascii=False)}；识别与预检未命中录音、现调模型 {summary['recognition_live_calls']} 次。",
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
        f"复核 ${per_file['review']['median']:.3f} / ${per_file['review']['p90']:.3f} / ${per_file['review']['max']:.3f}；单批最多 {summary['requests_per_batch_max']} 次请求。",
        "",
        "## 按文档类型",
        "",
        "| 类型 | 条款行 | 程序与参考一致 | 复核后与参考一致 |",
        "|---|---|---|---|",
    ]
    lines += [f"| {kind} | {item.get('rows', 0)} | {item.get('program_agrees', 0)} | {item.get('deep_agrees', 0)} |" for kind, item in summary["by_document_type"].items()]
    lines += ["", "## 结论变化", ""] + ([f"- {key}：{value}" for key, value in summary["transitions"].items()] or ["- 无"])
    for key, title in (("new_pass_against_reference", "新增与参考不符的 PASS"), ("broke", "改成不一致"), ("rejected", "订正被拒")):
        lines += ["", f"## {title}", ""] + ([_detail(row) for row in summary["details"][key]] or ["- 无"])
    if summary["batch_errors"]:
        lines += ["", "## 批次错误", ""] + [f"- {name}：{error}" for name, error in summary["batch_errors"].items()]
    return "\n".join(lines) + "\n"


def report(run: Path) -> dict:
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    benchmark, _clauses = revision_paths(meta["revision"])
    summary = summarize(_jsonl(run / "results.jsonl"), benchmark, set((meta.get("clauses") or {}).get("changed", [])), json.loads(PRICES.read_text(encoding="utf-8"))["models"])
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "summary.md").write_text(render(summary, meta), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="execute benchmark batches inside the deep image")
    run.add_argument("--revision", required=True, help="the rulebook revision read against, e.g. m4: its benchmark and signed clauses")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--shard", default="0/1", help="k/n: run batches whose position modulo n is k")
    run.add_argument("--batch", action="append", default=[], help="only this benchmark batch (repeatable)")
    clauses = commands.add_parser("clauses", help="print installed clause text against the revision's, as JSON")
    clauses.add_argument("--revision", required=True)
    summary = commands.add_parser("report", help="summarize one run directory")
    summary.add_argument("run", type=Path)
    args = parser.parse_args(argv)
    if args.command == "report":
        result = report(args.run)
        print(json.dumps({key: value for key, value in result.items() if key != "details"}, ensure_ascii=False, indent=1))
        return 0
    if args.command == "clauses":
        print(json.dumps(clause_check(args.revision), ensure_ascii=False))
        return 0
    shard, shards = (int(part) for part in args.shard.split("/"))
    return run_batches(args.revision, args.batch, (shard, shards), args.out)


if __name__ == "__main__":
    sys.exit(main())
