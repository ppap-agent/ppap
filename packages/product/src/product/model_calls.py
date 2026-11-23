"""Model call records (#360): every model call of an audit, kept by round for platform operators only.

    python -m product.model_calls <round-id>                              # list the round's calls and whether the records are complete
    python -m product.model_calls <round-id> --export <directory>         # write records, images and replay.json there
    python -m product.model_calls <round-id> --compare <round-id> [--clause U08:2]   # each check item's model inputs and outputs, side by side

Records live in object storage under ``model-calls/<tenant>/<round>/<attempt>/<sequence>.json``; images once per
round under ``model-calls/<tenant>/<round>/images/<sha256>``. They are an audit record, never shown to customers, and
there is no web or MCP route to them. A record not stored is counted and never retried; the counts are the
attempt's ``model_calls`` diagnostic in ``engine.attempts``, and a round whose records are incomplete says so here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from audit.storage_errors import ObjectExists

PREFIX = "model-calls"
_RECORD_KEY = re.compile(r"^(?P<attempt>[1-9][0-9]*)/(?P<sequence>[0-9]{6,})\.json$")
_MAX_ERRORS = 20


def round_prefix(tenant_id: str, round_id: str) -> str:
    return f"{PREFIX}/{UUID(tenant_id)}/{UUID(round_id)}"


def _content_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/octet-stream"


class RoundCallLog:
    """The host's store of one execution attempt's records; the local model gateway hands it every call.

    ``write`` only accepts a record, so a slow or failing object store never holds up a model call; the log's
    own thread stores records in call order. What could not be stored is counted, never retried.
    """

    def __init__(
        self,
        objects: Any,
        *,
        tenant_id: str,
        round_id: str,
        attempt: int,
        on_close: Callable[[dict[str, Any]], None] | None = None,
        max_queued_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if type(attempt) is not int or attempt < 1:
            raise ValueError("invalid_attempt")
        self.objects, self.attempt, self.on_close, self.max_queued_bytes = objects, attempt, on_close, max_queued_bytes
        self.root = round_prefix(tenant_id, round_id)
        self.identity = {"tenant_id": str(UUID(tenant_id)), "round_id": str(UUID(round_id)), "attempt": attempt}
        self.condition = threading.Condition()
        self.sequence = self.recorded = self.failed = self.pending = self.queued_bytes = 0
        self.queue: deque[tuple[int, bytes, dict[str, bytes], int]] = deque()
        self.writing = self.closed = False
        self.writer: threading.Thread | None = None
        self.errors: list[str] = []
        # Provider requests the gateway forwarded for each direct call (one, or two after an output correction).
        self.seen_calls: Counter[str] = Counter()
        self.received_calls: set[str] = set()
        self.images: set[str] = set()
        # The task under way, as the guest last reported it: what a request recorded at the gateway served.
        self.scope: dict[str, str] = {}

    def seen(self, call_id: str) -> None:
        with self.condition:
            self.seen_calls[call_id] += 1

    def begin(self) -> None:
        with self.condition:
            self.pending += 1

    def progress(self, state: Mapping[str, Any]) -> None:
        scope = {key: str(state[key]) for key in ("stage", "target") if state.get(key)}
        if scope:
            with self.condition:
                self.scope = scope

    def write(self, record: dict[str, Any], images: Mapping[str, bytes], *, exchange: bool = False) -> None:
        """Accept one record. A direct call's record must name a call the gateway forwarded, once."""
        call_id = str(record.get("call_id"))
        with self.condition:
            try:
                if not exchange:
                    if call_id not in self.seen_calls or call_id in self.received_calls:
                        raise ValueError("unknown_model_call")
                    self.received_calls.add(call_id)
                self.sequence += 1
                own = {"scope": dict(self.scope)} if exchange else {"provider_requests": self.seen_calls[call_id]}
                stored = {**record, **own, **self.identity, "sequence": self.sequence}
                body = json.dumps(stored, ensure_ascii=False, sort_keys=True).encode()
                size = len(body) + sum(len(image) for image in images.values())
                if self.closed or self.queued_bytes + size > self.max_queued_bytes:
                    self._lost("model_call_log_closed" if self.closed else "model_call_log_full")
                    raise OSError("model_call_record_not_queued")
                self.queue.append((self.sequence, body, dict(images), size))
                self.queued_bytes += size
                if self.writer is None:
                    self.writer = threading.Thread(target=self._drain, name="ppap-model-call-log", daemon=True)
                    self.writer.start()
                self.condition.notify_all()
            finally:
                if exchange:
                    self.pending -= 1
                    self.condition.notify_all()

    def _lost(self, error: str) -> None:
        self.failed += 1
        if len(self.errors) < _MAX_ERRORS:
            self.errors.append(error)

    def _drain(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: bool(self.queue) or self.closed)
                if not self.queue:
                    return
                sequence, body, images, size = self.queue.popleft()
                self.writing = True
            try:
                for digest, image in images.items():
                    with self.condition:
                        if digest in self.images:
                            continue
                    try:
                        self.objects.put_exclusive(f"{self.root}/images/{digest}", image, _content_type(image))
                    except ObjectExists:
                        pass  # Another attempt of the round stored the same image.
                    with self.condition:
                        self.images.add(digest)
                self.objects.put_exclusive(f"{self.root}/{self.attempt}/{sequence:06d}.json", body, "application/json")
            except Exception as exc:  # noqa: BLE001 - counted, never retried
                with self.condition:
                    self._lost(type(exc).__name__)
            else:
                with self.condition:
                    self.recorded += 1
            finally:
                with self.condition:
                    self.queued_bytes -= size
                    self.writing = False
                    self.condition.notify_all()

    def summary(self) -> dict[str, Any]:
        with self.condition:
            return {"recorded": self.recorded, "failed": self.failed, "missing": len(set(self.seen_calls) - self.received_calls), "errors": list(self.errors)}

    def close(self, timeout: float = 30) -> dict[str, Any]:
        """Wait (bounded) for records still on their way, then keep the counts as the attempt's diagnostic."""
        with self.condition:
            self.condition.wait_for(lambda: self.pending <= 0 and not self.queue and not self.writing, timeout=timeout)
            unfinished = self.pending + len(self.queue) + int(self.writing)
            # What was not stored in time is given up, not stored later behind the diagnostic.
            self.queue.clear()
            self.closed = True
            self.condition.notify_all()
        summary = self.summary()
        if unfinished:
            summary["unfinished"] = unfinished
        if self.on_close is not None:
            try:
                self.on_close(summary)
            except Exception:  # noqa: BLE001 - a lost diagnostic never changes the audit
                pass
        return summary


