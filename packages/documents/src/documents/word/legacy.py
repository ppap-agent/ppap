"""Restricted local conversion for real Word 97-2003 binary documents."""

from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from typing import BinaryIO, Callable, Mapping, Sequence

import olefile

from .parser import extract_docx_structure

OLE_COMPOUND_FILE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


class LegacyDocConversionError(ValueError):
    """Fail-closed legacy DOC conversion error with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LegacyDocConversionLimits:
    """Hard limits for the optional local office process."""

    max_input_bytes: int = 25 * 1024 * 1024
    max_output_bytes: int = 50 * 1024 * 1024
    timeout_seconds: float = 45.0
    cpu_seconds: int = 40
    address_space_bytes: int = 2 * 1024 * 1024 * 1024
    max_open_files: int = 128


def is_legacy_word_doc(file_content: bytes, source_name: str) -> bool:
    """Require both a .doc name and the WordDocument OLE stream."""
    if Path(source_name).suffix.lower() != ".doc":
        return False
    if not file_content.startswith(OLE_COMPOUND_FILE_MAGIC):
        return False
    try:
        with olefile.OleFileIO(BytesIO(file_content)) as compound_file:
            return bool(compound_file.exists("WordDocument"))
    except (OSError, IOError, ValueError):
        return False


def _resolve_executable(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("LEGACY_DOC_CONVERTER_EXECUTABLE")
    if candidate:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    else:
        for command in ("soffice", "libreoffice"):
            resolved = shutil.which(command)
            if resolved:
                return resolved
    raise LegacyDocConversionError(
        "converter_unavailable",
        "旧 DOC 需要可选的本地 LibreOffice 运行时；当前未找到 soffice 可执行文件。",
    )


def _sandboxed_command(command: Sequence[str]) -> tuple[str, ...]:
    """Run converters with local IPC only and no IP networking."""
    if sys.platform == "darwin":
        sandbox = Path("/usr/bin/sandbox-exec")
        if sandbox.is_file() and os.access(sandbox, os.X_OK):
            return (
                str(sandbox),
                "-p",
                "(version 1) (allow default) (deny network*)",
                *command,
            )
    elif sys.platform.startswith("linux"):
        candidate = os.environ.get(
            "LEGACY_DOC_SANDBOX_EXECUTABLE",
            "ppap-local-converter-sandbox",
        )
        resolved = shutil.which(candidate)
        if resolved:
            return (resolved, *command)

    raise LegacyDocConversionError(
        "sandbox_unavailable",
        "旧 DOC 本地转换缺少禁止外部网络访问的进程沙箱，已拒绝启动转换器。",
    )


def _resource_limiter(limits: LegacyDocConversionLimits) -> Callable[[], None]:
    def apply_limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        # macOS reports RLIMIT_AS but rejects practical finite values. The
        # production optional runtime is Linux, where this is enforced.
        if sys.platform != "darwin":
            resource.setrlimit(
                resource.RLIMIT_AS,
                (limits.address_space_bytes, limits.address_space_bytes),
            )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (limits.max_output_bytes, limits.max_output_bytes),
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.max_open_files, limits.max_open_files))

    return apply_limits


def _local_environment(work_dir: Path) -> Mapping[str, str]:
    """Pass only locale and process basics; never inherit proxies or credentials."""
    environment = {
        "HOME": str(work_dir),
        "TMPDIR": str(work_dir),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    for name in ("LANG", "LC_ALL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _run_converter(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    limits: LegacyDocConversionLimits,
) -> tuple[int, bytes, bytes]:
    sandboxed = _sandboxed_command(command)
    with TemporaryFile() as stdout_file, TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            sandboxed,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            preexec_fn=_resource_limiter(limits),
        )
        try:
            process.communicate(timeout=limits.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise LegacyDocConversionError(
                "conversion_timeout",
                f"旧 DOC 本地转换超过 {limits.timeout_seconds:g} 秒限制。",
            ) from exc

        def _tail(stream: BinaryIO) -> bytes:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 4096))
            return stream.read(4096)

        return process.returncode, _tail(stdout_file), _tail(stderr_file)


def convert_legacy_doc_to_docx(
    file_content: bytes,
    source_name: str,
    *,
    executable: str | None = None,
    limits: LegacyDocConversionLimits | None = None,
    temp_root: str | Path | None = None,
) -> bytes:
    """Convert one validated binary DOC locally and return bounded DOCX bytes."""
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

    converter = _resolve_executable(executable)
    root = str(temp_root) if temp_root is not None else None
    with TemporaryDirectory(prefix="ppap-legacy-doc-", dir=root) as work_dir_str:
        work_dir = Path(work_dir_str)
        input_path = work_dir / "input.doc"
        output_dir = work_dir / "output"
        profile_dir = work_dir / "profile"
        output_dir.mkdir()
        profile_dir.mkdir()
        input_path.write_bytes(file_content)

        command = (
            converter,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--nolockcheck",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            "docx:Office Open XML Text",
            "--outdir",
            str(output_dir),
            str(input_path),
        )
        try:
            return_code, stdout, stderr = _run_converter(
                command,
                environment=_local_environment(work_dir),
                limits=active_limits,
            )
        except LegacyDocConversionError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise LegacyDocConversionError(
                "converter_start_failed",
                f"旧 DOC 本地转换器无法安全启动：{exc}",
            ) from exc
        if return_code != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
            suffix = f"：{detail}" if detail else "。"
            raise LegacyDocConversionError(
                "conversion_failed",
                f"旧 DOC 本地转换失败，LibreOffice 退出码为 {return_code}{suffix}",
            )

        outputs = list(output_dir.glob("*.docx"))
        if len(outputs) != 1:
            raise LegacyDocConversionError(
                "missing_conversion_output",
                "旧 DOC 本地转换未产生唯一的 DOCX 输出。",
            )
        output_size = outputs[0].stat().st_size
        if output_size > active_limits.max_output_bytes:
            raise LegacyDocConversionError(
                "output_too_large",
                f"转换后的 DOCX 超过 {active_limits.max_output_bytes} 字节大小上限。",
            )
        converted = outputs[0].read_bytes()
        parsed = extract_docx_structure(converted)
        if not parsed.valid or not (parsed.blocks or parsed.headers or parsed.footers):
            raise LegacyDocConversionError(
                "conversion_quality",
                "转换后的 DOCX 结构无效或没有可审核内容，已停止审核。",
            )
        return converted
