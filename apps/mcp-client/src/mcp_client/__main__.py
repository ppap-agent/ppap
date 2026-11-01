"""stdio PPAP MCP bridge; secrets and file bytes never appear on stdout."""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path
from uuid import UUID

from fastmcp import Client
from fastmcp.server.providers.proxy import FastMCPProxy
from mcp.types import ToolAnnotations
from platformdirs import user_data_path

from mcp_client import ClientError
from mcp_client.auth import SharedOAuth, secure_store, server_url
from mcp_client.files import LocalFiles


def bridge(url, auth, roots, *, max_download_bytes=2 * 1024**3, client_factory=None, http_factory=None):
    factory = client_factory or (lambda: Client(url, auth=auth, timeout=300))
    kwargs = {"http_factory": http_factory} if http_factory else {}
    files = LocalFiles(roots, url, auth, factory, max_download_bytes=max_download_bytes, **kwargs)
    server = FastMCPProxy(
        client_factory=factory,
        name="PPAP",
        mask_error_details=True,
        provider_error_strategy="raise",
        list_page_size=25,
        instructions="Remote PPAP tools keep their normal approval and receipt rules. Use local_upload_file for files inside the configured directories. "
        "Keep the same request_id and original bytes after interruption. Use local_download_file only with URLs created through this connection. "
        "Local downloads never overwrite existing files. An uploaded file does not imply validation or audit success.",
    )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def local_file_roots() -> dict:
        """Read the user-configured directories available for local upload/download."""
        return {"directories": [str(root) for root in files.roots], "upload_limit_bytes": 200 * 1024 * 1024, "download_limit_bytes": max_download_bytes}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
    async def local_upload_file(path: str, request_id: UUID, file_id: UUID | None = None, prevalidate: bool = True) -> dict:
        """Upload a local file (<=200 MiB), finalize and optionally prevalidate it. Supply an absolute path within local_file_roots.

        Keep the UUID and file bytes unchanged when resuming. file_id appends an independent version to that file.
        A successful file upload includes a separate validation receipt, which may still be pending or failed.
        """
        try:
            return await files.upload(path, request_id, file_id, prevalidate=prevalidate)
        except ClientError as exc:
            return {"status": "error", "error": str(exc), "request_id": str(request_id)}
        except OSError:
            return {"status": "error", "error": "local_file_unavailable", "request_id": str(request_id)}
        except Exception:
            return {"status": "unknown", "error": "read_upload_receipt", "request_id": str(request_id)}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
    async def local_download_file(transfer_url: str, destination: str, expected_sha256: str | None = None) -> dict:
        """Stream an original/PDF/ZIP into a new local file. First obtain a download URL using this connection's remote tool.

        destination must be absolute and inside local_file_roots; parent directories must exist. Existing files are never overwritten.
        Pass the original's SHA-256 when known; server-provided checksums are also checked.
        """
        try:
            return await files.download(transfer_url, destination, expected_sha256=expected_sha256)
        except ClientError as exc:
            return {"status": "error", "error": str(exc)}
        except OSError:
            return {"status": "error", "error": "local_destination_unavailable"}
        except Exception:
            return {"status": "error", "error": "download_incomplete_retry_original_url"}

    return server


def main():
    parser = argparse.ArgumentParser(description="PPAP MCP stdio adapter with local file transfer. OAuth opens your browser on first use.")
    parser.add_argument("--server", required=True, help="PPAP HTTPS MCP URL; HTTP is allowed only on loopback")
    parser.add_argument("--directory", required=True, action="append", type=Path, help="Existing local directory to allow; repeat for multiple directories")
    parser.add_argument("--profile", default="default", help="Credential profile; use separate profiles for different people or consent scopes")
    parser.add_argument("--max-download-mib", default=2048, type=int, help="Maximum size of each download, default 2048 MiB")
    args = parser.parse_args()
    try:
        url = server_url(args.server)
        if not 1 <= args.max_download_mib <= 102400:
            raise ClientError("invalid_download_limit")
        directory = user_data_path("ppap-mcp-client") / hashlib.sha256((url + "\n" + args.profile).encode()).hexdigest()
        auth = SharedOAuth(url, directory, secure_store(directory))
        server = bridge(url, auth, args.directory, max_download_bytes=args.max_download_mib * 1024**2)
        # OAuth helpers log authorization URLs at INFO, and transport exceptions
        # can include credential requests. Keep only explicit local error codes.
        logging.getLogger("fastmcp").setLevel(logging.CRITICAL)
        logging.getLogger("mcp").setLevel(logging.CRITICAL)
        logging.getLogger("httpx").setLevel(logging.CRITICAL)
        logging.getLogger("httpcore").setLevel(logging.CRITICAL)
        server.run(transport="stdio", show_banner=False, log_level="CRITICAL")
    except ClientError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("ppap_mcp_client_start_failed", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