def write_attempt_summary(settings: Any, job_id: str, attempt: int, summary: Mapping[str, Any]) -> None:
    """The attempt's record counts join its execution diagnostic in ``engine``."""
    from psycopg.types.json import Jsonb

    from product.db import connect

    if not job_id:
        return
    with connect(settings, "worker") as conn:
        conn.execute(
            "UPDATE engine.attempts SET diagnostic = diagnostic || %s WHERE job_id = %s AND attempt = %s",
            (Jsonb({"model_calls": dict(summary)}), str(UUID(job_id)), attempt),
        )


# ---------------------------------------------------------------------------
# Operator view


def read_round(settings: Any, round_id: str) -> tuple[str, list[dict[str, Any]]]:
    """The round's tenant and its audit attempts with their record counts, as the execution kept them."""
    from product.db import connect

    with connect(settings, "api") as conn:
        row = conn.execute("SELECT tenant_id FROM product.audit_rounds WHERE id = %s", (str(UUID(round_id)),)).fetchone()
        if row is None:
            raise LookupError("round_not_found")
        rows = conn.execute(
            """SELECT a.attempt, a.outcome, a.diagnostic -> 'model_calls', a.diagnostic #> '{usage,model_requests}' FROM engine.attempts a
            JOIN engine.jobs j ON j.id = a.job_id
            WHERE j.audit_round_id = %s AND j.kind = 'audit' ORDER BY a.attempt""",
            (str(UUID(round_id)),),
        ).fetchall()
    return str(row[0]), [
        {"attempt": int(attempt), "outcome": outcome, "summary": summary if isinstance(summary, dict) else None, "model_requests": requests if type(requests) is int else None}
        for attempt, outcome, summary, requests in rows
    ]


