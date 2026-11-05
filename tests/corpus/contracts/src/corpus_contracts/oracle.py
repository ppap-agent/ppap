"""One table contract for corpus inputs and independently adjudicated outcomes."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from audit.audits import RoundInput, RoundWork
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from audit.engine.routing import resolve_engine_agent_class
from audit.kinds import PpapDocumentType
from audit.presets import preset_document_type, preset_rule, preset_rule_body

from corpus_contracts.product_round import engine_rule_body

REPO_ROOT = Path(__file__).resolve().parents[5]
CASES_PATH = REPO_ROOT / "assets/corpus/cases.csv"
ORACLE_PATH = REPO_ROOT / "assets/corpus/oracle.csv"
DATASETS = ("v2", "v3", "v4-uaes")
FILE_COLUMNS = ("数据集", "组合ID", "文件ID", "原件", "提交文件名", "角色", "路由", "Checker组")
EXPECTED_COLUMNS = ("数据集", "组合ID", "文件ID", "条款ID", "人工真值", "条款说明", "人工判定依据", "判定人", "判定日期", "已知差距")
TRUTHS = frozenset({"PASS", "NG", "UNABLE", "NA"})
# What the product still does differently from the truth, and why.
_KNOWN_GAP = re.compile(r"^(PASS|NG|UNABLE|NA|ERR) \S.*$")
_ROUTE = re.compile(r"^(builtin:v[23]:|preset:[^:]+:)[^:]+$")


@dataclass(frozen=True)
class Expectation:
    status: str
    rule_desc: str
    manual_reason: str
    known_gap: str = ""

    @property
    def gap_status(self) -> str:
        return self.known_gap.split(" ", 1)[0]


@dataclass(frozen=True)
class OracleFile:
    file_id: str
    path: Path
    filename: str
    role: str
    route: str
    checker_group: str
    expected: dict[str, Expectation]

    def rule_body(self) -> dict:
        kind, first, second = self.route.split(":", 2)
        return preset_rule_body(first, second) if kind == "preset" else engine_rule_body(second, self.filename, first)[0]

    def resolved_checker_group(self) -> str:
        kind, first, second = self.route.split(":", 2)
        if kind == "preset":
            preset = preset_rule(first, second)
            agent = resolve_engine_agent_class(PpapDocumentType.from_string(preset_document_type(preset)), self.filename, "v4", preset_key=second, preset_customer=first)
        else:
            agent = resolve_engine_agent_class(PpapDocumentType.from_string(second), self.filename, first)
        return f"{agent.__module__.removeprefix('audit.checkers.')}.{agent.__name__}" if agent else ""


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
        if not all(row[column] for column in FILE_COLUMNS if column not in {"路由", "Checker组"}):
            raise ValueError(f"Incomplete file row: {key}")
        if key in indexed or (key[0], key[1], row["提交文件名"]) in names:
            raise ValueError(f"Duplicate file: {key}")
        if row["角色"] not in {"target", "reference"}:
            raise ValueError(f"Invalid file role: {key}")
        if not (root / row["原件"]).is_file():
            raise ValueError(f"Missing original: {row['原件']}")
        if row["角色"] == "target" and (not row["Checker组"] or _ROUTE.fullmatch(row["路由"]) is None):
            raise ValueError(f"Invalid route: {key}")
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
        expectations[key][rule_id] = Expectation(status, row["条款说明"], row["人工判定依据"], gap)
    groups: dict[tuple[str, str], list[OracleFile]] = {}
    for key, row in indexed.items():
        if row["角色"] == "target" and not expectations[key]:
            raise ValueError(f"Target has zero assertions: {key}")
        groups.setdefault(key[:2], []).append(OracleFile(key[2], root / row["原件"], row["提交文件名"], row["角色"], row["路由"], row["Checker组"], expectations[key]))
    if any(not any(file.role == "target" for file in group) for group in groups.values()):
        raise ValueError("Oracle case has no targets")
    return [OracleCase(key[0], key[1], tuple(items)) for key, items in groups.items() if dataset is None or key[0] == dataset]


def original(filename: str, *, cases: Path = CASES_PATH, root: Path = REPO_ROOT) -> Path:
    """Content-addressed path of one submitted file, for regressions that pin a specific original."""
    paths = {row["原件"] for row in _rows(cases, FILE_COLUMNS) if row["提交文件名"] == filename}
    if len(paths) != 1:
        raise ValueError(f"Expected one original named {filename}, found {len(paths)}")
    return root / paths.pop()


def build_round(case: OracleCase) -> tuple[RoundWork, dict[str, bytes], dict[str, int]]:
    inputs, blobs, totals = [], {}, {}
    for file in case.files:
        body, execution = None, None
        if file.role == "target":
            if file.resolved_checker_group() != file.checker_group:
                raise ValueError(f"{case.id}/{file.file_id}: routed to {file.resolved_checker_group()}, not {file.checker_group}")
            body = file.rule_body()
            execution = freeze_target_rule_execution(body, file.filename)
            ids = [str(rule["id"]) for rule in body["rules"]]
            if len(ids) != len(set(ids)) or set(file.expected) - set(ids):
                raise ValueError(f"{case.id}/{file.file_id}: duplicate or missing declared rule IDs")
            totals[file.file_id] = len(ids)
        inputs.append(RoundInput(file.role, file.file_id, file.file_id, file.filename, "ready", rule_version_id=file.route or None, rule_body=body, rule_execution=execution))
        blobs[file.file_id] = file.path.read_bytes()
    return RoundWork(case.id, "corpus", inputs), blobs, totals
