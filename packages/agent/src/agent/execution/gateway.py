"""Execution-scoped Responses gateway; provider credentials never enter a guest."""

from __future__ import annotations

import hashlib
import http.client
import json
import secrets
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from agent.execution.budget import DEEP_TOTAL_SECONDS
from agent.execution.guest_model import ModelBudgetError
from agent.execution.secure_files import reject_constant, unique_object

MAX_BODY = 8 * 1024 * 1024
MAX_RESPONSE = 64 * 1024 * 1024


class DuplicateInference(ValueError):
    """The same request may already have incurred provider charges."""


@dataclass
class Lease:
    execution_id: UUID
    expires: float
    requests_left: int
    input_budget: int
    output_budget: int
    completed_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    in_flight: bool = False
    uncertain: bool = False
    budget_exhausted: bool = False
    request_hashes: set[str] = field(default_factory=set)
    transport_failure: dict[str, int | str | bool | None] | None = None
    upstream: http.client.HTTPConnection | None = field(default=None, repr=False)
    upstream_socket: socket.socket | None = field(default=None, repr=False)


class Grants:
    def __init__(
        self,
        model: str,
        *,
        ttl: int = DEEP_TOTAL_SECONDS,
        requests: int = 128,
        input_tokens: int = 4_000_000,
        output_tokens: int = 100_000,
        per_request_output_tokens: int = 32_768,
        capacity: int = 128,
    ) -> None:
        if not model or any(type(value) is not int or value < 1 for value in (ttl, requests, input_tokens, output_tokens, per_request_output_tokens, capacity)):
            raise ValueError("Invalid gateway limits")
        self.model = model
        self.ttl = ttl
        self.requests = requests
        self.input_budget = input_tokens
        self.output_budget = output_tokens
        self.per_request_output_tokens = per_request_output_tokens
        self.capacity = capacity
        self.leases: dict[str, Lease] = {}
        self.lock = threading.Lock()

    def issue(self, execution_id: UUID, prior_usage: dict[str, Any] | None = None) -> str:
        # Rule compilation and audit have separate leases but share one budget.
        prior = {"model_requests": 0, "input_tokens": 0, "output_tokens": 0}
        if prior_usage is not None:
            if not isinstance(prior_usage, dict):
                raise ValueError("Invalid prior usage")
            prior = dict(prior_usage)
        limits = {"model_requests": self.requests, "input_tokens": self.input_budget, "output_tokens": self.output_budget}
        if (
            any(type(prior.get(key)) is not int or prior[key] < 0 for key in limits)
            or prior.get("uncertain", False) is not False
            or prior.get("in_flight", False) is not False
            or (prior["model_requests"] == 0 and (prior["input_tokens"] or prior["output_tokens"]))
        ):
            raise ValueError("Invalid prior usage")
        if any(prior[key] > limit for key, limit in limits.items()):
            raise ModelBudgetError("Grant budget exhausted")
        with self.lock:
            now = time.monotonic()
            self.leases = {key: lease for key, lease in self.leases.items() if lease.expires > now and lease.execution_id != execution_id}
            if len(self.leases) >= self.capacity:
                raise ValueError("Gateway capacity exceeded")
            token = secrets.token_urlsafe(48)
            self.leases[hashlib.sha256(token.encode()).hexdigest()] = Lease(
                execution_id,
                now + self.ttl,
                self.requests - prior["model_requests"],
                self.input_budget - prior["input_tokens"],
                self.output_budget - prior["output_tokens"],
            )
            return token

    def authorize(self, token: str, body: dict[str, Any], request_bytes: int) -> Lease:
        with self.lock:
            lease = self.leases.get(hashlib.sha256(token.encode()).hexdigest())
            if lease is None or lease.expires <= time.monotonic() or lease.in_flight or lease.uncertain or lease.budget_exhausted or body.get("model") != self.model:
                raise ValueError("Invalid execution grant")
            if lease.requests_left <= 0 or lease.input_tokens >= lease.input_budget or lease.output_tokens >= lease.output_budget:
                lease.budget_exhausted = True
                raise ValueError("Grant budget exhausted")
            if body.get("previous_response_id") or body.get("conversation") or body.get("background"):
                raise ValueError("Stateful provider requests are disabled")
            tools = body.get("tools", [])
            # Grok decides the tool set. Do not drop shell, web, or any other tool type here.
            if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
                raise ValueError("Invalid model tool")
            remaining_output = lease.output_budget - lease.output_tokens
            requested = body.get("max_output_tokens", min(self.per_request_output_tokens, remaining_output))
            if type(requested) is not int or requested < 1:
                raise ValueError("Invalid output token limit")
            if not 0 < request_bytes <= MAX_BODY:
                raise ValueError("Request byte limit")
            body["store"] = False
            body["max_output_tokens"] = min(requested, self.per_request_output_tokens, remaining_output)
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            if digest in lease.request_hashes:
                raise DuplicateInference("Inference already submitted")
            lease.request_hashes.add(digest)
            lease.requests_left -= 1
            lease.in_flight = True
            return lease

    def record_usage(self, lease: Lease, usage: dict[str, Any]) -> None:
        counts = [usage.get(name) for name in ("input_tokens", "output_tokens")]
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("Unknown provider usage")
        with self.lock:
            if not lease.in_flight:
                raise ValueError("No pending inference")
            lease.in_flight = False
            lease.completed_requests += 1
            lease.input_tokens += cast(int, counts[0])
            lease.output_tokens += cast(int, counts[1])
            if lease.input_tokens > lease.input_budget or lease.output_tokens > lease.output_budget:
                # Usage is confirmed, not uncertain. The one in-flight request
                # may cross the input threshold because encrypted reasoning
                # cannot be counted accurately from request bytes.
                lease.budget_exhausted = True

    def finish_unknown(self, lease: Lease, failure: dict[str, int | str | bool | None] | None = None) -> None:
        with self.lock:
            lease.in_flight = False
            lease.uncertain = True
            lease.transport_failure = failure
            # Unknown usage closes the lease. Bytes and requested output are
            # not actual token counts and must not be reported as such.

    @staticmethod
    def _usage(leases: list[Lease]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "completed_requests": sum(lease.completed_requests for lease in leases),
            "input_tokens": sum(lease.input_tokens for lease in leases),
            "output_tokens": sum(lease.output_tokens for lease in leases),
            "uncertain": any(lease.uncertain or lease.in_flight for lease in leases),
            "in_flight": any(lease.in_flight for lease in leases),
            "budget_exhausted": any(lease.budget_exhausted for lease in leases),
        }
        for lease in leases:
            if lease.transport_failure:
                result["transport_failure"] = dict(lease.transport_failure)
        return result

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
            connections = [(lease.upstream, lease.upstream_socket) for lease in revoked]
        # A provider need not send another byte after cancellation. Interrupt
        # the local socket outside the grants lock so the handler can settle
        # unknown usage and release its inference slot. This is not evidence
        # that the provider stopped work or did not charge for the request.
        for upstream, transport in connections:
            if upstream is None:
                continue
            transport = transport or getattr(upstream, "sock", None)
            if transport is not None:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            upstream.close()
        return usage

    def bind_upstream(self, lease: Lease, upstream: http.client.HTTPConnection) -> None:
        with self.lock:
            if lease.expires <= time.monotonic() or not any(value is lease for value in self.leases.values()):
                raise ValueError("Invalid execution grant")
            lease.upstream = upstream
            # getresponse() can detach a Connection: close socket from the
            # HTTPConnection while its response is still blocking in read1().
            lease.upstream_socket = getattr(upstream, "sock", None)

    def release_upstream(self, lease: Lease) -> None:
        with self.lock:
            lease.upstream = None
            lease.upstream_socket = None

    def active(self, lease: Lease) -> bool:
        with self.lock:
            return lease.expires > time.monotonic() and not lease.uncertain and not lease.budget_exhausted and any(value is lease for value in self.leases.values())


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], grants: Grants, upstream: str, provider_key: str) -> None:
        url = urlsplit(upstream)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Invalid provider endpoint")
        self.grants, self.upstream, self.provider_key = grants, url, provider_key
        self.slots = threading.BoundedSemaphore(16)
        # The runtime executes one audit at a time. Grok can nevertheless issue
        # an auxiliary request concurrently with its main agent turn. Queue it
        # here so a single pending usage reservation remains sufficient.
        self.inference_lock = threading.Lock()
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
        self._forward_request()

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
        recorded = False
        started = False
        acquired = False
        phase, error_type, last_event = "request", "IncompleteResponse", "none"
        total, provider_status = 0, 0
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
            # The grant selects the paid model, including CLI compaction calls.
            # Internal CLI model aliases must not select a different provider/model.
            body["model"] = self.server.grants.model
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise ValueError("Missing grant")
            # Drain the bounded request before queueing inference. Otherwise
            # a large next turn blocks in the client's socket send while an
            # auxiliary provider request is still finishing.
            acquired = self.server.inference_lock.acquire(timeout=300)
            if not acquired:
                self.send_error(408, "Model request queue timeout")
                return
            lease = self.server.grants.authorize(authorization[7:], body, len(raw))
            url = self.server.upstream
            phase = "provider_request"
            # The 3.12 typeshed types this port as str. Runtime still needs an int.
            upstream = http.client.HTTPSConnection(str(url.hostname), port=cast(Any, url.port or 443), timeout=min(300, max(1, lease.expires - time.monotonic())))
            self.server.grants.bind_upstream(lease, upstream)
            # This local identifier correlates one authorized send attempt;
            # it is not evidence that the provider received or billed it.
            client_request_id = str(uuid4())
            upstream.request(
                "POST",
                url.path.rstrip("/") + "/responses",
                json.dumps(body),
                {"Authorization": "Bearer " + self.server.provider_key, "Content-Type": "application/json", "X-Client-Request-Id": client_request_id},
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
            self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            started = True
            client_connected = True
            pending = bytearray()
            provider_usage: dict[str, Any] | None = None
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
                    raise ValueError("Response budget expired")
                pending.extend(chunk)
                if len(pending) > 16 * 1024 * 1024:
                    raise ValueError("Provider event limit")
                if body.get("stream"):
                    phase = "provider_event"
                    while b"\n" in pending:
                        line, _, remainder = pending.partition(b"\n")
                        pending = bytearray(remainder)
                        if line.startswith(b"data: ") and line[6:].strip() != b"[DONE]":
                            event = json.loads(line[6:])
                            kind = event.get("type")
                            last_event = (
                                kind
                                if kind
                                in {
                                    "response.created",
                                    "response.in_progress",
                                    "response.completed",
                                    "response.failed",
                                    "response.incomplete",
                                    "error",
                                }
                                else "other"
                            )
                            if kind in {"response.completed", "response.incomplete", "response.failed"}:
                                if provider_usage is not None:
                                    raise ValueError("Duplicate provider completion")
                                provider_usage = event.get("response", {}).get("usage")
                                if not isinstance(provider_usage, dict):
                                    raise ValueError("Unknown provider usage")
                                # A failed or incomplete response may still have
                                # consumed tokens. Settle its valid receipt before
                                # forwarding the original terminal event, not EOF.
                                self.server.grants.record_usage(lease, provider_usage)
                                recorded = True
                if client_connected:
                    try:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    except OSError:
                        # The CLI may exit after its structured terminal result
                        # while a final request is still settling. Drain the paid
                        # response to obtain usage, within this grant's deadline.
                        # Explicit cancellation revokes the grant and stops it.
                        client_connected = False
                        client_write_failed = True
                phase = "provider_read"
            phase = "provider_usage"
            if not body.get("stream"):
                provider_usage = json.loads(pending).get("usage")
            if not isinstance(provider_usage, dict):
                raise ValueError("Unknown provider usage")
            if not recorded:
                self.server.grants.record_usage(lease, provider_usage)
                recorded = True
            if client_connected:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except DuplicateInference:
            self.send_error(409, "Inference already submitted; automatic replay disabled")
        except (ValueError, TypeError, AttributeError, OSError, http.client.HTTPException) as error:
            # Retain fixed metadata, never exception text, response content,
            # request headers, or credentials. The client sees only a failure.
            error_type = type(error).__name__
            if not started:
                self.send_error(403, "Execution request rejected")
        finally:
            if lease is not None and not recorded:
                finished_at = time.monotonic()
                self.server.grants.finish_unknown(
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
                    self.server.grants.release_upstream(lease)
                if acquired:
                    self.server.inference_lock.release()


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
        provider_key: str,
        input_tokens: int = 4_000_000,
        output_tokens: int = 100_000,
        requests: int = 128,
    ) -> None:
        if not socket_path.is_absolute() or not provider_key:
            raise ValueError("Invalid local gateway configuration")
        socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        grants = Grants(
            model,
            requests=requests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
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
    provider_key: str,
    input_tokens: int = 4_000_000,
    output_tokens: int = 100_000,
    requests: int = 128,
) -> LocalGateway:
    """Return a context manager for an execution-scoped grant gateway."""
    return LocalGateway(
        socket_path,
        upstream=upstream,
        model=model,
        provider_key=provider_key,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        requests=requests,
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
            if set(request) not in ({"method", "execution_id"}, {"method", "execution_id", "prior_usage"}):
                raise ValueError("Invalid grant request")
            execution_id = UUID(request["execution_id"])
            grants = self.server.grants
            if request["method"] == "issue":
                prior = request.get("prior_usage")
                token = grants.issue(execution_id, prior)
                prior = prior or {"model_requests": 0, "input_tokens": 0, "output_tokens": 0}
                remaining_requests = max(1, grants.requests - prior["model_requests"])
                result: dict[str, Any] = {
                    "base_url": self.server.base_url,
                    "token": token,
                    "model": grants.model,
                    "max_turns": min(64, remaining_requests),
                    "max_total_turns": remaining_requests,
                    "max_total_tokens": max(1, grants.input_budget + grants.output_budget - prior["input_tokens"] - prior["output_tokens"]),
                    "wall_seconds": grants.ttl,
                }  # noqa: E501
            elif "prior_usage" in request:
                raise ValueError("Prior usage only applies to issue")
            elif request["method"] == "usage":
                result = grants.usage(execution_id)
            elif request["method"] == "revoke":
                grants.revoke(execution_id)
                result = {"ok": True}
            elif request["method"] == "revoke_and_usage":
                result = grants.revoke(execution_id, require_existing=True)
            else:
                raise ValueError("Unsupported grant method")
        except ModelBudgetError:
            result = {"error": "Grant rejected", "code": "RESOURCE_LIMIT"}
        except (ValueError, TypeError, KeyError, OSError):
            result = {"error": "Grant rejected"}
        self.wfile.write(json.dumps(result).encode() + b"\n")


def grant_request(path: str, method: str, execution_id: UUID, *, prior_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"method": method, "execution_id": str(execution_id)}
    if prior_usage is not None:
        request["prior_usage"] = prior_usage
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(path)
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as stream:
            line = stream.readline(16385)
        if len(line) > 16384 or not line.endswith(b"\n"):
            raise ValueError("Invalid grant response")
        result: Any = json.loads(line)
        if isinstance(result, dict) and "error" in result and result.get("code") == "RESOURCE_LIMIT":
            raise ModelBudgetError("Grant budget exhausted")
        if not isinstance(result, dict) or "error" in result:
            raise ValueError("Grant unavailable")
        return result
