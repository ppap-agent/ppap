"""One table contract for corpus inputs and independently adjudicated outcomes."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import StandardSuite, library_revision, load_predefined_checkers, predefined_directory_suites

REPO_ROOT = Path(__file__).resolve().parents[5]
CORPUS = REPO_ROOT / "assets/corpus"
# Each customer keeps its own cases, human truth and reviewed rules; materials never mix.
DATASETS = ("li", "nio", "uaes")
FILE_COLUMNS = ("数据集", "组合ID", "文件ID", "原件", "提交文件名", "角色", "Suite版本", "Checker组", "Binding")
EXPECTED_COLUMNS = ("数据集", "组合ID", "文件ID", "条款ID", "人工真值", "条款说明", "人工判定依据", "判定人", "判定日期", "已知差距")
TRUTHS = frozenset({"PASS", "NG", "UNABLE", "NA"})
# What the product still does differently from the truth, and why.
_KNOWN_GAP = re.compile(r"^(PASS|NG|UNABLE|NA|ERR) \S.*$")
# A Suite is named by its checker leaf path, e.g. ``li/L08``.
_REVISION = re.compile(r"^[^/@\s]+/[^/@\s]+$")


def table(dataset: str, kind: str, *, corpus: Path = CORPUS) -> Path:
    """One customer's table: kind is cases.csv, oracle.csv or reviewed-rules.json."""
    if dataset not in DATASETS or kind not in {"cases.csv", "oracle.csv", "reviewed-rules.json"}:
        raise ValueError(f"Unknown corpus table: {dataset}-{kind}")
    return corpus / f"{dataset}-{kind}"


# A rule revision the product has moved on from keeps the checker leaves and presets it was judged with
# (assets/<customer>/rulebook/<version>/<revision>/checkers and presets.json). Human truths and model recordings belong
# to the frozen revision they were made under, so the corpus runs that revision rather than the current one.
FROZEN_REVISIONS = REPO_ROOT / "assets"


@lru_cache(maxsize=1)
def _judged_sources() -> tuple[dict[str, StandardSuite], dict[str, Path]]:
    """Per leaf path, the Suite the reviewed rules were judged under, and the frozen revision it came from."""
    current = {suite.source_path: suite for suite in predefined_directory_suites()}
    reviewed = reviewed_rules()
    chosen, origin = dict(current), {}
    for revision in sorted(path.parent for path in FROZEN_REVISIONS.glob("*/rulebook/*/*/checkers")):
        suites = load_predefined_checkers(revision / "checkers")

        # A revision the reviewed rules were judged under, while the product has moved on: the corpus runs all of it.
        def judged_here(suite: StandardSuite) -> bool:
            path = suite.source_path
            moved_on = path not in current or reviewed_contract(current[path]) != reviewed.get(path)
            return path in reviewed and reviewed[path] == reviewed_contract(suite) and moved_on

        judged = any(judged_here(suite) for suite in suites)
        for suite in suites if judged else ():
            chosen[suite.source_path], origin[suite.source_path] = suite, revision
    return chosen, origin


def suite_sources() -> dict[str, StandardSuite]:
    """Exact leaf paths, never a current/latest Suite lookup: the revision each leaf's human truths were judged under."""
    return dict(_judged_sources()[0])


@contextmanager
def presets_as_judged(customer: str) -> Iterator[None]:
    """Recognition reads the customer's presets of the frozen revision its truths and recordings were made under."""
    import audit.presets as presets

    customer_dir = {suite.customer: path.split("/", 1)[0] for path, suite in _judged_sources()[0].items()}.get(customer)
    revisions = {revision for path, revision in _judged_sources()[1].items() if path.split("/", 1)[0] == customer_dir}
    if not revisions:
        yield
        return
    if len(revisions) != 1:
        raise ValueError(f"{customer}: truths judged under several frozen revisions")
    rows = json.loads((revisions.pop() / "presets.json").read_text(encoding="utf-8"))
    frozen = tuple(presets.CustomerPreset(**{**row, "clauses": tuple(presets.PresetClause(**clause) for clause in row["clauses"])}) for row in rows)
    current = presets.customer_presets
    judged = (*frozen, *(preset for preset in current() if preset.customer != customer))
    presets.customer_presets = lambda: judged  # type: ignore[assignment]
    try:
        yield
    finally:
        presets.customer_presets = current  # type: ignore[assignment]


def reviewed_contract(suite: StandardSuite) -> dict:
    """Meaning of existing human answers, distinct from executable identity.

    A clause's default severity is left out: it grades an NG and never changes PASS/NG (PRD §2.11 五), so the
    reviewed human answers keep their meaning when a level is set.
    """
    rules = json.loads(suite.files["rules.json"])
    for item in rules.get("rules", []):
        item.pop("severity", None)
    return {
        "document_type": suite.document_type,
        "rules": rules,
        "appendix": json.loads(suite.files.get("appendix.json", b"[]")),
        "entrypoints": dict(suite.entrypoints),
        "related_suite_keys": sorted(suite.related_suite_keys),
    }


