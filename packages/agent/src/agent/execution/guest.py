"""Fixed guest entrypoint. Never called by the API or audit worker."""

from __future__ import annotations

import json
from pathlib import Path

from audit.document_tools import ParserLimitError
from audit.evidence import EvidenceLimitError
from audit.protocol import GuestFault

from agent.execution.guest_model import ModelProtocolError
from agent.execution.secure_files import read_json


def run_entrypoint(inputs: Path, work: Path, credentials: Path) -> None:
    manifest = read_json(inputs, ("manifest.json",), 1024 * 1024)
    if manifest.get("operation") != "execute-audit":
        raise ValueError("Unsupported guest operation")
    from agent.core_program import run_audit_manifest

    run_audit_manifest(inputs, work, credentials)


def run_with_faults(inputs: Path, work: Path, credentials: Path) -> None:
    try:
        run_entrypoint(inputs, work, credentials)
        return
    except ParserLimitError:
        fault = GuestFault(code="PARSER_LIMIT")
    except ModelProtocolError:
        fault = GuestFault(code="MODEL_PROTOCOL_ERROR")
    except EvidenceLimitError:
        fault = GuestFault(code="RESOURCE_LIMIT")
    except TimeoutError:
        fault = GuestFault(code="MODEL_TIMEOUT")
    except (ValueError, KeyError, TypeError, RecursionError):
        fault = GuestFault(code="OUTPUT_VALIDATION_ERROR")
    # Unexpected failures (including OOM / killed processes) produce no envelope.
    # The host then reports ISOLATION_UNAVAILABLE.
    (work / "result").mkdir(parents=True, exist_ok=True)
    payload = fault.model_dump(mode="json", exclude_none=True)
    (work / "result/fault.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main() -> None:
    run_with_faults(Path("/inputs"), Path("/work"), Path("/run/ppap"))


if __name__ == "__main__":
    main()
