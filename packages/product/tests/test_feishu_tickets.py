"""Preserve provider timestamp ordering without float or datetime rounding."""

from decimal import Decimal

from product.feishu import AppTicket, accept_event
from product.settings import Settings


def test_ticket_timestamp_preserves_submicrosecond_provider_order():
    settings = Settings(feishu_app_id="cli_tickets", feishu_verification_token="test-verification")
    for timestamp in ("1700000000.1234567", "1700000000.1234568"):
        body = {"type": "event_callback", "ts": timestamp, "token": "test-verification", "event": {"type": "app_ticket", "app_id": "cli_tickets", "app_ticket": "test-ticket"}}
        event = accept_event(settings, body)
        assert isinstance(event, AppTicket)
        assert event.issued_at == Decimal(timestamp)