def read_records(objects: Any, tenant_id: str, round_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Every record of the round in attempt and call order, and the images they name."""
    root = round_prefix(tenant_id, round_id) + "/"
    records, images = [], []
    for key in objects.list(root):
        relative = key[len(root) :]
        if relative.startswith("images/"):
            images.append(relative)
            continue
        match = _RECORD_KEY.match(relative)
        if match:
            record = json.loads(objects.get(key))
            record.setdefault("attempt", int(match["attempt"]))
            record.setdefault("sequence", int(match["sequence"]))
            records.append(record)
    records.sort(key=lambda record: (record["attempt"], record["sequence"]))
    return records, sorted(images)


def inventory(round_id: str, tenant_id: str, attempts: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    """What the round's calls were, and whether every attempt's records are all there."""
    notices: list[str] = []
    by_attempt: dict[int, list[int]] = {}
    covered: Counter[int] = Counter()
    for record in records:
        by_attempt.setdefault(int(record["attempt"]), []).append(int(record["sequence"]))
        # Provider requests a record covers: a direct call's own count, one for a request recorded at the gateway.
        covered[int(record["attempt"])] += int(record.get("provider_requests") or 1)
    states = []
    for attempt in sorted({item["attempt"] for item in attempts} | set(by_attempt)):
        listed = sorted(by_attempt.get(attempt, []))
        known = next((item for item in attempts if item["attempt"] == attempt), {})
        summary, outcome, completed = known.get("summary"), known.get("outcome"), known.get("model_requests")
        gaps = sorted(set(range(1, (listed[-1] if listed else 0) + 1)) - set(listed))
        if summary is None:
            complete = None
            notices.append(f"第 {attempt} 次执行没有记录汇总（执行中断，或早于记录功能），记录是否完整未知。")
        else:
            lost = int(summary.get("failed") or 0)
            missing = int(summary.get("missing") or 0)
            unfinished = int(summary.get("unfinished") or 0)
            # The gateway's own count of completed provider requests, when it knew every one.
            uncovered = completed - covered[attempt] if type(completed) is int and completed > covered[attempt] else 0
            complete = not (lost or missing or unfinished or gaps or uncovered or len(listed) < int(summary.get("recorded") or 0))
            if not complete:
                parts = [f"{lost} 条没有存下" if lost else "", f"{missing} 次调用没有收到记录" if missing else "", f"{unfinished} 次请求结束前没有记完" if unfinished else ""]
                parts.append(f"缺第 {'、'.join(map(str, gaps))} 条" if gaps else "")
                if len(listed) < int(summary.get("recorded") or 0):
                    parts.append(f"已记 {summary.get('recorded')} 条，现存 {len(listed)} 条")
                if uncovered:
                    parts.append(f"网关完成了 {completed} 次模型请求，记录只覆盖 {covered[attempt]} 次")
                notices.append(f"第 {attempt} 次执行的记录不全：{'；'.join(part for part in parts if part)}。")
        states.append(
            {"attempt": attempt, "outcome": outcome, "listed": len(listed), "provider_requests": covered[attempt], "model_requests": completed, "summary": summary, "gaps": gaps, "complete": complete}
        )
    if not states:
        notices.append("这一轮还没有执行，没有模型调用记录。")
    # Complete only when every attempt is known complete; one known gap makes the round incomplete.
    flags = [state["complete"] for state in states]
    complete_round = False if False in flags else (True if flags and None not in flags else None)
    return {
        "round_id": round_id,
        "tenant_id": tenant_id,
        "complete": complete_round,
        "notices": notices,
        "attempts": states,
        "calls": [_listed(record) for record in records],
    }


def _part(record: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = record.get(name)
    return value if isinstance(value, dict) else {}


def _listed(record: Mapping[str, Any]) -> dict[str, Any]:
    scope = _part(record, "scope")
    identity = _part(record, "identity")
    usage = _part(record, "usage")
    return {
        "attempt": record.get("attempt"),
        "sequence": record.get("sequence"),
        "kind": record.get("kind"),
        "task": record.get("task"),
        "purpose": record.get("purpose"),
        "target": scope.get("target"),
        "clause": scope.get("clause"),
        "stage": scope.get("stage"),
        "model": identity.get("model") or record.get("model"),
        "host": identity.get("host"),
        "code": record.get("code"),
        "provider_requests": record.get("provider_requests") or 1,
        "duration_ms": record.get("duration_ms"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "request_sha256": record.get("request_sha256"),
    }


def _answer(record: Mapping[str, Any]) -> Any:
    answer = record.get("answer")
    if isinstance(answer, str):
        try:
            return json.loads(answer)
        except ValueError:
            return answer
    return answer


def _item_key(record: Mapping[str, Any]) -> str:
    scope = _part(record, "scope")
    if scope.get("clause"):
        return str(scope["clause"])
    if scope.get("stage") == "review":
        return f"深度复核:{scope.get('target') or ''}"
    return "（无检查项）"


def _shown(record: Mapping[str, Any]) -> dict[str, Any]:
    scope = _part(record, "scope")
    shown = {name: record.get(name) for name in ("attempt", "sequence", "task", "purpose", "code")} | {"target": scope.get("target")}
    if record.get("kind") == "responses":
        return {**shown, "request": record.get("request"), "response": record.get("response")}
    return {**shown, "request": record.get("request"), "images": record.get("images"), "answer": _answer(record), "request_sha256": record.get("request_sha256")}


def _identity(record: Mapping[str, Any]) -> tuple[str, str]:
    digest = record.get("request_sha256")
    if not isinstance(digest, str):
        digest = json.dumps(record.get("request"), sort_keys=True, ensure_ascii=False)
    answer = record.get("answer") if record.get("kind") != "responses" else json.dumps(record.get("response"), sort_keys=True, ensure_ascii=False)
    return digest, json.dumps(answer, ensure_ascii=False)


def result_attempt(attempts: list[dict[str, Any]], records: list[dict[str, Any]]) -> int | None:
    """The attempt whose result the round shows; otherwise the latest one with records."""
    succeeded = [item["attempt"] for item in attempts if item["outcome"] == "succeeded"]
    if succeeded:
        return max(succeeded)
    return max((int(record["attempt"]) for record in records), default=None)


def compare(left: Iterable[dict[str, Any]], right: Iterable[dict[str, Any]], *, clause: str | None = None) -> list[dict[str, Any]]:
    """Each check item's model inputs and outputs in both rounds, and where they first differ."""
    groups: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for side, records in enumerate((left, right)):
        for record in records:
            groups.setdefault(_item_key(record), ([], []))[side].append(record)
    items = []
    for key in sorted(groups):
        if clause is not None and key != clause:
            continue
        mine, theirs = groups[key]
        inputs = Counter(_identity(record)[0] for record in mine) == Counter(_identity(record)[0] for record in theirs)
        outputs = Counter(_identity(record) for record in mine) == Counter(_identity(record) for record in theirs)
        if not mine or not theirs:
            finding = "只有一轮为这一项调用了模型"
        elif not inputs:
            finding = "两轮给模型的输入不同"
        elif not outputs:
            finding = "两轮给模型的输入相同，模型的回答不同"
        else:
            finding = "两轮给模型的输入和模型的回答都相同"
        sides = {"left": [_shown(record) for record in mine], "right": [_shown(record) for record in theirs]}
        items.append({"item": key, "same_input": inputs, "same_output": inputs and outputs, "finding": finding, **sides})
    return items


def export(objects: Any, tenant_id: str, round_id: str, records: list[dict[str, Any]], images: list[str], directory: Path) -> dict[str, Any]:
    """The round's records and images as stored, and replay.json: each answered direct call as offline replay reads it."""
    root = round_prefix(tenant_id, round_id)
    for record in records:
        path = directory / str(record["attempt"]) / f"{int(record['sequence']):06d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for relative in images:
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(objects.get(f"{root}/{relative}"))
    replay = [
        {name: record[name] for name in ("kind", "model", "request", "request_sha256", "answer")}
        for record in records
        if record.get("kind") in {"text", "ocr"} and record.get("code") == "ok" and isinstance(record.get("answer"), str)
    ]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "replay.json").write_text(json.dumps(replay, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"directory": str(directory), "records": len(records), "images": len(images), "replay_entries": len(replay)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("round_id")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--export", type=Path, metavar="DIRECTORY")
    action.add_argument("--compare", metavar="ROUND_ID")
    parser.add_argument("--clause", help="with --compare: only this check item, for example U08:2")
    args = parser.parse_args(argv)
    if args.clause and not args.compare:
        parser.error("--clause needs --compare")
    from product.settings import load_settings
    from product.storage import S3Objects

    settings = load_settings()
    objects = S3Objects(settings)
    try:
        tenant_id, attempts = read_round(settings, args.round_id)
        records, images = read_records(objects, tenant_id, args.round_id)
        if args.export:
            result = export(objects, tenant_id, args.round_id, records, images, args.export)
        elif args.compare:
            other_tenant, other_attempts = read_round(settings, args.compare)
            other, _ = read_records(objects, other_tenant, args.compare)
            mine_attempt, other_attempt = result_attempt(attempts, records), result_attempt(other_attempts, other)
            result = {
                "left": {"round_id": args.round_id, "attempt": mine_attempt, "complete": inventory(args.round_id, tenant_id, attempts, records)["complete"]},
                "right": {"round_id": args.compare, "attempt": other_attempt, "complete": inventory(args.compare, other_tenant, other_attempts, other)["complete"]},
                "items": compare(
                    [record for record in records if record["attempt"] == mine_attempt],
                    [record for record in other if record["attempt"] == other_attempt],
                    clause=args.clause,
                ),
            }
        else:
            result = inventory(args.round_id, tenant_id, attempts, records)
    except (LookupError, ValueError) as exc:
        print(json.dumps({"error": str(exc) or type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
