"""Usage events are shared within the enterprise, keep their initiator, and stay inside the tenant."""

from __future__ import annotations

from product.settings import Settings


def test_usage_lists_tenant_events_and_keeps_initiator(auth_store_factory, seed_usage_event_factory):
    store = auth_store_factory(Settings())
    store.seed_isolated_dev()
    alice, bob, carol = (store.principal_for_fixture(subject) for subject in ("alice", "bob", "carol"))
    seed_usage_event_factory(store, alice, "item-a", 400, True, "none", trace_id="alice-1")
    seed_usage_event_factory(store, alice, "item-a", 50, False, "pending", trace_id="alice-2")
    seed_usage_event_factory(store, bob, "item-a", 10, True, "none", trace_id="bob-1")

    events = store.list_usage_events(alice)
    assert {row["trace_id"] for row in events} == {"alice-1", "alice-2", "bob-1"}
    assert all("created_at" in row and row["created_at"] for row in events)
    assert all(row["ai_item_id"] == "item-a" for row in events)
    assert {row["user_id"] for row in events} == {alice.user_id, bob.user_id}
    assert {row["trace_id"] for row in store.list_usage_events(bob)} == {"alice-1", "alice-2", "bob-1"}
    assert store.list_usage_events(carol) == []
