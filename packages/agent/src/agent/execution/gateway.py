"""Execution-scoped Responses gateway; provider credentials never enter a guest.

It admits a short-lived grant, selects the frozen model for each request, forwards it
and stops it on revoke. Provider token counts are observed when reported; they are
neither a budget nor a condition for a valid result. One coarse request limit per
attempt stops a runaway loop.

Where the host binds a call log, every model call of the execution is recorded (#360):
a direct call (text, vision) hands its own record to ``/v1/ppap/model-calls`` after it
ends; any other request (the deep review agent) is recorded here as sent and answered.
Records never hold a key, a request header or the grant.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import secrets
import socket
import socketserver
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from audit.checker_core import MODEL_IDENTITY_KEYS
from documents.model_access import ModelAccess, ModelPurpose, ModelUnavailable, streams_only
from documents.model_calls import CALL_HEADER, FORMAT, image_sha256

from agent.execution.budget import REVIEW_CONCURRENCY, TASK_SECONDS
from agent.execution.secure_files import reject_constant, unique_object

MAX_BODY = 8 * 1024 * 1024
MAX_RESPONSE = 64 * 1024 * 1024
# A direct call's record carries its images, base64-encoded.
MAX_RECORD = 24 * 1024 * 1024
RECORD_PATH = "/v1/ppap/model-calls"
PURPOSE_ORDER: tuple[ModelPurpose, ...] = ("coding", "text", "vision")
_CALL_ID = re.compile(r"^[0-9a-f]{32}$")
_DATA_IMAGE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", re.DOTALL)


class CallLog(Protocol):
    """The host's store of one execution's model call records (product.model_calls.RoundCallLog)."""

    def seen(self, call_id: str) -> None:
        """A direct call reached the provider path; its own record should follow."""

    def begin(self) -> None:
        """A request the gateway records itself is under way."""

    def write(self, record: dict[str, Any], images: Mapping[str, bytes], *, exchange: bool = False) -> None:
        """Store one record; raises when it is not stored. ``exchange`` ends a request begun here."""


@dataclass
class Lease:
    execution_id: UUID
    expires: float
    models: dict[ModelPurpose, str]
    identity: dict[str, str]
    completed_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_known: bool = True
    # Requests sent and not yet settled; files reviewed side by side share the execution's grant.
    in_flight: int = 0
    provider_key: Callable[[], str] | None = field(default=None, repr=False)
    access_resolver: Callable[[ModelPurpose], ModelAccess] | None = field(default=None, repr=False)
    transport_failure: dict[str, int | str | bool | None] | None = None
    # Each open provider connection with its socket, so a revoke can interrupt every one.
    upstreams: dict[int, tuple[http.client.HTTPConnection, socket.socket | None]] = field(default_factory=dict, repr=False)
    log: CallLog | None = field(default=None, repr=False)
    # Who answered each model name, as last resolved (one name has one endpoint): what a record names, never the key.
    answered_by: dict[str, dict[str, str]] = field(default_factory=dict)


class Grants:
    def __init__(
        self,
        model: str,
        *,
        ttl: int = TASK_SECONDS,
        per_request_output_tokens: int = 32_768,
        capacity: int = 128,
        provider_key_factory: Callable[[], Callable[[], str]] | None = None,
        require_binding: bool = False,
    ) -> None:
        if not model or any(type(value) is not int or value < 1 for value in (ttl, per_request_output_tokens, capacity)):
            raise ValueError("Invalid gateway limits")
        self.model = model
        self.require_binding = require_binding
        self.bindings: dict[UUID, tuple[dict[str, str], Callable[[ModelPurpose], ModelAccess], CallLog | None]] = {}
        self.provider_key_factory = provider_key_factory
        self.ttl = ttl
        self.per_request_output_tokens = per_request_output_tokens
        self.capacity = capacity
        self.leases: dict[str, Lease] = {}
        self.lock = threading.Lock()

    @contextmanager
    def bind(self, execution_id: UUID, identity: dict[str, str], resolver: Callable[[ModelPurpose], ModelAccess], log: CallLog | None = None):
        """Only trusted host code registers an accepted execution, never guest RPC. ``log`` keeps its model call records."""
        frozen = dict(identity)
        if set(frozen) != MODEL_IDENTITY_KEYS or not all(frozen[key] for key in ("model", "vision_model", "coding_model")):
            raise ModelUnavailable()
        with self.lock:
            if execution_id in self.bindings:
                raise ValueError("Execution grant already bound")
            self.bindings[execution_id] = frozen, resolver, log
        try:
            yield
        finally:
            with self.lock:
                self.bindings.pop(execution_id, None)

    def identity(self, token: str) -> dict[str, str]:
        """The non-secret grant fields the guest needs to write its own requests: each use's model and effort, never a key."""
        with self.lock:
            lease = self.leases.get(hashlib.sha256(token.encode()).hexdigest())
            if lease is None:
                raise ValueError("Invalid execution grant")
            granted = {"model": lease.models["coding"], "text_model": lease.models["text"], "vision_model": lease.models["vision"]}
            granted.update({key: lease.identity[key] for key in ("provider", "revision", "deployment") if key in lease.identity})
            resolver = lease.access_resolver
        if resolver is not None:
            for purpose in ("text", "vision"):
                try:
                    effort = resolver(purpose).reasoning_effort
                except ModelUnavailable:
                    effort = ""
                if effort:
                    granted[f"{purpose}_effort"] = effort
        return granted

    def issue(self, execution_id: UUID) -> str:
        with self.lock:
            binding = self.bindings.get(execution_id)
            if self.require_binding and binding is None:
                raise ValueError("Execution grant is not bound")
            now = time.monotonic()
            self.leases = {key: lease for key, lease in self.leases.items() if lease.expires > now and lease.execution_id != execution_id}
            if len(self.leases) >= self.capacity:
                raise ValueError("Gateway capacity exceeded")
            if binding:
                identity = dict(binding[0])
                models: dict[ModelPurpose, str] = {"text": identity["model"], "vision": identity["vision_model"], "coding": identity["coding_model"]}
            else:
                identity, models = {}, dict.fromkeys(PURPOSE_ORDER, self.model)
            token = secrets.token_urlsafe(48)
            self.leases[hashlib.sha256(token.encode()).hexdigest()] = Lease(
                execution_id,
                now + self.ttl,
                models,
                identity,
                provider_key=self.provider_key_factory() if self.provider_key_factory is not None else None,
                access_resolver=binding[1] if binding else None,
                log=binding[2] if binding else None,
            )
            return token

    def current(self, token: str) -> Lease:
        """The live lease a direct call's record arrives under; it is not an inference request."""
        with self.lock:
            lease = self.leases.get(hashlib.sha256(token.encode()).hexdigest())
            if lease is None or lease.expires <= time.monotonic():
                raise ValueError("Invalid execution grant")
            return lease

    def authorize(self, token: str, body: dict[str, Any], request_bytes: int) -> tuple[Lease, ModelPurpose]:
        with self.lock:
            lease = self.leases.get(hashlib.sha256(token.encode()).hexdigest())
            if lease is None or lease.expires <= time.monotonic():
                raise ValueError("Invalid execution grant")
            # Only a model frozen for this operation is sent. One model name has one endpoint and
            # key, so any use that froze the name finds them; the request itself is not changed.
            purpose = next((name for name in PURPOSE_ORDER if body.get("model") == lease.models[name]), None)
            if purpose is None:
                raise ValueError("Model outside the grant")
            if body.get("previous_response_id") or body.get("conversation") or body.get("background"):
                raise ValueError("Stateful provider requests are disabled")
            tools = body.get("tools", [])
            # The agent decides the tool set. Do not drop shell, web, or any other tool type here.
            if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
                raise ValueError("Invalid model tool")
            requested = body.get("max_output_tokens", self.per_request_output_tokens)
            if type(requested) is not int or requested < 1:
                raise ValueError("Invalid output token limit")
            if not 0 < request_bytes <= MAX_BODY:
                raise ValueError("Request byte limit")
            body["store"] = False
            body["max_output_tokens"] = min(requested, self.per_request_output_tokens)
            lease.in_flight += 1
            return lease, purpose

    def record_usage(self, lease: Lease, usage: Any) -> None:
        """Count a finished request. Token counts are observed only when reported."""
        counts = [usage.get(name) for name in ("input_tokens", "output_tokens")] if isinstance(usage, dict) else [None, None]
        with self.lock:
            lease.in_flight -= 1
            lease.completed_requests += 1
            if all(type(value) is int and value >= 0 for value in counts):
                lease.input_tokens += cast(int, counts[0])
                lease.output_tokens += cast(int, counts[1])
            else:
                lease.usage_known = False

    def finish_failed(self, lease: Lease, failure: dict[str, int | str | bool | None]) -> None:
        """A request that may have reached the provider. The lease stays usable so the
        agent can retry; the counts are no longer complete."""
        with self.lock:
            lease.in_flight -= 1
            lease.usage_known = False
            lease.transport_failure = failure

    def finish_not_sent(self, lease: Lease) -> None:
        """A local credential rejection never reached the provider."""
        with self.lock:
            lease.in_flight -= 1
            lease.transport_failure = {
                "phase": "provider_request",
                "error_type": "ModelUnavailable",
                "provider_status": 0,
                "response_bytes": 0,
                "last_event": "none",
            }

    @staticmethod
    def _usage(leases: list[Lease]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "completed_requests": sum(lease.completed_requests for lease in leases),
            "input_tokens": sum(lease.input_tokens for lease in leases),
            "output_tokens": sum(lease.output_tokens for lease in leases),
            "usage_known": all(lease.usage_known and not lease.in_flight for lease in leases),
            "in_flight": any(lease.in_flight > 0 for lease in leases),
        }
        for lease in leases:
            if lease.transport_failure:
                result["transport_failure"] = dict(lease.transport_failure)
        return result

    def renew(self, execution_id: UUID) -> None:
        """The host saw the execution start its next task: its live grant gets one more task's time.

        A revoked or expired grant is never renewed.
        """
        with self.lock:
            now = time.monotonic()
            live = [lease for lease in self.leases.values() if lease.execution_id == execution_id and lease.expires > now]
            if not live:
                raise ValueError("Invalid execution grant")
            for lease in live:
                lease.expires = now + self.ttl

    def usage(self, execution_id: UUID) -> dict[str, Any]:
        with self.lock:
            return self._usage([lease for lease in self.leases.values() if lease.execution_id == execution_id])

    def revoke(self, execution_id: UUID, *, require_existing: bool = False) -> dict[str, Any]:
        with self.lock:
            revoked = [lease for lease in self.leases.values() if lease.execution_id == execution_id]
            if require_existing and not revoked:
                raise ValueError("Final usage receipt is unavailable")
            usage = self._usage(revoked)
            self.leases = {key: lease for key, lease in self.leases.items() if lease.execution_id != execution_id}
            connections = [connection for lease in revoked for connection in lease.upstreams.values()]
        # A provider need not send another byte after cancellation. Interrupt the
        # local socket outside the grants lock so the handler can release its slot.
        for upstream, transport in connections:
            transport = transport or getattr(upstream, "sock", None)
            if transport is not None:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            upstream.close()
        return usage

    def revoke_all(self) -> None:
        """Close every prior execution before rebinding a shared host runtime."""
        with self.lock:
            executions = {lease.execution_id for lease in self.leases.values()}
        for execution_id in executions:
            self.revoke(execution_id)

    def bind_upstream(self, lease: Lease, upstream: http.client.HTTPConnection) -> None:
        with self.lock:
            if lease.expires <= time.monotonic() or not any(value is lease for value in self.leases.values()):
                raise ValueError("Invalid execution grant")
            # getresponse() can detach a Connection: close socket from the
            # HTTPConnection while its response is still blocking in read1().
            lease.upstreams[id(upstream)] = (upstream, getattr(upstream, "sock", None))

    def release_upstream(self, lease: Lease, upstream: http.client.HTTPConnection | None) -> None:
        with self.lock:
            lease.upstreams.pop(id(upstream), None)

    def active(self, lease: Lease) -> bool:
        with self.lock:
            return lease.expires > time.monotonic() and any(value is lease for value in self.leases.values())


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], grants: Grants, upstream: str, provider_key: str | Callable[[], str]) -> None:
        url = urlsplit(upstream)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Invalid provider endpoint")
        self.grants, self.upstream, self.provider_key = grants, url, provider_key
        self.slots = threading.BoundedSemaphore(16)
        # The runtime executes one audit at a time, whose files are reviewed side by side.
        # Requests beyond that (an agent's auxiliary request beside its turn) queue here.
        self.inference_slots = threading.BoundedSemaphore(REVIEW_CONCURRENCY)
        super().__init__(address, Handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


class Handler(BaseHTTPRequestHandler):
    server: Gateway
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        if self.path == RECORD_PATH:
            self._record_call()
        else:
            self._forward_request()

    def _record_call(self) -> None:
        """Store a direct call's own record under the execution its grant belongs to."""
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise ValueError("Unsupported request")
            length = int(self.headers["Content-Length"])
            if not 0 < length <= MAX_RECORD:
                raise ValueError("Record limit")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request")
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise ValueError("Missing grant")
            token = authorization[7:]
        except (ValueError, TypeError, OSError):
            self.send_error(400, "Invalid model call record")
            return
        try:
            lease = self.server.grants.current(token)
        except ValueError:
            self.send_error(403, "Execution request rejected")
            return
        log = lease.log
        if log is None:
            # Nothing keeps records for this execution.
            self._no_content()
            return
        try:
            payload = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
            if not isinstance(payload, dict) or set(payload) != {"record", "images"}:
                raise ValueError("Invalid model call record")
            record, encoded = payload["record"], payload["images"]
            if not isinstance(record, dict) or not isinstance(encoded, dict) or not _CALL_ID.fullmatch(str(record.get("call_id"))) or record.get("purpose") not in {"text", "vision"}:
                raise ValueError("Invalid model call record")
            images: dict[str, bytes] = {}
            for digest, value in encoded.items():
                image = base64.b64decode(value, validate=True)
                if image_sha256(image) != digest:
                    raise ValueError("Image digest mismatch")
                images[digest] = image
            # The guest knows only the local gateway: name the model's own host, owner and revision.
            identity = record["identity"] if isinstance(record.get("identity"), dict) else {}
            answered = {key: value for key, value in lease.answered_by.get(str(record.get("model")), {}).items() if key != "reasoning_effort"}
            record["identity"] = {**identity, **answered}
            record = _redacted(record, (token,))
        except (ValueError, TypeError, KeyError):
            self.send_error(400, "Invalid model call record")
            return
        try:
            log.write(record, images)
        except Exception:  # noqa: BLE001 - the log counts its own failures; the call keeps its result
            self.send_error(503, "Model call record not stored")
            return
        self._no_content()

    def _no_content(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _write_exchange(self, lease: Lease, purpose: ModelPurpose, body: Any, response: Any, usage: Any, code: str, status: int, seconds: float, hidden: tuple[str, ...]) -> None:
        """Record one request as sent and answered. A record that cannot be built is still counted."""
        log = lease.log
        if log is None:
            return
        images: dict[str, bytes] = {}
        try:
            record = _redacted(
                {
                    "format": FORMAT,
                    "kind": "responses",
                    "purpose": purpose,
                    "model": body.get("model") if isinstance(body, dict) else None,
                    "identity": lease.answered_by.get(lease.models.get(purpose, "")) or {"model": lease.models.get(purpose, "")},
                    "request": _without_images(body, images),
                    "response": response,
                    "usage": usage if isinstance(usage, dict) else None,
                    "duration_ms": max(0, round(seconds * 1000)),
                    "code": code,
                    "provider_status": status,
                },
                hidden,
            )
        except (ValueError, TypeError):
            images, record = {}, {"format": FORMAT, "kind": "responses", "purpose": purpose, "code": code, "record": "unavailable"}
        try:
            log.write(record, images, exchange=True)
        except Exception:  # noqa: BLE001 - the log counts its own failures
            pass

    def _forward_request(self) -> None:
        started_at = time.monotonic()
        headers_at: float | None = None
        first_chunk_at: float | None = None
        last_chunk_at: float | None = None
        max_chunk_gap: float | None = None
        chunk_count = 0
        client_write_failed = False
        client_request_id: str | None = None
        upstream: http.client.HTTPSConnection | None = None
        lease: Lease | None = None
        settled = False
        started = False
        acquired = False
        phase, error_type, last_event = "request", "IncompleteResponse", "none"
        total, provider_status = 0, 0
        # A request the gateway records itself (#360): what was sent, and what came back.
        exchange: tuple[ModelPurpose, dict[str, Any]] | None = None
        answered: Any = None
        provider_usage: Any = None
        provider_key = ""
        grant_token = ""
        succeeded = False
        try:
            if self.path != "/v1/responses" or self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise ValueError("Unsupported request")
            length = int(self.headers["Content-Length"])
            if not 0 < length <= MAX_BODY:
                raise ValueError("Request limit")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request")
            body = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
            if not isinstance(body, dict):
                raise ValueError("Invalid model request")
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise ValueError("Missing grant")
            # Drain the bounded request before queueing inference. Otherwise
            # a large next turn blocks in the client's socket send while an
            # auxiliary provider request is still finishing.
            acquired = self.server.inference_slots.acquire(timeout=300)
            if not acquired:
                self.send_error(408, "Model request queue timeout")
                return
            grant_token = authorization[7:]
            lease, purpose = self.server.grants.authorize(grant_token, body, len(raw))
            if lease.log is not None:
                call_id = self.headers.get(CALL_HEADER, "")
                if _CALL_ID.fullmatch(call_id):
                    lease.log.seen(call_id)
                else:
                    lease.log.begin()
                    exchange = purpose, body
            if lease.access_resolver is not None:
                # The host callback returns a key only while the tenant's selection
                # still equals the identity frozen when the operation was accepted.
                access = lease.access_resolver(purpose)
                url = urlsplit(access.base_url)
                if access.model != lease.models[purpose] or url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ModelUnavailable()
                provider_key = access.api_key.get_secret_value()
                lease.answered_by[access.model] = {
                    "host": url.hostname or "",
                    "model": access.model,
                    "reasoning_effort": access.reasoning_effort,
                    "revision": access.revision,
                    "source": access.source,
                }
            else:
                url = self.server.upstream
                selected_key = lease.provider_key or self.server.provider_key
                provider_key = selected_key() if callable(selected_key) else selected_key
                lease.answered_by[lease.models[purpose]] = {"host": url.hostname or "", "model": lease.models[purpose]}
            if not provider_key or not self.server.grants.active(lease):
                raise ModelUnavailable()
            # A guest cannot know that its model answers only as a stream. Stream upstream and
            # return the completed response object, as a non-streaming request expects.
            collapse = streams_only(url.geturl()) and not body.get("stream")
            if collapse:
                body["stream"] = True
            completed_response: Any = None
            phase = "provider_request"
            # The 3.12 typeshed types this port as str. Runtime still needs an int.
            upstream = http.client.HTTPSConnection(str(url.hostname), port=cast(Any, url.port or 443), timeout=min(300, max(1, lease.expires - time.monotonic())))
            self.server.grants.bind_upstream(lease, upstream)
            # This local identifier correlates one authorized send attempt;
            # it is not evidence that the provider received it.
            client_request_id = str(uuid4())
            upstream.request(
                "POST",
                url.path.rstrip("/") + "/responses",
                json.dumps(body),
                {"Authorization": "Bearer " + provider_key, "Content-Type": "application/json", "X-Client-Request-Id": client_request_id},
            )
            self.server.grants.bind_upstream(lease, upstream)
            phase = "provider_headers"
            response = upstream.getresponse()
            headers_at = time.monotonic()
            provider_status = response.status
            if response.status != 200:
                error_type = "ProviderStatus"
                self.send_error(502, "Provider request failed")
                return
            phase = "client_headers"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if body.get("stream") and not collapse else "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            started = True
            client_connected = True
            pending = bytearray()
            phase = "provider_read"
            while chunk := response.read1(65536):
                chunk_at = time.monotonic()
                if first_chunk_at is None:
                    first_chunk_at = chunk_at
                if last_chunk_at is not None:
                    max_chunk_gap = max(max_chunk_gap or 0, chunk_at - last_chunk_at)
                last_chunk_at = chunk_at
                chunk_count += 1
                total += len(chunk)
                phase = "response_limits"
                if total > MAX_RESPONSE or not self.server.grants.active(lease):
                    raise ValueError("Response limit or grant expired")
                pending.extend(chunk)
                if len(pending) > 16 * 1024 * 1024:
                    raise ValueError("Provider event limit")
                if body.get("stream"):
                    # Events are observed for usage and diagnostics only; the client
                    # receives the provider bytes unchanged and judges them itself.
                    while b"\n" in pending:
                        line, _, remainder = pending.partition(b"\n")
                        pending = bytearray(remainder)
                        if not line.startswith(b"data: ") or line[6:].strip() == b"[DONE]":
                            continue
                        try:
                            event = json.loads(line[6:])
                        except ValueError:
                            continue
                        kind = event.get("type") if isinstance(event, dict) else None
                        last_event = kind if kind in {"response.created", "response.in_progress", "response.completed", "response.failed", "response.incomplete", "error"} else "other"
                        if kind in {"response.completed", "response.incomplete", "response.failed"}:
                            completed_response = event.get("response")
                            provider_usage = (completed_response or {}).get("usage")
                if client_connected and not collapse:
                    try:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    except OSError:
                        # The agent may exit after its terminal result while a final
                        # request is still streaming. Finish reading within the grant;
                        # an explicit cancellation revokes the grant and stops it.
                        client_connected = False
                        client_write_failed = True
                phase = "provider_read"
            if not body.get("stream"):
                try:
                    answered = json.loads(pending)
                    provider_usage = answered.get("usage")
                except (ValueError, AttributeError):
                    provider_usage = None
            else:
                answered = completed_response
            if collapse:
                if not isinstance(completed_response, dict):
                    raise ValueError("Incomplete provider stream")
                answer = json.dumps(completed_response).encode()
                if client_connected:
                    self.wfile.write(f"{len(answer):x}\r\n".encode() + answer + b"\r\n")
            self.server.grants.record_usage(lease, provider_usage)
            settled = succeeded = True
            if client_connected:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except ModelUnavailable:
            error_type = "ModelUnavailable"
            if lease is not None:
                # The resolver runs before upstream.request; nothing was sent.
                self.server.grants.finish_not_sent(lease)
                settled = True
            self.send_error(503, "Model unavailable")
        except (ValueError, TypeError, AttributeError, OSError, http.client.HTTPException) as error:
            # Retain fixed metadata, never exception text, response content,
            # request headers, or credentials. The client sees only a failure.
            error_type = type(error).__name__
            if not started:
                self.send_error(403, "Execution request rejected")
        finally:
            if lease is not None and not settled:
                finished_at = time.monotonic()
                self.server.grants.finish_failed(
                    lease,
                    {
                        "phase": phase,
                        "error_type": error_type,
                        "response_bytes": total,
                        "last_event": last_event,
                        "provider_status": provider_status,
                        **({"client_request_id": client_request_id} if client_request_id is not None else {}),
                        # Offsets use handler entry, including local queue time.
                        # Gaps observe read1 returns, not provider token timing.
                        "elapsed_ms": int((finished_at - started_at) * 1000),
                        "headers_ms": None if headers_at is None else int((headers_at - started_at) * 1000),
                        "first_chunk_ms": None if first_chunk_at is None else int((first_chunk_at - started_at) * 1000),
                        "last_chunk_ms": None if last_chunk_at is None else int((last_chunk_at - started_at) * 1000),
                        "last_chunk_gap_ms": None if last_chunk_at is None else int((finished_at - last_chunk_at) * 1000),
                        "max_chunk_gap_ms": None if max_chunk_gap is None else int(max_chunk_gap * 1000),
                        "chunk_count": chunk_count,
                        "client_write_failed": client_write_failed,
                    },
                )
            self.close_connection = True
            try:
                if upstream:
                    upstream.close()
            finally:
                if lease is not None:
                    self.server.grants.release_upstream(lease, upstream)
                if acquired:
                    self.server.inference_slots.release()
                # Written after the next request may start: a record never holds up the agent.
                if exchange is not None and lease is not None:
                    self._write_exchange(
                        lease,
                        exchange[0],
                        exchange[1],
                        answered,
                        provider_usage,
                        "ok" if succeeded else error_type,
                        provider_status,
                        time.monotonic() - started_at,
                        (grant_token, provider_key),
                    )


def _without_images(value: Any, images: dict[str, bytes]) -> Any:
    """The request with each inline image replaced by its content digest; the bytes are kept apart, once."""
    if isinstance(value, dict):
        return {key: _without_images(item, images) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_images(item, images) for item in value]
    if isinstance(value, str) and (match := _DATA_IMAGE.match(value)):
        try:
            image = base64.b64decode(match.group(2), validate=True)
        except ValueError:
            return value
        digest = image_sha256(image)
        images[digest] = image
        return f"<image sha256:{digest}>"
    return value


def _redacted(record: dict[str, Any], hidden: tuple[str, ...]) -> dict[str, Any]:
    """A record never carries the grant or a provider key, wherever a guest or a provider put it."""
    text = json.dumps(record, ensure_ascii=False)
    for secret in hidden:
        if secret:
            text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[redacted]")
    return cast(dict[str, Any], json.loads(text))


class GrantServer(socketserver.UnixStreamServer):
    def __init__(self, path: str, grants: Grants, base_url: str) -> None:
        self.grants, self.base_url = grants, base_url
        super().__init__(path, GrantHandler)
        Path(path).chmod(0o600)


class LocalGateway:
    """Loopback-only gateway owned by the trusted Controller process."""

    def __init__(
        self,
        socket_path: Path,
        *,
        upstream: str,
        model: str,
        provider_key: str | Callable[[], str],
        provider_key_factory: Callable[[], Callable[[], str]] | None = None,
        require_binding: bool = False,
    ) -> None:
        if not socket_path.is_absolute() or not provider_key:
            raise ValueError("Invalid local gateway configuration")
        socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        grants = Grants(model, provider_key_factory=provider_key_factory, require_binding=require_binding)
        self.http = Gateway(("127.0.0.1", 0), grants, upstream, provider_key)
        try:
            self.control = GrantServer(str(socket_path), grants, f"http://127.0.0.1:{self.http.server_address[1]}/v1")
        except BaseException:
            self.http.server_close()
            raise
        self.socket_path = socket_path
        self.base_url = self.control.base_url
        self.threads: list[threading.Thread] = []

    def __enter__(self) -> LocalGateway:
        self.threads = [
            threading.Thread(target=self.http.serve_forever, daemon=True),
            threading.Thread(target=self.control.serve_forever, daemon=True),
        ]
        for thread in self.threads:
            thread.start()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.http.shutdown()
        self.control.shutdown()
        for thread in self.threads:
            thread.join()
        self.http.server_close()
        self.control.server_close()
        self.socket_path.unlink(missing_ok=True)


def local_gateway(
    socket_path: Path,
    *,
    upstream: str,
    model: str,
    provider_key: str | Callable[[], str],
    provider_key_factory: Callable[[], Callable[[], str]] | None = None,
    require_binding: bool = False,
) -> LocalGateway:
    """Return a context manager for an execution-scoped grant gateway."""
    return LocalGateway(
        socket_path,
        upstream=upstream,
        model=model,
        provider_key=provider_key,
        provider_key_factory=provider_key_factory,
        require_binding=require_binding,
    )


class GrantHandler(socketserver.StreamRequestHandler):
    server: GrantServer

    def handle(self) -> None:
        self.connection.settimeout(5)
        try:
            line = self.rfile.readline(4097)
            if len(line) > 4096 or not line.endswith(b"\n"):
                raise ValueError("Invalid frame")
            request = json.loads(line, object_pairs_hook=unique_object)
            if set(request) != {"method", "execution_id"}:
                raise ValueError("Invalid grant request")
            execution_id = UUID(request["execution_id"])
            grants = self.server.grants
            if request["method"] == "issue":
                token = grants.issue(execution_id)
                result: dict[str, Any] = {"base_url": self.server.base_url, "token": token, **grants.identity(token), "wall_seconds": grants.ttl}
            elif request["method"] == "renew":
                grants.renew(execution_id)
                result = {"ok": True}
            elif request["method"] == "usage":
                result = grants.usage(execution_id)
            elif request["method"] == "revoke":
                grants.revoke(execution_id)
                result = {"ok": True}
            elif request["method"] == "revoke_and_usage":
                result = grants.revoke(execution_id, require_existing=True)
            else:
                raise ValueError("Unsupported grant method")
        except (ValueError, TypeError, KeyError, OSError, ModelUnavailable):
            result = {"error": "Grant rejected"}
        self.wfile.write(json.dumps(result).encode() + b"\n")


def grant_request(path: str, method: str, execution_id: UUID) -> dict[str, Any]:
    request: dict[str, Any] = {"method": method, "execution_id": str(execution_id)}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(path)
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as stream:
            line = stream.readline(16385)
        if len(line) > 16384 or not line.endswith(b"\n"):
            raise ValueError("Invalid grant response")
        result: Any = json.loads(line)
        if not isinstance(result, dict) or "error" in result:
            raise ValueError("Grant unavailable")
        return result
