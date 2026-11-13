"""Explicit live corpus capability; provider credentials stay in the host gateway."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from agent.execution.gateway import LocalGateway, local_gateway
from documents.model_access import PURPOSES, ModelAccess, ModelPurpose

# CI and development model use is not budget-limited; the gateway still requires a number.
UNLIMITED = 10**12


@dataclass
class LiveModels:
    """The models a live run uses, one per use as in production; their identity is what a proof freezes.

    A single ModelAccess serves every use (the identity earlier proofs froze).
    """

    access: ModelAccess | Callable[[ModelPurpose], ModelAccess] = field(repr=False)
    max_requests: int = UNLIMITED
    _gateway: LocalGateway | None = field(default=None, init=False, repr=False)
    _execution_id: UUID = field(default_factory=uuid4, init=False, repr=False)
    _used: bool = field(default=False, init=False, repr=False)
    _usage: dict = field(default_factory=lambda: {"completed_requests": 0, "input_tokens": 0, "output_tokens": 0, "usage_known": True, "in_flight": False, "budget_exhausted": False}, init=False)

    def __post_init__(self) -> None:
        if type(self.max_requests) is not int or self.max_requests < 1:
            raise ValueError("official_live_positive_limits_required")

    def resolve(self, purpose: ModelPurpose) -> ModelAccess:
        return self.access if isinstance(self.access, ModelAccess) else self.access(purpose)

    def identity(self) -> dict[str, str]:
        text, vision, coding = (self.resolve(purpose) for purpose in PURPOSES)
        if isinstance(self.access, ModelAccess):
            return {**text.identity(), "vision_model": text.model, "coding_model": text.model}
        parts = {purpose: self.resolve(purpose).identity() for purpose in PURPOSES}
        revision = hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {**text.identity(), "revision": revision, "vision_model": vision.model, "coding_model": coding.model}

    def receipt(self) -> dict:
        usage = self._gateway.http.grants.usage(self._execution_id) if self._gateway is not None else self._usage
        return {"mode": "live", "model_identity": self.identity(), "limits": {"requests": self.max_requests}, "usage": usage, "monetary_cost": "unknown"}

    @contextmanager
    def session(self) -> Iterator[dict]:
        if self._used:
            raise ValueError("official_live_session_already_used")
        self._used = True
        # One lease for the whole Suite: no per-sample reset or retry.
        with tempfile.TemporaryDirectory(prefix="ppap-official-model-", dir="/tmp") as work:
            text = self.resolve("text")
            with local_gateway(
                Path(work) / "g.sock",
                upstream=text.base_url,
                model=text.model,
                provider_key=lambda: text.api_key.get_secret_value(),
                require_binding=True,
                requests=self.max_requests,
            ) as gateway:
                self._gateway = gateway
                grants = gateway.http.grants
                with grants.bind(self._execution_id, self.identity(), self.resolve):
                    token = grants.issue(self._execution_id)
                    try:
                        yield {"base_url": gateway.base_url, "token": token, **grants.identity(token), "wall_seconds": grants.ttl}
                    finally:
                        self._usage = grants.revoke(self._execution_id, require_existing=True)
                        self._gateway = None
