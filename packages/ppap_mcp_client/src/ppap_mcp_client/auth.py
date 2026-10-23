"""FastMCP OAuth with native-keychain encryption and process-safe rotation.

OAuth/PKCE/discovery remain in FastMCP and the MCP SDK. The local adapter owns
only credential persistence and the single-use refresh boundary. A new OAuth
object per HTTP flow reloads rotated credentials after taking the process lock.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet
from fastmcp.client.auth import OAuth
from fastmcp.client.auth.oauth import TokenStorageAdapter
from filelock import AsyncFileLock, FileLock
from key_value.aio.stores.filetree import FileTreeStore, FileTreeV1KeySanitizationStrategy
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

from ppap_mcp_client import ClientError


def server_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})) or url.username or url.password or url.query or url.fragment:
        raise ClientError("invalid_server_url")
    if not url.hostname or not url.path.endswith("/mcp") or "\\" in value or any(ord(c) < 33 for c in value):
        raise ClientError("invalid_server_url")
    return value


def secure_store(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with FileLock(directory / "oauth.lock", timeout=360):
        return _secure_store(directory)


def _native_keychain():
    import sys

    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring

        return Keyring()
    elif sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()
    else:
        from keyring.backends.SecretService import Keyring

        return Keyring()


def _secure_store(directory: Path):
    """Use native credential storage for the key; never fall back to plaintext."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    name = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()
    try:
        keychain = _native_keychain()
        key = keychain.get_password("PPAP MCP", name)
        if key is None:
            # A missing encryption key with existing ciphertext is not an empty
            # account. Replacing it would silently abandon live delegations.
            if (directory / "credentials").exists():
                raise ClientError("credential_key_missing")
            key = Fernet.generate_key().decode("ascii")
            keychain.set_password("PPAP MCP", name, key)
    except ClientError:
        raise
    except Exception:
        raise ClientError("native_credential_store_unavailable") from None
    return encrypted_store(directory, key.encode("ascii"))


def encrypted_store(directory: Path, key: bytes):
    # SDK keys include full URLs. FileTreeStore's default passes slashes through
    # as directory separators; select its supported filename sanitization.
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = FileTreeStore(data_directory=directory / "credentials", key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory))
    return FernetEncryptionWrapper(key_value=files, fernet=Fernet(key))


class SharedOAuth(httpx.Auth):
    def __init__(self, url: str, directory: Path, storage, *, oauth_factory=OAuth):
        self.url = server_url(url)
        self.directory, self.storage, self.oauth_factory = directory, storage, oauth_factory
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.adapter = TokenStorageAdapter(storage, self.url)

    def lock(self):
        # Each flow has its own lock instance: sharing a reentrant instance
        # between coroutines would let a second flow bypass the process lock.
        return AsyncFileLock(self.directory / "oauth.lock", timeout=360, thread_local=False)

    async def _recover(self):
        marker = await self.storage.get("refresh", collection="ppap-client-claim")
        if marker is None:
            return
        tokens = await self.adapter.get_tokens()
        if tokens is None or hashlib.sha256((tokens.refresh_token or "").encode()).hexdigest() == marker["token_hash"]:
            # Unknown refresh outcome: start a fresh browser consent, never
            # replay the credential that may already have been consumed.
            await self.adapter.clear()
        await self.storage.delete("refresh", collection="ppap-client-claim")

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        async with self.lock():
            await self._recover()
            registered = await self.adapter.get_client_info()
            port = None
            if registered is not None:
                redirects = registered.redirect_uris or []
                callback = urlsplit(str(redirects[0])) if len(redirects) == 1 else None
                if (
                    callback is None
                    or callback.scheme != "http"
                    or callback.hostname != "127.0.0.1"
                    or callback.path != "/callback"
                    or not callback.port
                    or callback.username
                    or callback.password
                    or callback.query
                    or callback.fragment
                ):
                    raise ClientError("invalid_cached_oauth_callback")
                # DCR binds the redirect URI. A new random port on restart would
                # make reauthorization fail even though token refresh works.
                port = callback.port
            oauth = self.oauth_factory(
                mcp_url=self.url,
                token_storage=self.storage,
                client_name="PPAP local file adapter",
                callback_host="127.0.0.1",
                callback_port=port,
                additional_client_metadata={"token_endpoint_auth_method": "none"},
            )
            # SDK 1.x does not persist issuer metadata. Without restoring the
            # PPAP endpoint contract, a restarted client tries origin /token.
            # Full initial consent still performs SDK discovery/issuer checks.
            issuer = self.url.removesuffix("/mcp") + "/oauth"
            oauth.context.auth_server_url = issuer
            oauth.context.oauth_metadata = OAuthMetadata.model_validate(
                {
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "/authorize",
                    "token_endpoint": issuer + "/token",
                    "registration_endpoint": issuer + "/register",
                    "response_types_supported": ["code"],
                }
            )
            oauth.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate({"resource": self.url, "authorization_servers": [issuer]})
            async with aclosing(oauth.async_auth_flow(request)) as flow:
                response = None
                while True:
                    try:
                        outgoing = await flow.asend(response)
                    except StopAsyncIteration:
                        break
                    if str(outgoing.url) == issuer + "/token":
                        form = parse_qs(outgoing.content.decode("ascii"))
                        if form.get("grant_type") == ["refresh_token"]:
                            old = form["refresh_token"][0]
                            await self.storage.put("refresh", {"token_hash": hashlib.sha256(old.encode()).hexdigest()}, collection="ppap-client-claim")
                    response = yield outgoing
            # A refresh success stored a different refresh token before the
            # business response. If it failed or storage failed, keep the claim.
            marker = await self.storage.get("refresh", collection="ppap-client-claim")
            tokens = await self.adapter.get_tokens()
            if marker and tokens and hashlib.sha256((tokens.refresh_token or "").encode()).hexdigest() != marker["token_hash"]:
                await self.storage.delete("refresh", collection="ppap-client-claim")

    async def bearer(self) -> str:
        # Use only after an authenticated MCP call. Byte transfers never ask
        # HTTPX to replay a consumed upload stream after OAuth negotiation.
        async with self.lock():
            await self._recover()
            tokens = await self.adapter.get_tokens()
            if tokens is None:
                raise ClientError("authorization_required")
            return tokens.access_token
