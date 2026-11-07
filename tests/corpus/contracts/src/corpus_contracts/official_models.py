"""Explicit live corpus capability; provider credentials stay in the host gateway."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from agent.execution.gateway import LocalGateway, local_gateway
from documents.model_access import ModelAccess


# CI and development model use is not budget-limited; the gateway still requires a number.
UNLIMITED = 10**12


@dataclass
class LiveModels:
    access: ModelAccess = field(repr=False)
    max_requests: int = UNLIMITED
    max_input_tokens: int = UNLIMITED
    max_output_tokens: int = UNLIMITED
    _gateway: LocalGateway | None = field(default=None, init=False, repr=False)
    _execution_id: UUID = field(default_factory=uuid4, init=False, repr=False)
    _used: bool = field(default=False, init=False, repr=False)
    _usage: dict = field(default_factory=lambda: {"completed_requests": 0, "input_tokens": 0, "output_tokens": 0, "uncertain": False, "in_flight": False, "budget_exhausted": False}, init=False)

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in (self.max_requests, self.max_input_tokens, self.max_output_tokens)):
            raise ValueError("official_live_positive_limits_required")

    def receipt(self) -> dict:
        usage = self._gateway.http.grants.usage(self._execution_id) if self._gateway is not None else self._usage
        return {
            "mode": "live",
            "model_identity": self.access.identity(),
            "limits": {"requests": self.max_requests, "input_tokens": self.max_input_tokens, "output_tokens": self.max_output_tokens},
            "usage": usage,
            "monetary_cost": "unknown",
        }

    @contextmanager
    def session(self) -> Iterator[dict[str, str]]:
        if self._used:
            raise ValueError("official_live_session_already_used")
        self._used = True
        # One lease for the whole Suite: no per-sample budget reset or retry.
        with tempfile.TemporaryDirectory(prefix="ppap-official-model-", dir="/tmp") as work:
            with local_gateway(
                Path(work) / "g.sock",
                upstream=self.access.base_url,
                model=self.access.model,
                provider_key=lambda: self.access.api_key.get_secret_value(),
                require_binding=True,
                requests=self.max_requests,
                input_tokens=self.max_input_tokens,
                output_tokens=self.max_output_tokens,
            ) as gateway:
                self._gateway = gateway
                grants = gateway.http.grants
                with grants.bind(self._execution_id, self.access.identity(), lambda: self.access):
                    token = grants.issue(self._execution_id)
                    try:
                        yield {"base_url": gateway.base_url, "token": token, **self.access.identity()}
                    finally:
                        self._usage = grants.revoke(self._execution_id, require_existing=True)
                        self._gateway = None
