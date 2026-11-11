"""One table contract for corpus inputs and independently adjudicated outcomes."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import StandardSuite, library_revision, standard_suites
from audit.suite_migration import generic_standard_suites

REPO_ROOT = Path(__file__).resolve().parents[5]
CASES_PATH = REPO_ROOT / "assets/corpus/cases.csv"
ORACLE_PATH = REPO_ROOT / "assets/corpus/oracle.csv"
REVIEWED_RULES_PATH = REPO_ROOT / "assets/corpus/reviewed-rules.json"
DATASETS = ("理想", "蔚来", "UAES")
FILE_COLUMNS = ("数据集", "组合ID", "文件ID", "原件", "提交文件名", "角色", "Suite版本", "Checker组", "Binding")
EXPECTED_COLUMNS = ("数据集", "组合ID", "文件ID", "条款ID", "人工真值", "条款说明", "人工判定依据", "判定人", "判定日期", "已知差距")
TRUTHS = frozenset({"PASS", "NG", "UNABLE", "NA"})
# What the product still does differently from the truth, and why.
_KNOWN_GAP = re.compile(r"^(PASS|NG|UNABLE|NA|ERR) \S.*$")
_REVISION = re.compile(r"^.+@v[1-9][0-9]*$")


@lru_cache(maxsize=1)
def suite_sources() -> dict[str, StandardSuite]:
    """Exact source selectors, never a current/latest Suite lookup."""
    sources = [(f"{suite.suite_key}@v4", suite) for suite in standard_suites()]
    for suite in generic_standard_suites(include_history=True):
        revision = json.loads(suite.files["tests.json"])[0]["dataset"]
        sources.append((f"{suite.suite_key}@{revision}", suite))
    result = {}
    for selector, suite in sources:
        if selector in result:
            raise ValueError("Ambiguous corpus Suite selector: " + selector)
        result[selector] = suite
    return result


def _reviewed_resources(resources: dict) -> dict:
    # Formats and clause-to-method bindings describe the reviewed scope. Class
    # paths belong to the executable identity, not the human judgment.
    return {**resources, "carriers": sorted(resources["carriers"])}


def reviewed_contract(suite: StandardSuite) -> dict:
    """Meaning of existing human answers, distinct from executable identity.

    Read the literal RESOURCES declaration without importing or running it.
    Preserve carrier formats and method bindings, but not implementation paths.
    """
    resources = [
        node.value for node in ast.parse(suite.files["checker.py"]).body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RESOURCES" for target in node.targets)
    ]
    if len(resources) != 1:
        raise ValueError("Corpus Suite requires one explicit source binding: " + suite.suite_key)
    return {
        "document_type": suite.document_type,
        "rules": json.loads(suite.files["rules.json"]),
        "appendix": json.loads(suite.files["appendix.json"]),
        "entrypoints": dict(suite.entrypoints),
        "resources": _reviewed_resources(ast.literal_eval(resources[0])),
        "related_suite_keys": sorted(suite.related_suite_keys),
    }


@lru_cache(maxsize=1)
def reviewed_rules() -> dict:
    # Never regenerate this historical human-review evidence from current code.
    contracts = json.loads(REVIEWED_RULES_PATH.read_text(encoding="utf-8"))
    return {selector: {**contract, "resources": _reviewed_resources(contract["resources"])} for selector, contract in contracts.items()}


@dataclass(frozen=True)
class Expectation:
    status: str
    rule_desc: str
    manual_reason: str
    known_gap: str = ""
    confirmed_by: str = ""
    confirmed_on: str = ""

    @property
    def gap_status(self) -> str:
        return self.known_gap.split(" ", 1)[0]


@dataclass(frozen=True)
class OracleFile:
    file_id: str
    path: Path
    filename: str
    role: str
    suite_revision: str
    checker_group: str
    binding_ids: tuple[str, ...]
    expected: dict[str, Expectation]

    def suite(self) -> StandardSuite:
        suite = suite_sources().get(self.suite_revision)
        if suite is None:
            raise ValueError("Unknown corpus Suite revision: " + self.suite_revision)
        if suite.suite_key != self.checker_group:
            raise ValueError(f"Suite revision belongs to {suite.suite_key}, not {self.checker_group}")
        if len(set(self.binding_ids)) != len(self.binding_ids) or set(self.binding_ids) != set(suite.entrypoints):
            raise ValueError("Corpus Binding identities differ from frozen Suite")
        if reviewed_rules().get(self.suite_revision) != reviewed_contract(suite):
            raise ValueError("Corpus rules or bindings changed; independent human review required: " + self.suite_revision)
        return suite

    def rule_body(self) -> dict:
        return json.loads(self.suite().files["rules.json"])


@dataclass(frozen=True)
class OracleCase:
    dataset: str
    case_id: str
    files: tuple[OracleFile, ...]

    @property
    def id(self) -> str:
        return f"{self.dataset}/{self.case_id}"


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        if header is None or tuple(header) != columns:
            raise ValueError(f"{path.name}: required headers {columns}")
        result = []
        for number, row in enumerate(reader, 2):
            if not any(value.strip() for value in row):
                continue
            if len(row) != len(columns):
                raise ValueError(f"{path.name}:{number}: expected {len(columns)} columns")
            if any(value.startswith("=") for value in row):
                raise ValueError(f"{path.name}:{number}: formulas are not oracle facts")
            result.append(dict(zip(columns, (value.strip() for value in row), strict=True)))
    if not result:
        raise ValueError(f"{path.name}: empty oracle")
    return result


def load_oracle(dataset: str | None = None, *, cases: Path = CASES_PATH, oracle: Path = ORACLE_PATH, root: Path = REPO_ROOT) -> list[OracleCase]:
    """Validate the whole table before selecting cases; missing assets cannot skip."""
    files = _rows(cases, FILE_COLUMNS)
    expected = _rows(oracle, EXPECTED_COLUMNS)
    indexed: dict[tuple[str, str, str], dict[str, str]] = {}
    expectations: dict[tuple[str, str, str], dict[str, Expectation]] = {}
    names: set[tuple[str, str, str]] = set()
    for row in files:
        key = (row["数据集"], row["组合ID"], row["文件ID"])
        if not all(row[column] for column in FILE_COLUMNS if column not in {"Suite版本", "Checker组", "Binding"}):
            raise ValueError(f"Incomplete file row: {key}")
        if key in indexed or (key[0], key[1], row["提交文件名"]) in names:
            raise ValueError(f"Duplicate file: {key}")
        if row["角色"] not in {"target", "reference"}:
            raise ValueError(f"Invalid file role: {key}")
        if not (root / row["原件"]).is_file():
            raise ValueError(f"Missing original: {row['原件']}")
        if row["角色"] == "target" and (not row["Checker组"] or not row["Binding"] or _REVISION.fullmatch(row["Suite版本"]) is None):
            raise ValueError(f"Invalid Suite revision: {key}")
        indexed[key] = row
        expectations[key] = {}
        names.add((key[0], key[1], row["提交文件名"]))
    for row in expected:
        key = (row["数据集"], row["组合ID"], row["文件ID"])
        if key not in indexed or indexed[key]["角色"] != "target":
            raise ValueError(f"Expectation has no target: {key}")
        rule_id, status, gap = row["条款ID"], row["人工真值"], row["已知差距"]
        if not rule_id or status not in TRUTHS:
            raise ValueError(f"Invalid expectation: {key} rule={rule_id} status={status!r}")
        if rule_id in expectations[key]:
            raise ValueError(f"Duplicate expectation: {key} rule={rule_id}")
        if gap and (_KNOWN_GAP.fullmatch(gap) is None or gap.split(" ", 1)[0] == status):
            raise ValueError(f"Invalid known gap: {key} rule={rule_id} gap={gap!r}")
        expectations[key][rule_id] = Expectation(status, row["条款说明"], row["人工判定依据"], gap, row["判定人"], row["判定日期"])
    groups: dict[tuple[str, str], list[OracleFile]] = {}
    for key, row in indexed.items():
        if row["角色"] == "target" and not expectations[key]:
            raise ValueError(f"Target has zero assertions: {key}")
        file = OracleFile(key[2], root / row["原件"], row["提交文件名"], row["角色"], row["Suite版本"], row["Checker组"], tuple(row["Binding"].split(";")) if row["Binding"] else (), expectations[key])
        if file.role == "target":
            file.suite()
        groups.setdefault(key[:2], []).append(file)
    if any(not any(file.role == "target" for file in group) for group in groups.values()):
        raise ValueError("Oracle case has no targets")
    return [OracleCase(key[0], key[1], tuple(items)) for key, items in groups.items() if dataset is None or key[0] == dataset]


def original(filename: str, *, cases: Path = CASES_PATH, root: Path = REPO_ROOT) -> Path:
    """Content-addressed path of one submitted file, for regressions that pin a specific original."""
    paths = {row["原件"] for row in _rows(cases, FILE_COLUMNS) if row["提交文件名"] == filename}
    if len(paths) != 1:
        raise ValueError(f"Expected one original named {filename}, found {len(paths)}")
    return root / paths.pop()


def build_round(case: OracleCase) -> tuple[ExecutionSnapshot, dict[str, bytes], dict[str, int]]:
    inputs, targets, blobs, totals = [], [], {}, {}
    for file in case.files:
        data = file.path.read_bytes()
        document_type = ""
        if file.role == "target":
            suite = file.suite()
            body = file.rule_body()
            ids = [str(rule["id"]) for rule in body["rules"]]
            if len(ids) != len(set(ids)) or set(file.expected) - set(ids):
                raise ValueError(f"{case.id}/{file.file_id}: duplicate or missing declared rule IDs")
            totals[file.file_id] = len(ids)
            document_type = suite.document_type
            targets.append(
                FrozenTarget(
                    file.file_id,
                    "sha256:" + suite.revision_digest,
                    suite.suite_key,
                    suite.revision_digest,
                    body,
                    RuleBinding(suite.suite_key, checker_digest(suite.program_files), suite.entrypoints),
                )
            )
        inputs.append(FrozenInput(file.file_id, hashlib.sha256(data).hexdigest(), cast(Literal["target", "reference"], file.role), file.filename, document_type=document_type))
        blobs[file.file_id] = data
    snapshot = ExecutionSnapshot("corpus", "corpus-controlled-runner", tuple(inputs), tuple(targets), "corpus-source", library_revision(), {}, round_id=case.id)
    return snapshot, blobs, totals


def programs_for(snapshot: ExecutionSnapshot) -> dict[str, dict[str, bytes]]:
    sources = {"sha256:" + suite.revision_digest: suite for suite in suite_sources().values()}
    return {target.binding.checker_digest: sources[target.suite_revision_id].program_files for target in snapshot.targets}
