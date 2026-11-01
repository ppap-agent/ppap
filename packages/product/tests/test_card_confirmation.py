"""The callback connection has its own bound; failure is not a queue receipt.

Durability, authorization and claim fencing are exercised against PostgreSQL
by tests/schema/test_audit_bindings.py, not by a mocked database here.
"""

from unittest.mock import patch

import psycopg
import pytest
from product.feishu import CardAction
from product.settings import Settings
from product.store import AuthStore


def test_confirmation_enqueue_overrides_only_its_connection_deadline_and_propagates_failure():
    settings = Settings(current_tenant_allowlist="test-tenant")
    action = CardAction("test-tenant", "test-subject", "00000000-0000-4000-8000-000000000001", "om_test")
    store = AuthStore(settings, objects=object())
    with patch("product.store.store.psycopg.connect", side_effect=psycopg.OperationalError("connection not established")) as connect:
        with pytest.raises(psycopg.OperationalError):
            store.enqueue_task_confirmation(action)
    assert connect.call_args.args == (settings.dsn("api"),)
    assert connect.call_args.kwargs == {"autocommit": True, "connect_timeout": 1, "options": "-c statement_timeout=500 -c lock_timeout=200"}
    assert "connect_timeout=15" in settings.dsn("api")


def test_invalid_confirmation_identity_never_opens_a_database_connection():
    store = AuthStore(Settings(), objects=object())
    with patch("product.store.store.psycopg.connect", side_effect=AssertionError("invalid identity must not connect")) as connect:
        with pytest.raises(KeyError):
            store.enqueue_task_confirmation(CardAction("test-tenant", "test-subject", "not-a-notification", "om_test"))
    connect.assert_not_called()
