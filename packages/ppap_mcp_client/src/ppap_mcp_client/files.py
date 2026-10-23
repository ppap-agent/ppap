"""Bounded local bytes. Remote tools retain product authority and receipts."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
import tempfile
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import anyio
import httpx

from ppap_mcp_client import MAX_UPLOAD_BYTES, ClientError

CHUNK_BYTES = 1024 * 1024


class LocalFiles:
    def __init__(self, roots: list[Path], url, auth, client_factory, *, http_factory=httpx.AsyncClient, max_download_bytes=2 * 1024**3):
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise ClientError("secure_local_paths_unsupported_on_this_platform")
        self.roots = [root.resolve(strict=True) for root in roots]
        if not self.roots or any(not root.is_dir() for root in self.roots):
            raise ClientError("local_directory_required")
        self.root_identities = {root: (root.stat().st_dev, root.stat().st_ino) for root in self.roots}
        self.url, self.auth, self.client_factory, self.http_factory = url, auth, client_factory, http_factory
        self.max_download_bytes = max_download_bytes

    @contextmanager
    def parent(self, path: str):
        candidate = Path(path)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise ClientError("absolute_path_within_allowed_directory_required")
        root = next((r for r in self.roots if candidate.is_relative_to(r) and candidate != r), None)
        if root is None:
            raise ClientError("local_path_not_allowed")
        parts = candidate.relative_to(root).parts
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if (info.st_dev, info.st_ino) != self.root_identities[root]:
                raise ClientError("local_directory_changed_restart_adapter")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            yield directory, parts[-1]
        finally:
            os.close(directory)

    @contextmanager
    def snapshot(self, path):
        with self.parent(path) as (directory, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as source, tempfile.TemporaryFile() as frozen:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ClientError("regular_file_required")
            if not 0 < info.st_size <= MAX_UPLOAD_BYTES:
                raise ClientError("file_size_out_of_range")
            digest = hashlib.sha256()
            size = 0
            while chunk := source.read(CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ClientError("file_size_out_of_range")
                digest.update(chunk)
                frozen.write(chunk)
            after = os.fstat(source.fileno())
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ClientError("local_file_changed_retry_with_same_request")
            frozen.seek(0)
            yield frozen, {"original_filename": name, "byte_size": size, "sha256": digest.hexdigest(), "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream"}

    @asynccontextmanager
    async def async_snapshot(self, path):
        manager = self.snapshot(path)
        # Local disk/USB reads must not block unrelated MCP requests. Cleanup
        # owns the temporary descriptor even if the caller cancels the upload.
        with anyio.CancelScope(shield=True):
            value = await anyio.to_thread.run_sync(manager.__enter__)
        try:
            yield value
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(manager.__exit__, None, None, None)

    @staticmethod
    async def call(client, name, arguments):
        result = await client.call_tool(name, arguments)
        reply = result.structured_content
        if not isinstance(reply, dict) or "status" not in reply:
            raise ClientError("invalid_remote_reply")
        return reply

    @staticmethod
    def matches(version, body, file_id):
        if (file_id and version["file_id"] != file_id) or any(version[k] != body[k] for k in ("original_filename", "byte_size")):
            raise ClientError("upload_request_conflict")
        if version.get("sha256") and version["sha256"] != body["sha256"]:
            raise ClientError("upload_request_conflict")

    def transfer_url(self, value):
        prefix = self.url + "/transfers/"
        if not isinstance(value, str) or not value.startswith(prefix):
            raise ClientError("untrusted_transfer_url")
        ident = value[len(prefix) :]
        try:
            if str(UUID(ident)) != ident:
                raise ValueError()
        except ValueError:
            raise ClientError("untrusted_transfer_url") from None
        return value

    async def upload(self, path: str, request_id: UUID, file_id: UUID | None = None, *, prevalidate: bool = True):
        rid, fid = str(request_id), str(file_id) if file_id else None
        # Snapshot is local disk, not a 200 MiB in-memory or model-context copy.
        async with self.async_snapshot(path) as (frozen, body):
            async with self.client_factory() as client:
                arguments = {"request_id": rid, "body": body, **({"file_id": fid} if fid else {})}
                reply = await self.call(client, "create_file_version" if fid else "create_file", arguments)
                if reply["status"] not in {"ok", "unknown", "running"}:
                    return reply
                receipt = await self.call(client, "get_upload", {"request_id": rid})
                if receipt["status"] != "ok":
                    return receipt
                upload = receipt["data"]["value"]
                version = upload.get("file")
                if not version:
                    return {"status": "unknown", "error": "read_upload_receipt", "request_id": rid}
                self.matches(version, body, fid)
                if version["status"] != "ready":
                    if not upload.get("resume_allowed"):
                        return {"status": "error", "error": "upload_not_resumable", "request_id": rid, "receipt": upload}
                    ids = {"file_id": version["file_id"], "version_id": version["id"]}
                    entry = await self.call(client, "create_upload_transfer", ids)
                    if entry["status"] != "ok":
                        return entry
                    transfer = self.transfer_url(entry["data"]["url"])

                    async def chunks():
                        while part := await anyio.to_thread.run_sync(frozen.read, CHUNK_BYTES):
                            yield part

                    headers = {"Authorization": "Bearer " + await self.auth.bearer(), "Content-Type": body["content_type"], "Content-Length": str(body["byte_size"])}
                    async with self.http_factory(follow_redirects=False, timeout=httpx.Timeout(120, connect=15)) as http:
                        result = await http.put(transfer, headers=headers, content=chunks())
                    if not result.is_success:
                        return {
                            "status": "error" if 400 <= result.status_code < 500 and result.status_code != 408 else "unknown",
                            "error": "upload_transfer_refused" if 400 <= result.status_code < 500 and result.status_code != 408 else "read_upload_receipt",
                            "request_id": rid,
                            "http_status": result.status_code,
                        }
                    finalized = await self.call(client, "finalize_file", {**ids, "request_id": str(uuid5(request_id, "finalize")), "body": {"sha256": body["sha256"]}})
                    receipt = await self.call(client, "get_upload", {"request_id": rid})
                    if receipt["status"] != "ok":
                        return receipt
                    version = receipt["data"]["value"].get("file")
                    if not version or version["status"] != "ready":
                        if finalized["status"] == "error":
                            return {"status": "error", "error": "finalization_failed", "request_id": rid, "finalization": finalized}
                        return {"status": "unknown", "error": "read_upload_receipt", "request_id": rid}
                    self.matches(version, body, fid)
                validation = None
                if prevalidate:
                    validation_id = str(uuid5(request_id, "validate"))
                    validation = await self.call(
                        client, "validate_file", {"request_id": validation_id, "body": {"request_id": validation_id, "source_file_id": version["file_id"], "source_file_version_id": version["id"]}}
                    )
                return {"status": "ok", "request_id": rid, "file": version, "validation": validation}

    async def download(self, transfer_url: str, destination: str, *, expected_sha256: str | None = None):
        url = self.transfer_url(transfer_url)
        if expected_sha256 and (len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256)):
            raise ClientError("invalid_sha256")
        # The URL must have been obtained with this local adapter's delegation.
        # A remote MCP connection's grant cannot be substituted here.
        async with self.client_factory() as client:
            await client.ping()
        headers = {"Authorization": "Bearer " + await self.auth.bearer(), "Accept-Encoding": "identity"}
        with self.parent(destination) as (directory, name):
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ClientError("destination_exists")
            temp = ".ppap-download-" + str(uuid4())
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    digest, total = hashlib.sha256(), 0
                    async with self.http_factory(follow_redirects=False, timeout=httpx.Timeout(120, connect=15)) as http:
                        async with http.stream("GET", url, headers=headers) as response:
                            if not response.is_success:
                                raise ClientError("download_refused_read_original_resource")
                            declared = response.headers.get("content-length")
                            if declared and int(declared) > self.max_download_bytes:
                                raise ClientError("download_too_large")
                            expected = {value for value in (expected_sha256, response.headers.get("x-content-sha256")) if value}
                            async for chunk in response.aiter_raw(CHUNK_BYTES):
                                total += len(chunk)
                                if total > self.max_download_bytes:
                                    raise ClientError("download_too_large")
                                digest.update(chunk)
                                await anyio.to_thread.run_sync(output.write, chunk)
                    if declared and total != int(declared):
                        raise ClientError("download_size_mismatch")
                    checksum = digest.hexdigest()
                    if any(checksum != value for value in expected):
                        raise ClientError("download_sha256_mismatch")
                    output.flush()
                    os.fsync(output.fileno())
                # Link is atomic and refuses a destination created concurrently.
                # There is no overwrite mode, and incomplete bytes stay private.
                os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.fsync(directory)
                return {"status": "ok", "path": destination, "byte_size": total, "sha256": checksum}
            finally:
                os.unlink(temp, dir_fd=directory)
