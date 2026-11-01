"""Credential persistence and recovery boundaries, without a real keychain."""

import asyncio
import hashlib
import subprocess
import sys

import pytest
from filelock import Timeout
from mcp.shared.auth import OAuthToken
from mcp_client import ClientError
from mcp_client.auth import SharedOAuth, secure_store, server_url

URL = "https://ppap.example/ppap/mcp"


class TestKeychain:
    __test__ = False

    def __init__(self):
        self.key = None

    def get_password(self, service, user):
        return self.key

    def set_password(self, service, user, value):
        self.key = value


@pytest.mark.asyncio
async def test_encrypted_credentials_survive_reopen_but_missing_key_fails_closed(tmp_path, monkeypatch):
    keychain = TestKeychain()
    monkeypatch.setattr("mcp_client.auth._native_keychain", lambda: keychain)
    store = secure_store(tmp_path)
    await store.put("sample", {"access_token": "private-test-credential"})
    reopened = secure_store(tmp_path)
    assert await reopened.get("sample") == {"access_token": "private-test-credential"}
    content = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert b"private-test-credential" not in content and keychain.key.encode() not in content
    keychain.key = None
    with pytest.raises(ClientError, match="credential_key_missing"):
        secure_store(tmp_path)


def test_unavailable_keychain_never_creates_plaintext_store(tmp_path, monkeypatch):
    def denied():
        raise RuntimeError("secret diagnostic must not escape")

    monkeypatch.setattr("mcp_client.auth._native_keychain", denied)
    with pytest.raises(ClientError, match="^native_credential_store_unavailable$"):
        secure_store(tmp_path)
    assert not (tmp_path / "credentials").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_restart_recovers_refresh_claim_from_durable_rotated_token(tmp_path, monkeypatch, committed):
    keychain = TestKeychain()
    monkeypatch.setattr("mcp_client.auth._native_keychain", lambda: keychain)
    store = secure_store(tmp_path)
    first = SharedOAuth(URL, tmp_path, store)
    await first.adapter.set_tokens(OAuthToken(access_token="first", refresh_token="old", token_type="Bearer", expires_in=900))
    await store.put("refresh", {"token_hash": hashlib.sha256(b"old").hexdigest()}, collection="ppap-client-claim")
    if committed:
        await first.adapter.set_tokens(OAuthToken(access_token="second", refresh_token="new", token_type="Bearer", expires_in=900))
    restarted = SharedOAuth(URL, tmp_path, secure_store(tmp_path))
    if committed:
        assert await restarted.bearer() == "second"
    else:
        with pytest.raises(ClientError, match="authorization_required"):
            await restarted.bearer()
        assert await restarted.adapter.get_tokens() is None
    assert await store.get("refresh", collection="ppap-client-claim") is None


@pytest.mark.asyncio
async def test_profile_lock_excludes_other_process_and_recovers_after_exit(tmp_path):
    # A real separate process holds the same file lock, then exits abruptly.
    code = "from filelock import FileLock; import sys,time; lock=FileLock(sys.argv[1]); lock.acquire(); print('locked',flush=True); time.sleep(30)"
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "oauth.lock")], stdout=subprocess.PIPE, text=True)
    try:
        assert await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 10) == "locked\n"
        auth = SharedOAuth(URL, tmp_path, None)
        lock = auth.lock()
        lock.timeout = 0.1
        with pytest.raises(Timeout):
            async with lock:
                pytest.fail("cross-process lock bypassed")
    finally:
        process.kill()
        process.wait(timeout=5)
        process.stdout.close()
    async with auth.lock():
        pass


@pytest.mark.parametrize("url", ["http://remote.example/mcp", "https://user:secret@example.com/mcp", URL + "?key=secret", URL + "#fragment", "https://ppap.example/other"])
def test_invalid_server_urls_are_rejected(url):
    with pytest.raises(ClientError, match="invalid_server_url"):
        server_url(url)
