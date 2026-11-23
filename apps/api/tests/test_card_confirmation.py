"""A start button on a card sent before signing moved to the Web (#355) sends the person to the page; it starts nothing."""

from __future__ import annotations

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product.settings import Settings


def card_event(*, token="verification", app_id="cli_callback"):
    return {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "app_id": app_id, "token": token},
        "event": {
            "operator": {"tenant_key": "test-tenant", "open_id": "test-creator"},
            "token": "card-update-token",
            "action": {"tag": "button", "value": {"action": "confirm_audit_task", "notification_id": "test-notification"}},
            "context": {"open_message_id": "test-message"},
        },
    }


class UntouchedStore:
    """Any attribute read means the callback reached for the database."""

    def __getattr__(self, name):
        raise AssertionError(f"card callback touched the store: {name}")


def client():
    settings = Settings(feishu_app_id="cli_callback", feishu_app_secret="test-secret", feishu_verification_token="verification", usage_charge_enabled=False)
    return TestClient(create_app(settings, auth_store=UntouchedStore()))


def test_an_old_start_button_points_to_the_page_and_starts_nothing():
    response = client().post("/integrations/feishu/events", json=card_event())
    assert response.status_code == 200
    assert response.json()["toast"]["type"] == "info" and "在页面上核对后开始审核" in response.json()["toast"]["content"]


@pytest.mark.parametrize(("event", "status", "error"), [(card_event(token="card-update-token"), 401, "unauthenticated"), (card_event(app_id="other-app"), 403, "app_mismatch")])
def test_callback_authentication_guards_still_run(event, status, error):
    response = client().post("/integrations/feishu/events", json=event)
    assert (response.status_code, response.json()) == (status, {"error": error})
