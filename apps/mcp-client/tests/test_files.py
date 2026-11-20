"""Local filesystem and byte transport contracts; no product-server substitute."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from mcp_client import MAX_UPLOAD_BYTES, ClientError
from mcp_client.files import LocalFiles

URL = "https://ppap.example/ppap/mcp"


class Auth:
    async def bearer(self):
        return "test-only-token"


class Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def ping(self):
        pass


def local(root, handler=None, **kwargs):
    return LocalFiles([root], URL, Auth(), Connection, http_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handler), **options), **kwargs)


def test_snapshot_fixes_bytes_and_enforces_200_mib_boundary(tmp_path):
    source = tmp_path / "original.pdf"
    source.write_bytes(b"original")
    files = local(tmp_path)
    with files.snapshot(str(source)) as (frozen, metadata):
        source.write_bytes(b"replacement")
        assert frozen.read() == b"original"
        assert metadata["sha256"] == hashlib.sha256(b"original").hexdigest()
    assert frozen.closed
    with source.open("wb") as file:
        file.truncate(MAX_UPLOAD_BYTES)
    with files.snapshot(str(source)) as (frozen, metadata):
        assert metadata["byte_size"] == MAX_UPLOAD_BYTES
        assert os.fstat(frozen.fileno()).st_size == MAX_UPLOAD_BYTES
    with source.open("ab") as file:
        file.write(b"x")
    with pytest.raises(ClientError, match="file_size_out_of_range"):
        with files.snapshot(str(source)):
            pytest.fail("oversized file accepted")


def test_paths_reject_escape_symlinks_and_special_files(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("private")
    (root / "link").symlink_to(outside)
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(root / "pipe")
    files = local(root)
    for path in (outside, root / "link", root / "escape" / "secret.txt", root / ".." / "secret.txt", Path("relative"), root / "pipe"):
        with pytest.raises((ClientError, OSError)):
            with files.snapshot(str(path)):
                pytest.fail("unsafe input accepted")


def test_replaced_root_ancestor_cannot_redirect_file_access(tmp_path):
    parent = tmp_path / "parent"
    root = parent / "allowed"
    root.mkdir(parents=True)
    files = local(root)
    parent.rename(tmp_path / "original-parent")
    other = tmp_path / "outside"
    (other / "allowed").mkdir(parents=True)
    (other / "allowed" / "secret").write_text("private")
    parent.symlink_to(other, target_is_directory=True)
    with pytest.raises(ClientError, match="local_directory_changed"):
        with files.snapshot(str(root / "secret")):
            pytest.fail("replaced root accepted")


@pytest.mark.asyncio
async def test_download_atomic_no_overwrite_and_checksum(tmp_path):
    content = b"report bytes"
    digest = hashlib.sha256(content).hexdigest()
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(content), headers={"x-content-sha256": digest})

    files = local(tmp_path, respond)
    target = tmp_path / "report.pdf"
    url = URL + "/transfers/" + str(uuid4())
    result = await files.download(url, str(target), expected_sha256=digest)
    assert result["sha256"] == digest and target.read_bytes() == content
    assert seen[0].headers["authorization"] == "Bearer test-only-token"
    with pytest.raises(ClientError, match="destination_exists"):
        await files.download(url, str(target))
    assert len(seen) == 1 and target.read_bytes() == content
    assert not list(tmp_path.glob(".ppap-download-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["size", "checksum", "redirect", "interrupted", "race"])
async def test_failed_download_leaves_no_partial_or_overwrite(tmp_path, failure):
    target = tmp_path / "report.zip"

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"start"
            raise httpx.ReadError("controlled disconnect")

    def respond(request):
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example/file"})
        if failure == "interrupted":
            return httpx.Response(200, stream=Broken())
        if failure == "race":
            target.write_bytes(b"other writer")
        return httpx.Response(200, stream=httpx.ByteStream(b"report"), headers={"x-content-sha256": "0" * 64} if failure == "checksum" else {})

    files = local(tmp_path, respond, max_download_bytes=3 if failure == "size" else 100)
    with pytest.raises((ClientError, httpx.ReadError, FileExistsError)):
        await files.download(URL + "/transfers/" + str(uuid4()), str(target))
    assert target.read_bytes() == b"other writer" if failure == "race" else not target.exists()
    assert not list(tmp_path.glob(".ppap-download-*"))


@pytest.mark.asyncio
async def test_transfer_rejects_other_origin_and_modified_paths_before_credentials(tmp_path):
    files = local(tmp_path)
    for url in ("https://attacker.example/mcp/transfers/" + str(uuid4()), URL + "/transfers/../secret", URL + "/transfers/" + str(uuid4()) + "?path=other"):
        with pytest.raises(ClientError, match="untrusted_transfer_url"):
            await files.download(url, str(tmp_path / "result"))


def test_listing_shows_regular_files_only(tmp_path):
    (tmp_path / "10 控制计划").mkdir()
    (tmp_path / "10 控制计划" / "控制计划.xlsx").write_bytes(b"plan")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "link.xlsx").symlink_to(tmp_path / "10 控制计划" / "控制计划.xlsx")
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    (outside / "secret.xlsx").write_text("private")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    listed = local(tmp_path).listing()
    assert [row["relative_path"] for row in listed] == [str(Path("10 控制计划") / "控制计划.xlsx")]
    assert listed[0]["byte_size"] == 4


@pytest.mark.asyncio
async def test_folder_upload_derives_one_request_per_file_and_returns_audit_inputs(tmp_path):
    folder = tmp_path / "测试文件"
    (folder / "06 MSA").mkdir(parents=True)
    (folder / "06 MSA" / "MSA-1.xls").write_bytes(b"a")
    (folder / "PFMEA.xlsx").write_bytes(b"b")
    files = local(tmp_path)
    calls = []

    async def upload(path, request_id, file_id=None):
        calls.append((Path(path).name, request_id))
        if Path(path).name == "PFMEA.xlsx":
            return {"status": "error", "error": "upload_transfer_refused", "request_id": str(request_id)}
        return {"status": "ok", "data": {"file": {"id": "v-" + Path(path).stem}, "validation": {"id": "c-" + Path(path).stem}}}

    files.upload = upload
    request_id = uuid4()
    first = await files.upload_folder(str(folder), request_id)
    assert first["status"] == "partial" and first["uploaded"] == 1 and first["failed"] == 1
    assert first["audit_files"] == [{"file_version_id": "v-MSA-1", "validation_id": "c-MSA-1", "selection": "selected", "removal_reason": None}]
    again = await files.upload_folder(str(folder), request_id)
    assert [call[1] for call in calls[:2]] == [call[1] for call in calls[2:]]
    assert again["files"] == first["files"]
    with pytest.raises(ClientError, match="local_path_not_allowed"):
        await files.upload_folder(str(tmp_path.parent), request_id)


def test_adapter_offers_tools_only(tmp_path):
    from mcp.server.lowlevel.server import NotificationOptions
    from mcp_client.__main__ import bridge

    server = bridge(URL, Auth(), [tmp_path], client_factory=Connection)
    offered = server._mcp_server.get_capabilities(NotificationOptions(), {})
    assert offered.tools is not None and offered.resources is None and offered.prompts is None