@lru_cache(maxsize=1)
def reviewed_rules() -> dict:
    # Never regenerate this historical human-review evidence from current code.
    merged: dict = {}
    for dataset in DATASETS:
        reviewed = json.loads(table(dataset, "reviewed-rules.json").read_text(encoding="utf-8"))
        if any(not key.startswith(dataset + "/") for key in reviewed):
            raise ValueError(f"{dataset}-reviewed-rules.json holds another customer's Suite")
        merged.update(reviewed)
    return merged


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


def load_oracle(dataset: str | None = None, *, cases: Path | None = None, oracle: Path | None = None, root: Path = REPO_ROOT) -> list[OracleCase]:
    """One customer's tables, or each customer's in turn; explicit tables are loaded as given."""
    if cases is not None or oracle is not None:
        if cases is None or oracle is None:
            raise ValueError("Explicit corpus tables come in pairs")
        return [case for case in _load_table(cases, oracle, root) if dataset is None or case.dataset == dataset]
    result = []
    for name in (dataset,) if dataset is not None else DATASETS:
        loaded = _load_table(table(name, "cases.csv"), table(name, "oracle.csv"), root)
        if any(case.dataset != name for case in loaded):
            raise ValueError(f"{name} tables hold another customer's cases")
        result += loaded
    return result


def _load_table(cases: Path, oracle: Path, root: Path) -> list[OracleCase]:
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
    return [OracleCase(key[0], key[1], tuple(items)) for key, items in groups.items()]


def case_rows(dataset: str | None = None) -> list[dict[str, str]]:
    """File rows of one customer's case table, or of every customer's."""
    return [row for name in ((dataset,) if dataset else DATASETS) for row in _rows(table(name, "cases.csv"), FILE_COLUMNS)]


def original(filename: str, *, cases: Path | None = None, root: Path = REPO_ROOT) -> Path:
    """Content-addressed path of one submitted file, for regressions that pin a specific original."""
    rows = _rows(cases, FILE_COLUMNS) if cases is not None else case_rows()
    paths = {row["原件"] for row in rows if row["提交文件名"] == filename}
    if len(paths) != 1:
        raise ValueError(f"Expected one original named {filename}, found {len(paths)}")
    return root / paths.pop()


def customer_rules(customer: str) -> list[dict]:
    """Every preset of one customer, as a task offers them for recognition."""
    from audit.presets import customer_presets, preset_document_type

    return [
        {"id": preset.entry_id, "title": preset.title, "kind": "engine", "type": preset_document_type(preset), "customers": [customer], "rule_profile": "v4"}
        for preset in customer_presets()
        if preset.customer == customer
    ]


def case_customer(case: OracleCase) -> str:
    customers = {file.checker_group.split(":", 1)[0] for file in case.files if file.role == "target"}
    if len(customers) != 1:
        raise ValueError(f"{case.id}: targets must share one customer")
    return customers.pop()


def recognize_references(case: OracleCase) -> dict[str, dict]:
    """Recognize each reference with the product's upload pipeline, never from the table."""
    from worker.intake.file_analysis import analyze_file

    customer = case_customer(case)
    with presets_as_judged(customer):
        rules = customer_rules(customer)
        return {
            file.file_id: analyze_file(file.filename, file.path.read_bytes(), rules, customer_preference=customer, scoped=True, reference_only=True) for file in case.files if file.role == "reference"
        }


def build_round(case: OracleCase, recognized: dict[str, dict] | None = None) -> tuple[ExecutionSnapshot, dict[str, bytes], dict[str, int]]:
    """`recognized` gives references their pipeline type and facts, as an uploaded task freezes them."""
    inputs, targets, blobs, totals = [], [], {}, {}
    for file in case.files:
        data = file.path.read_bytes()
        document_type = ""
        metadata: dict = {}
        if file.role == "reference" and recognized is not None:
            document_type = recognized[file.file_id].get("document_type") or ""
            metadata = recognized[file.file_id].get("metadata") or {}
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
        inputs.append(
            FrozenInput(
                file.file_id,
                hashlib.sha256(data).hexdigest(),
                cast(Literal["target", "reference"], file.role),
                file.filename,
                document_type=document_type,
                customer=metadata.get("customer"),
                model=metadata.get("model"),
                part=metadata.get("part"),
            )
        )
        blobs[file.file_id] = data
    snapshot = ExecutionSnapshot("corpus", "corpus-controlled-runner", tuple(inputs), tuple(targets), "corpus-source", library_revision(), {}, round_id=case.id)
    return snapshot, blobs, totals


def programs_for(snapshot: ExecutionSnapshot) -> dict[str, dict[str, bytes]]:
    sources = {"sha256:" + suite.revision_digest: suite for suite in suite_sources().values()}
    return {target.binding.checker_digest: sources[target.suite_revision_id].program_files for target in snapshot.targets}
