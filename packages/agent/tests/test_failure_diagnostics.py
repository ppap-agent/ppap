import json

import pytest
from agent.execution.diagnostics import failure_facts


@pytest.mark.parametrize("payload", [None, [], "secret", 42])
def test_non_object_diagnostic_is_not_persisted(payload):
    assert failure_facts(payload) == {}


def test_failure_facts_rejects_free_text_and_invalid_metadata():
    payload = {
        "usage": {"input_tokens": True, "output_tokens": -1, "uncertain": "secret", "in_flight": False},
        "stages": [
            {
                "stage": "secret",
                "stderr": "Bearer secret",
                "terminal": {
                    "num_turns": True,
                    "is_error": "secret",
                    "stop_reason": {"secret": True},
                    "usage": {"input_tokens": "secret"},
                },
            }
        ]
        * 40,
        "transport_failure": {
            "phase": ["secret"],
            "error_type": "secret",
            "last_event": "secret",
            "client_request_id": "secret",
            "elapsed_ms": -1,
            "response_bytes": True,
            "provider_status": "secret",
            "client_write_failed": "secret",
        },
        "guest": {"exit_code": True, "stderr_tail": "secret"},
    }
    saved = failure_facts(payload)
    assert "secret" not in json.dumps(saved)
    assert len(saved["stages"]) == 32
    assert saved["usage"] == {"in_flight": False}
    assert saved["transport_failure"] == {}
    assert "guest" not in saved


def test_failure_facts_keeps_confirmed_usage_transport_and_terminal_facts():
    saved = failure_facts(
        {
            "usage": {"completed_requests": 2, "input_tokens": 100, "output_tokens": 20, "uncertain": False, "in_flight": False},
            "transport_failure": {
                "phase": "provider_usage",
                "error_type": "ValueError",
                "last_event": "response.completed",
                "provider_status": 200,
                "chunk_count": 3,
                "elapsed_ms": 50,
                "client_write_failed": True,
                "client_request_id": "6ba7b810-9dad-41d1-80b4-00c04fd430c8",
            },
        }
    )
    assert saved["usage"]["completed_requests"] == 2
    assert saved["usage"]["input_tokens"] == 100
    assert saved["transport_failure"]["client_write_failed"] is True
    assert saved["transport_failure"]["client_request_id"] == "6ba7b810-9dad-41d1-80b4-00c04fd430c8"
