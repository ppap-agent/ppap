"""Live task change notices over Server-Sent Events.

The database sends `audit_task_events` at commit whenever a task, its rounds, findings,
reports, jobs or job progress change (migration 0073). One listener per API process fans
the notices out to subscribers. A notice only says that a task changed: the browser reads
the task through its authorized endpoints, so nothing here is a source of results. After a
reconnect the browser reads once more, so a missed notice never leaves a stale view.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from product.store import AuthStore

CHANNEL = "audit_task_events"
HEARTBEAT_SECONDS = 15.0
COALESCE_SECONDS = 0.3
REAUTHORIZE_SECONDS = 60.0
HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
STREAM_RESPONSES: dict[int | str, dict[str, Any]] = {200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}}

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    def push(self, item: dict) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, item)


class TaskEventHub:
    """One LISTEN connection per process; subscribers by task and by (tenant, user)."""

    def __init__(self, store: AuthStore) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._tasks: dict[str, set[Subscriber]] = {}
        self._users: dict[tuple[str, str], set[Subscriber]] = {}
        self._owners: OrderedDict[str, str] = OrderedDict()
        self._lookup: psycopg.Connection | None = None
        self._thread: threading.Thread | None = None

    def subscribe_task(self, task_id: str, subscriber: Subscriber) -> None:
        with self._lock:
            self._tasks.setdefault(task_id, set()).add(subscriber)
        self._ensure_listener()

    def subscribe_user(self, tenant_id: str, user_id: str, subscriber: Subscriber) -> None:
        with self._lock:
            self._users.setdefault((tenant_id, user_id), set()).add(subscriber)
        self._ensure_listener()

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            for task_id in [key for key, members in self._tasks.items() if subscriber in members]:
                self._tasks[task_id].discard(subscriber)
                if not self._tasks[task_id]:
                    del self._tasks[task_id]
            for user in [key for key, members in self._users.items() if subscriber in members]:
                self._users[user].discard(subscriber)
                if not self._users[user]:
                    del self._users[user]

    def _ensure_listener(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._listen, name="task-events", daemon=True)
                self._thread.start()

    def _listen(self) -> None:
        delay = 1.0
        while True:
            try:
                with psycopg.connect(self.store.settings.dsn("api"), autocommit=True) as conn:
                    conn.execute("LISTEN " + CHANNEL)
                    delay = 1.0
                    # Notices sent while the listener was down are gone; every view reads again.
                    self._broadcast({"kind": "resync"})
                    while True:
                        for notice in conn.notifies(timeout=30):
                            self._dispatch(notice.payload)
            except Exception:  # noqa: BLE001 - the listener must outlive database restarts
                logger.warning("task_events_listener_reconnect", exc_info=True)
                time.sleep(delay)
                delay = min(delay * 2, 30.0)

    def _broadcast(self, item: dict) -> None:
        with self._lock:
            members = {member for table in (self._tasks, self._users) for group in table.values() for member in group}
        for member in members:
            member.push(item)

    def _dispatch(self, payload: str) -> None:
        try:
            body = json.loads(payload)
            task_id, tenant_id, kind = str(body["task"]), str(body.get("tenant") or ""), str(body.get("kind") or "state")
        except (ValueError, KeyError, TypeError):
            return
        with self._lock:
            task_members = set(self._tasks.get(task_id, ()))
            user_keys = [key for key in self._users if key[0] == tenant_id]
        item = {"kind": kind, "task": task_id}
        for member in task_members:
            member.push(item)
        if user_keys:
            owner = self._owner(task_id)
            with self._lock:
                members = set(self._users.get((tenant_id, owner), ())) if owner else set()
            for member in members:
                member.push(item)

    def _owner(self, task_id: str) -> str | None:
        """The task's creator, whose task list shows it. The list read itself applies authorization."""
        if task_id in self._owners:
            self._owners.move_to_end(task_id)
            return self._owners[task_id]
        row = None
        # Called only from the listener thread: one lookup connection, rebuilt once on failure.
        for attempt in range(2):
            try:
                if self._lookup is None or self._lookup.closed:
                    self._lookup = psycopg.connect(self.store.settings.dsn("api"), autocommit=True)
                row = self._lookup.execute("SELECT created_by FROM product.audit_drafts WHERE id = %s", (task_id,)).fetchone()
                break
            except Exception:  # noqa: BLE001 - an unknown owner only skips this list notice
                logger.warning("task_events_owner_lookup_failed", extra={"task_id": task_id, "attempt": attempt}, exc_info=True)
                if self._lookup is not None:
                    self._lookup.close()
                self._lookup = None
        if not row or row[0] is None:
            # Not cached: a missing row may be a task whose creating transaction is still in flight.
            return None
        owner = str(row[0])
        self._owners[task_id] = owner
        while len(self._owners) > 4096:
            self._owners.popitem(last=False)
        return owner


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream(request: Request, subscriber: Subscriber, still_allowed, unsubscribe):
    """Ready first; then coalesced change notices; heartbeats keep proxies from idling out."""
    try:
        yield "retry: 3000\n" + _frame("ready", {})
        checked = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            try:
                item = await asyncio.wait_for(subscriber.queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                item = None
            if time.monotonic() - checked >= REAUTHORIZE_SECONDS:
                checked = time.monotonic()
                if not await asyncio.to_thread(still_allowed):
                    yield _frame("end", {"reason": "unauthorized"})
                    return
            if item is None:
                continue
            kinds, tasks = {item["kind"]}, {item.get("task")}
            await asyncio.sleep(COALESCE_SECONDS)
            while not subscriber.queue.empty():
                more = subscriber.queue.get_nowait()
                kinds.add(more["kind"])
                tasks.add(more.get("task"))
            yield _frame("change", {"kinds": sorted(kinds), "tasks": sorted(task for task in tasks if task)})
    finally:
        unsubscribe()


def register_task_event_routes(app: FastAPI, store: AuthStore, principal_of) -> TaskEventHub:
    hub = TaskEventHub(store)

    @app.get("/audit-tasks/{task_id}/events", response_class=StreamingResponse, responses=STREAM_RESPONSES)
    async def audit_task_events(task_id: str, request: Request):
        # The session lookup reads the database; keep it off the event loop.
        principal = await asyncio.to_thread(principal_of, request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)

        def allowed() -> bool:
            current = principal_of(request)
            if not current:
                return False
            try:
                # The task read applies tenant and object authorization.
                store.get_audit_task(current, task_id)
            except (KeyError, ValueError):
                return False
            return True

        if not await asyncio.to_thread(allowed):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        subscriber = Subscriber(asyncio.get_running_loop())
        hub.subscribe_task(task_id, subscriber)
        return StreamingResponse(_stream(request, subscriber, allowed, lambda: hub.unsubscribe(subscriber)), media_type="text/event-stream", headers=HEADERS)

    @app.get("/audit-task-events", response_class=StreamingResponse, responses=STREAM_RESPONSES)
    async def audit_task_list_events(request: Request):
        # The session lookup reads the database; keep it off the event loop.
        principal = await asyncio.to_thread(principal_of, request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        subscriber = Subscriber(asyncio.get_running_loop())
        hub.subscribe_user(str(principal.tenant_id), str(principal.user_id), subscriber)

        def allowed() -> bool:
            current = principal_of(request)
            return bool(current and str(current.tenant_id) == str(principal.tenant_id) and str(current.user_id) == str(principal.user_id))

        return StreamingResponse(_stream(request, subscriber, allowed, lambda: hub.unsubscribe(subscriber)), media_type="text/event-stream", headers=HEADERS)

    return hub
