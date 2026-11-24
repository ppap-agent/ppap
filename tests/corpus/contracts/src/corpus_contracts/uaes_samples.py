"""UAES 样本按零件目录整批运行：文件检查 → 上传识别 → 预检（含内容级）→ 已匹配文件全部审核。

每个零件目录 ``assets/uaes/samples/<零件>/测试文件`` 整批提交一次，``比对文件/`` 一并提交为比对件（不单独审核）。
每份缺陷样本（``缺陷样本(-test-)``）单独成批：沿用正常批次，把它对应的正常件换成缺陷件——缺陷样本是文件夹时，
换掉同一类目文件夹里的全部正常件。真实提交不会同时出现同一文件的正常件与缺陷件。

这里只产出结果，不判对错：真值在 ``uaes-oracle.csv``，评测集只拿这些结果做差异分析。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from corpus_contracts.oracle import REPO_ROOT, customer_rules, suite_sources

SAMPLES = REPO_ROOT / "assets/uaes/samples"
SUBMITTED = "测试文件"
REFERENCES = "比对文件"
DEFECTS = "缺陷样本(-test-)"
_DEFECT_SUFFIX = re.compile(r"\s*-\s*test-.*$", re.IGNORECASE)


def _files(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in directory.rglob("*") if path.is_file() and not path.name.startswith("."))) if directory.is_dir() else ()


@dataclass(frozen=True)
class Batch:
    """``files`` 是本批产出结果的文件；``others`` 是缺陷批次里沿用的正常件，``references`` 是比对文件，二者同批提交但不产出结果。"""

    part: str
    files: tuple[Path, ...]
    others: tuple[Path, ...] = ()
    references: tuple[Path, ...] = ()
    defect: str = ""

    @property
    def id(self) -> str:
        # 一个零件目录一份回放：缺陷批次与正常批次的请求大多相同。
        return f"uaes-samples/{self.part}"

    @property
    def label(self) -> str:
        return f"{self.part}/{self.defect}" if self.defect else self.part

    @property
    def has_pdf(self) -> bool:
        return any(path.suffix.lower() == ".pdf" for path in (*self.files, *self.others, *self.references))


def _normal_counterparts(sample: Path, normal: tuple[Path, ...], submitted: Path) -> tuple[Path, ...]:
    """缺陷样本替换的正常件：文件按去掉「-test-…」后同名配对，类目里只有一份正常件时就是它（仓库按内容只存一份、
    文件名可能不同）；文件夹换掉同一类目文件夹里的全部正常件。"""
    category = sample.parent.name
    same_category = tuple(path for path in normal if path.relative_to(submitted).parts[0] == category)
    if sample.is_dir():
        return same_category
    name = _DEFECT_SUFFIX.sub("", sample.stem).strip() + sample.suffix
    found = tuple(path for path in same_category if path.name == name) or (same_category if len(same_category) == 1 else ())
    if len(found) != 1:
        raise ValueError(f"缺陷样本找不到唯一的正常件：{sample.relative_to(SAMPLES)}")
    return found


def batches() -> tuple[Batch, ...]:
    out: list[Batch] = []
    for part in sorted(path for path in SAMPLES.iterdir() if (path / SUBMITTED).is_dir()):
        submitted = part / SUBMITTED
        normal = _files(submitted)
        references = _files(part / REFERENCES)
        out.append(Batch(part.name, normal, references=references))
        defects = part / DEFECTS
        if not defects.is_dir():
            continue
        for category in sorted(path for path in defects.iterdir() if path.is_dir()):
            for sample in sorted(path for path in category.iterdir() if not path.name.startswith(".")):
                replaced = _normal_counterparts(sample, normal, submitted)
                files = _files(sample) if sample.is_dir() else (sample,)
                others = tuple(path for path in normal if path not in replaced)
                out.append(Batch(part.name, files, others, references, sample.name))
    return tuple(out)


def precheck_actual(precheck: dict[str, Any], metadata: dict[str, Any], file_version_id: str) -> dict[str, Any]:
    """产品预检结果里一个文件的结论、计数、问题代码与五项身份字段。"""
    blocks = [item for item in precheck["blocks"] if item["file_version_id"] == file_version_id]
    warnings = [item for item in precheck["warnings"] if item["file_version_id"] == file_version_id]
    level = next(row["level"] for row in precheck["files"] if row["file_version_id"] == file_version_id)
    verdict = "不通过" if level == "block" else "通过" if level in {"ok", "warn"} else f"未预检（{level}）"
    return {
        "预检结果": verdict,
        "阻断数": len(blocks),
        "警告数": len(warnings),
        "问题": sorted(item["code"] for item in [*blocks, *warnings]),
        "项目": metadata.get("project"),
        "客户": metadata.get("customer"),
        "车型": metadata.get("model"),
        "零件号": metadata.get("part_number"),
        "零件名称": metadata.get("part"),
    }


def run_batch(batch: Batch, suites: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """按产品路径提交一批，返回本批 ``files`` 的报告行：每份文件一行预检，每个条款一行审核结论。

    ``suites`` 按目录叶子路径给出要运行的检查程序；不给时用人工真值所对应的冻结版本。"""
    from agent.execution.audit_runtime import run_audit
    from agent.mapping import unproven_pass
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
    from audit.standard_suites import library_revision
    from product.precheck import precheck_task
    from product.precheck_content import entry_key, precheck_content
    from worker.intake.file_analysis import analyze_file
    from worker.intake.file_validation import validate_file

    rules = customer_rules("UAES")
    submitted = [(path, False) for path in (*batch.others, *batch.files)] + [(path, True) for path in batch.references]
    rows: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    sources: dict[str, Path] = {}
    for index, (path, reference) in enumerate(submitted, 1):
        identity = f"f{index:02d}"
        data = path.read_bytes()
        blobs[identity], sources[identity] = data, path
        validation = validate_file(path.name, data)
        match: dict[str, Any] | None = None
        if validation["state"] == "valid":
            result = analyze_file(path.name, data, rules, customer_preference="UAES", reference_only=reference)
            state = result.get("matching_status")
            state = state if state in {"matched", "reference", "unmatched", "failed"} else "failed"
            match = {
                "state": state,
                "metadata": result.get("metadata") or {},
                "document_type": result.get("document_type"),
                "gaps": result.get("notices") or (["rule_matching_failed"] if state == "failed" else []),
                "matched_rule_id": result.get("matched_rule_id"),
            }
        row: dict[str, Any] = {
            "file": {"id": identity, "file_id": identity, "original_filename": path.name, "sha256": hashlib.sha256(data).hexdigest()},
            "selection": "selected",
            "validation": validation,
            "match": match,
        }
        rows.append({**row, "reference_only": True} if reference else row)
    task: dict[str, Any] = {"customer_preference": "UAES", "files": rows}
    content: dict[str, list[dict]] = {}
    for row in rows:
        found: dict[str, Any] = row["match"] or {}
        if found.get("state") != "matched" or row.get("reference_only"):
            continue
        customer = task["customer_preference"]
        key = entry_key(customer, found.get("document_type"))
        if key is None:
            continue
        try:
            content[row["file"]["id"]] = precheck_content(blobs[row["file"]["id"]], row["file"]["original_filename"], key, customer=customer)
        except Exception:  # noqa: BLE001 - as the product: an unreadable file stays at the file-level result
            continue
    precheck = precheck_task(task, content, read=lambda row: blobs[row["file"]["id"]])
    metadata = {row["file"]["id"]: dict((row["match"] or {}).get("metadata") or {}) for row in rows}

    reported = {identity for identity, path in sources.items() if path in batch.files}
    # Every submitted file recognition left out of the audit, and why (its notices), so a missing result has a cause to read.
    missed: list[dict[str, Any]] = [
        {
            "kind": "recognition",
            "batch": batch.label,
            "source": str(sources[row["file"]["id"]].relative_to(REPO_ROOT)),
            "state": (row["match"] or {}).get("state") or row["validation"].get("state"),
            "document_type": (row["match"] or {}).get("document_type"),
            "notices": (row["match"] or {}).get("gaps") or [],
        }
        for row in rows
        if (row["match"] or {}).get("state") not in {"matched", "reference"}
    ]
    report: list[dict[str, Any]] = missed + [
        {"kind": "precheck", "batch": batch.label, "source": str(sources[identity].relative_to(REPO_ROOT)), "actual": precheck_actual(precheck, metadata[identity], identity)}
        for identity in sorted(reported)
    ]

    suites = suites if suites is not None else suite_sources()
    inputs: list[FrozenInput] = []
    targets: list[FrozenTarget] = []
    for row in rows:
        recognized: dict[str, Any] = row["match"] or {}
        identity = row["file"]["id"]
        suite = suites.get(f"uaes/{recognized.get('matched_rule_id')}") if recognized.get("state") == "matched" else None
        if suite is not None:
            role, document_type = "target", suite.document_type
            body = json.loads(suite.files["rules.json"])
            targets.append(
                FrozenTarget(
                    identity, "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, body, RuleBinding(suite.suite_key, checker_digest(suite.program_files), suite.entrypoints)
                )
            )
        elif recognized.get("state") == "reference":
            role, document_type = "reference", recognized.get("document_type") or ""
        else:
            continue  # 未匹配的文件不进入审核。
        meta = metadata[identity]
        inputs.append(
            FrozenInput(
                identity,
                row["file"]["sha256"],
                cast(Literal["target", "reference"], role),
                row["file"]["original_filename"],
                document_type=document_type,
                customer=meta.get("customer"),
                model=meta.get("model"),
                part=meta.get("part"),
            )
        )
    if not targets:
        return report
    snapshot = ExecutionSnapshot("corpus", "uaes-sample-runner", tuple(inputs), tuple(targets), "corpus-source", library_revision(), {}, round_id=batch.id)
    by_digest = {"sha256:" + suite.revision_digest: suite for suite in suites.values()}
    programs = {target.binding.checker_digest: by_digest[target.suite_revision_id].program_files for target in targets}
    result = run_audit(snapshot, {item.file_version_id: blobs[item.file_version_id] for item in inputs}, programs)
    severity = {(target.file_version_id, str(rule["id"])): rule.get("severity") for target in targets for rule in target.rules["rules"]}
    for finding in result["findings"]:
        identity, clause = finding["file_version_id"], str(finding["clause_id"])
        if identity in reported:
            where: dict[str, Any] = {"kind": "finding", "batch": batch.label, "source": str(sources[identity].relative_to(REPO_ROOT)), "clause": clause}
            status, reason = finding.get("status"), str(finding.get("suggestions") or "")
            # As an audit shows it: a PASS that ran no check is a technical error there.
            if unproven := unproven_pass(status, finding.get("checks_performed"), finding.get("empty_scope_basis"), reason):
                status, reason = "ERR", unproven
            report.append({**where, "status": status, "severity": severity.get((identity, clause)), "reason": reason})
    return report


__all__ = ["SAMPLES", "Batch", "batches", "precheck_actual", "run_batch"]
