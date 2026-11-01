"""Local Unix-socket boundary for optional legacy DOC conversion."""

from __future__ import annotations

import json
import os
import socket
import struct
import time
from pathlib import Path
from typing import NoReturn

from .legacy import (
    LegacyDocConversionError,
    LegacyDocConversionLimits,
    convert_legacy_doc_to_docx,
    is_legacy_word_doc,
)

_REQUEST_MAGIC = b"PPAPDOC1"
_HEALTH_REQUEST_MAGIC = b"PPAPHLT1"
_RESPONSE_MAGIC = b"PPAPRES1"
_HEALTH_RESPONSE = b"ok"
_REQUEST_HEADER = struct.Struct("!8sIQ")
_RESPONSE_HEADER = struct.Struct("!8sBQ")
_MAX_SOURCE_NAME_BYTES = 4096
_MAX_ERROR_BYTES = 64 * 1024


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(min(remaining, 1024 * 1024))
        if not chunk:
            raise LegacyDocConversionError(
                "broker_protocol_error",
                "旧 DOC 本地转换服务提前关闭了连接。",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _raise_broker_error(payload: bytes) -> NoReturn:
    try:
        error = json.loads(payload.decode("utf-8"))
        code = str(error["code"])
        message = str(error["message"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise LegacyDocConversionError(
            "broker_protocol_error",
            "旧 DOC 本地转换服务返回了无效错误信息。",
        ) from exc
    raise LegacyDocConversionError(code, message)


def check_legacy_doc_broker(
    socket_path: str | Path,
    *,
    timeout_seconds: float = 2.0,
) -> None:
    """Verify that the local broker accepts and answers its protocol."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout_seconds)
    try:
        connection.connect(str(socket_path))
    except OSError as exc:
        connection.close()
        raise LegacyDocConversionError(
            "broker_unavailable",
            f"无法连接旧 DOC 本地转换服务：{exc}",
        ) from exc

    with connection:
        try:
            connection.sendall(_REQUEST_HEADER.pack(_HEALTH_REQUEST_MAGIC, 0, 0))
            header = _receive_exact(connection, _RESPONSE_HEADER.size)
            magic, status, payload_size = _RESPONSE_HEADER.unpack(header)
            if magic != _RESPONSE_MAGIC or payload_size > _MAX_ERROR_BYTES:
                raise LegacyDocConversionError(
                    "broker_protocol_error",
                    "旧 DOC 本地转换服务健康检查返回了无效协议头。",
                )
            payload = _receive_exact(connection, payload_size)
        except socket.timeout as exc:
            raise LegacyDocConversionError(
                "broker_timeout",
                "旧 DOC 本地转换服务健康检查响应超时。",
            ) from exc
        except OSError as exc:
            raise LegacyDocConversionError(
                "broker_io_error",
                f"旧 DOC 本地转换服务健康检查通信失败：{exc}",
            ) from exc

    if status == 1:
        _raise_broker_error(payload)
    if status != 0 or payload != _HEALTH_RESPONSE:
        raise LegacyDocConversionError(
            "broker_protocol_error",
            "旧 DOC 本地转换服务健康检查返回了无效响应。",
        )


def convert_legacy_doc_via_local_broker(
    file_content: bytes,
    source_name: str,
    socket_path: str | Path,
    *,
    limits: LegacyDocConversionLimits | None = None,
) -> bytes:
    """Send one validated DOC to a same-host converter over a Unix socket."""
    active_limits = limits or LegacyDocConversionLimits()
    if len(file_content) > active_limits.max_input_bytes:
        raise LegacyDocConversionError(
            "input_too_large",
            f"旧 DOC 超过 {active_limits.max_input_bytes} 字节大小上限。",
        )
    if not is_legacy_word_doc(file_content, source_name):
        raise LegacyDocConversionError(
            "invalid_legacy_doc",
            "文件不是扩展名与内容一致的真实的 Word 97-2003 DOC，已拒绝启动转换器。",
        )

    source_name_bytes = source_name.encode("utf-8")
    if not source_name_bytes or len(source_name_bytes) > _MAX_SOURCE_NAME_BYTES:
        raise LegacyDocConversionError(
            "invalid_source_name",
            "旧 DOC 文件名为空或超过本地转换服务限制。",
        )

    request = _REQUEST_HEADER.pack(
        _REQUEST_MAGIC,
        len(source_name_bytes),
        len(file_content),
    )
    deadline = time.monotonic() + min(active_limits.timeout_seconds, 2.0)
    while True:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(active_limits.timeout_seconds + 5.0)
        try:
            connection.connect(str(socket_path))
            break
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            connection.close()
            if time.monotonic() >= deadline:
                raise LegacyDocConversionError(
                    "broker_unavailable",
                    "旧 DOC 本地转换服务未就绪；请检查可选转换容器和 Unix socket。",
                ) from exc
            time.sleep(0.1)
        except OSError as exc:
            connection.close()
            raise LegacyDocConversionError(
                "broker_unavailable",
                f"无法连接旧 DOC 本地转换服务：{exc}",
            ) from exc

    with connection:
        try:
            connection.sendall(request)
            connection.sendall(source_name_bytes)
            connection.sendall(file_content)
            header = _receive_exact(connection, _RESPONSE_HEADER.size)
            magic, status, payload_size = _RESPONSE_HEADER.unpack(header)
            if magic != _RESPONSE_MAGIC:
                raise LegacyDocConversionError(
                    "broker_protocol_error",
                    "旧 DOC 本地转换服务返回了无效协议头。",
                )
            limit = active_limits.max_output_bytes if status == 0 else _MAX_ERROR_BYTES
            if payload_size > limit:
                raise LegacyDocConversionError(
                    "broker_protocol_error",
                    "旧 DOC 本地转换服务返回的数据超过限制。",
                )
            payload = _receive_exact(connection, payload_size)
        except socket.timeout as exc:
            raise LegacyDocConversionError(
                "broker_timeout",
                "旧 DOC 本地转换服务响应超时。",
            ) from exc
        except OSError as exc:
            raise LegacyDocConversionError(
                "broker_io_error",
                f"旧 DOC 本地转换服务通信失败：{exc}",
            ) from exc

    if status == 0:
        return payload
    if status == 1:
        _raise_broker_error(payload)
    raise LegacyDocConversionError(
        "broker_protocol_error",
        "旧 DOC 本地转换服务返回了未知状态。",
    )


def convert_legacy_doc_for_runtime(
    file_content: bytes,
    source_name: str,
    *,
    limits: LegacyDocConversionLimits | None = None,
) -> bytes:
    """Use the configured same-host broker, or the in-process local converter."""
    socket_path = os.environ.get("LEGACY_DOC_CONVERTER_SOCKET", "").strip()
    if socket_path:
        return convert_legacy_doc_via_local_broker(
            file_content,
            source_name,
            socket_path,
            limits=limits,
        )
    return convert_legacy_doc_to_docx(file_content, source_name, limits=limits)


def _send_response(connection: socket.socket, status: int, payload: bytes) -> None:
    connection.sendall(_RESPONSE_HEADER.pack(_RESPONSE_MAGIC, status, len(payload)))
    connection.sendall(payload)


def _serve_connection(
    connection: socket.socket,
    limits: LegacyDocConversionLimits,
) -> None:
    try:
        header = _receive_exact(connection, _REQUEST_HEADER.size)
        magic, source_name_size, content_size = _REQUEST_HEADER.unpack(header)
        if magic == _HEALTH_REQUEST_MAGIC:
            if source_name_size != 0 or content_size != 0:
                raise LegacyDocConversionError(
                    "broker_protocol_error",
                    "旧 DOC 本地转换服务健康检查请求无效。",
                )
            _send_response(connection, 0, _HEALTH_RESPONSE)
            return
        if magic != _REQUEST_MAGIC:
            raise LegacyDocConversionError(
                "broker_protocol_error",
                "旧 DOC 本地转换请求协议无效。",
            )
        if source_name_size < 1 or source_name_size > _MAX_SOURCE_NAME_BYTES:
            raise LegacyDocConversionError(
                "invalid_source_name",
                "旧 DOC 文件名为空或超过本地转换服务限制。",
            )
        if content_size > limits.max_input_bytes:
            raise LegacyDocConversionError(
                "input_too_large",
                f"旧 DOC 超过 {limits.max_input_bytes} 字节大小上限。",
            )
        source_name = _receive_exact(connection, source_name_size).decode("utf-8")
        content = _receive_exact(connection, content_size)
        converted = convert_legacy_doc_to_docx(content, source_name, limits=limits)
        _send_response(connection, 0, converted)
    except LegacyDocConversionError as exc:
        payload = json.dumps(
            {"code": exc.code, "message": str(exc)},
            ensure_ascii=False,
        ).encode("utf-8")[:_MAX_ERROR_BYTES]
        try:
            _send_response(connection, 1, payload)
        except OSError:
            pass
    except (OSError, UnicodeDecodeError, struct.error) as exc:
        payload = json.dumps(
            {"code": "broker_protocol_error", "message": f"旧 DOC 本地转换请求失败：{exc}"},
            ensure_ascii=False,
        ).encode("utf-8")[:_MAX_ERROR_BYTES]
        try:
            _send_response(connection, 1, payload)
        except OSError:
            pass


def serve_legacy_doc_conversion(
    socket_path: str | Path,
    *,
    limits: LegacyDocConversionLimits | None = None,
) -> None:
    """Serve bounded conversions serially on a local Unix socket."""
    active_limits = limits or LegacyDocConversionLimits()
    path = Path(socket_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_socket():
        path.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        path.chmod(0o660)
        server.listen(8)
        while True:
            connection, _address = server.accept()
            with connection:
                connection.settimeout(active_limits.timeout_seconds + 5.0)
                _serve_connection(connection, active_limits)
    finally:
        server.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
