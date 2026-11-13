import asyncio
import json
import uuid

import pytest
from api import task_events
from api.task_events import Subscriber, TaskEventHub, _stream


class FakeRequest:
    def __init__(self) -> None:
        self.closed = False

    async def is_disconnected(self) -> bool:
        return self.closed


def test_a_change_reaches_the_task_and_its_owner_but_not_other_users():
    async def scenario():
        hub = TaskEventHub(store=None)  # type: ignore[arg-type]
        hub._ensure_listener = lambda: None  # type: ignore[method-assign]
        hub._owner = lambda task_id: "owner"  # type: ignore[method-assign]
        loop = asyncio.get_running_loop()
        on_task, owner, other_user, other_task = (Subscriber(loop) for _ in range(4))
        hub.subscribe_task("t1", on_task)
        hub.subscribe_task("t2", other_task)
        hub.subscribe_user("tenant", "owner", owner)
        hub.subscribe_user("tenant", "someone", other_user)
        hub._dispatch(json.dumps({"tenant": "tenant", "task": "t1", "kind": "progress"}))
        await asyncio.sleep(0)
        assert on_task.queue.get_nowait() == {"kind": "progress", "task": "t1"}
        assert owner.queue.get_nowait() == {"kind": "progress", "task": "t1"}
        assert other_user.queue.empty() and other_task.queue.empty()
        hub.unsubscribe(on_task)
        hub._dispatch(json.dumps({"tenant": "tenant", "task": "t1", "kind": "state"}))
        await asyncio.sleep(0)
        assert on_task.queue.empty()

    asyncio.run(scenario())


def test_the_stream_starts_ready_coalesces_changes_and_keeps_alive(monkeypatch):
    monkeypatch.setattr(task_events, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(task_events, "COALESCE_SECONDS", 0.01)

    async def scenario():
        request, subscriber, unsubscribed = FakeRequest(), Subscriber(asyncio.get_running_loop()), []
        frames = _stream(request, subscriber, lambda: True, lambda: unsubscribed.append(True))
        assert "event: ready" in await frames.__anext__()
        assert await frames.__anext__() == ": ping\n\n"
        subscriber.queue.put_nowait({"kind": "progress", "task": "t1"})
        subscriber.queue.put_nowait({"kind": "state", "task": "t1"})
        change = await frames.__anext__()
        assert change.startswith("event: change") and json.loads(change.split("data: ", 1)[1]) == {"kinds": ["progress", "state"], "tasks": ["t1"]}
        request.closed = True
        with pytest.raises(StopAsyncIteration):
            await frames.__anext__()
        assert unsubscribed == [True]

    asyncio.run(scenario())


def test_committed_job_changes_are_delivered_and_lease_renewals_are_not(auth_store_factory):
    store = auth_store_factory()

    async def scenario():
        hub = TaskEventHub(store)
        task_id, tenant_id, job_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        subscriber = Subscriber(asyncio.get_running_loop())
        hub.subscribe_task(task_id, subscriber)
        # The listener announces itself with a resync once LISTEN is active.
        assert await asyncio.wait_for(subscriber.queue.get(), timeout=10) == {"kind": "resync"}

        def write(sql: str, *args) -> None:
            with store._conn() as conn:
                conn.execute(sql, args)

        await asyncio.to_thread(write, "INSERT INTO engine.jobs (id, tenant_id, kind, payload, status) VALUES (%s, %s, 'audit', %s, 'queued')", job_id, tenant_id, json.dumps({"task_id": task_id}))
        assert await asyncio.wait_for(subscriber.queue.get(), timeout=10) == {"kind": "state", "task": task_id}
        await asyncio.to_thread(write, "UPDATE engine.jobs SET lease_until = now() WHERE id = %s", job_id)
        await asyncio.to_thread(write, "UPDATE engine.jobs SET status = 'leased' WHERE id = %s", job_id)
        # Only the status change arrives; the lease renewal before it sent nothing.
        assert await asyncio.wait_for(subscriber.queue.get(), timeout=10) == {"kind": "state", "task": task_id}
        assert subscriber.queue.empty()

    asyncio.run(scenario())


def test_owner_lookup_reuses_one_connection_and_never_caches_a_missing_task(auth_store_factory):
    hub = TaskEventHub(auth_store_factory())
    missing = str(uuid.uuid4())
    assert hub._owner(missing) is None
    # The creating transaction may still be in flight: a later notice looks again.
    assert missing not in hub._owners
    first = hub._lookup
    assert first is not None and not first.closed
    hub._owner(str(uuid.uuid4()))
    assert hub._lookup is first
    # A broken lookup connection is replaced instead of dropping list notices.
    first.close()
    assert hub._owner(missing) is None and hub._lookup is not None and not hub._lookup.closed
    hub._lookup.close()


def test_live_steps_reach_only_the_tasks_viewers_and_never_ask_for_a_read(monkeypatch):
    monkeypatch.setattr(task_events, "COALESCE_SECONDS", 0.01)

    async def scenario():
        hub = TaskEventHub(store=None)  # type: ignore[arg-type]
        hub._ensure_listener = lambda: None  # type: ignore[method-assign]
        hub._owner = lambda task_id: "owner"  # type: ignore[method-assign]
        loop = asyncio.get_running_loop()
        viewer, owner_list = Subscriber(loop), Subscriber(loop)
        hub.subscribe_task("t1", viewer)
        hub.subscribe_user("tenant", "owner", owner_list)
        step = {"kind": "note", "text": "第 3 条维持 NG"}
        hub._dispatch(json.dumps({"tenant": "tenant", "task": "t1", "kind": "activity", "activity": step}))
        await asyncio.sleep(0)
        assert owner_list.queue.empty()
        request = FakeRequest()
        frames = _stream(request, viewer, lambda: True, lambda: None)
        assert "event: ready" in await frames.__anext__()
        assert await frames.__anext__() == 'event: activity\ndata: {"kind": "note", "text": "第 3 条维持 NG"}\n\n'
        # A change and the steps around it: one change frame, then the steps in order.
        viewer.queue.put_nowait({"kind": "progress", "task": "t1"})
        viewer.queue.put_nowait({"kind": "activity", "task": "t1", "activity": {"kind": "read", "text": "cp.xlsx"}})
        assert (await frames.__anext__()).startswith("event: change")
        assert (await frames.__anext__()).startswith('event: activity\ndata: {"kind": "read"')
        request.closed = True
        await frames.aclose()

    asyncio.run(scenario())


def test_a_step_for_an_unknown_round_runs_and_sends_nothing(auth_store_factory):
    from audit.audits import ClaimedJob

    store = auth_store_factory()

    async def scenario():
        hub = TaskEventHub(store)
        subscriber = Subscriber(asyncio.get_running_loop())
        hub.subscribe_task(str(uuid.uuid4()), subscriber)
        assert await asyncio.wait_for(subscriber.queue.get(), timeout=10) == {"kind": "resync"}
        claimed = ClaimedJob(id=str(uuid.uuid4()), kind="audit", payload={}, attempt=1, round_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()))
        await asyncio.to_thread(store.broadcast_activity, claimed, {"kind": "read", "text": "cp.xlsx"})
        await asyncio.sleep(0.5)
        assert subscriber.queue.empty()

    asyncio.run(scenario())
