"""PSW object scope and quantifiers over established, source-located facts."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import fitz

from audit.contracts import AuditInput, AuditResultItem

from .ocr_visual_facts import observe_ocr_scopes
from .psw_evidence_trace import psw_fact_summary
from .psw_visual_evidence import PSW_RULE_FIELDS, evaluate_psw_observation

logger = logging.getLogger(__name__)
REFERENCE = "psw_reference_content"
_REFERENCE_DESCRIPTION = (
    "此页是否明确作为样例、模板展示或填写说明，而非本次提交的业务表。"
    "依据用途标记和页面构成判断，例如示范表上叠加的讲解批注；不能因缺少签字、印章、零件号或其他必填值而认定样例。"
    "真实提交即使未填写完整也为false；真实业务与说明混排时不能将整页排除。用途无法确认返回null。"
)
_REFERENCE_HEADING = re.compile(r"^(?:EXAMPLE|SAMPLE|样例|示例|样张|填写说明|填表说明)(?:\s*[:：].*)?$", re.IGNORECASE)
_GUIDANCE = re.compile(r"填写说明|填表说明|填写为|必须手签|样例页|示例页|\b(?:EXAMPLE|SAMPLE)\b", re.IGNORECASE)
_DISPOSITION = frozenset({"disposition_visible", "approved_checked", "conditional_checked", "rejected_checked"})
_STAMP = frozenset({"psw_stamp_present", "psw_stamp_ending_text"})


def _reference_role(text: str) -> bool | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and _REFERENCE_HEADING.fullmatch(lines[0]):
        return True
    return None if _GUIDANCE.search(text) else False


def _all_results(rule_no: int, results: list[AuditResultItem], scope_known: bool) -> AuditResultItem:
    """A known counterexample defeats ALL; an empty domain never proves PASS."""
    for result in results:
        if result.status == "NG":
            return result
    base = evaluate_psw_observation(rule_no, {})
    if not scope_known:
        return base.model_copy(update={"suggestions": "业务对象范围尚不能确认，请核对样例用途或表单边界。"})
    if any(result.status == "UNABLE" for result in results):
        return base
    if not results:
        return base.model_copy(update={"status": "NG", "suggestions": "未找到实际提交的业务保证书，样例和说明不能代替提交。", "checks_performed": 1})
    return results[0]


def _exists_authorization(facts: list[dict[str, object]], scope_known: bool) -> tuple[bool | None, bool | None]:
    """Existence and its seal must refer to the same authorization object."""

    def condition(item, fields):
        role = item.get(REFERENCE, False)
        values = [None if role is None else role is False, *(item.get(field) for field in fields)]
        return False if any(value is False for value in values) else True if all(value is True for value in values) else None

    tables = [condition(item, ("authorization_table_present",)) for item in facts]
    valid = [condition(item, ("authorization_table_present", "authorization_company_stamp_present")) for item in facts]

    def exists(values):
        if any(value is True for value in values):
            return True
        return False if scope_known and all(value is False for value in values) else None

    return exists(tables), exists(valid)


class _Objects:
    """One execution-local projection; original files and business rules stay fixed."""

    def __init__(self, audit: AuditInput, digest: str, cache: dict):
        from audit.checkers.v4.visual_facts import VisualFactsUnavailable, _native_disposition_texts
        from audit.engine.checkers.psw_checkbox_evidence import read_disposition_facts
        from audit.pdf_tables import valid_pdf_bbox

        self.audit, self.digest, self.cache = audit, digest, cache
        self.warrants: list[int] = []
        self.form_pages: set[int] = set()
        self.authorization: list[int] = []
        self.roles: dict[int, bool | None] = {}
        self.facts: dict[int, dict[str, object]] = {}
        self.sources: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.ambiguous: set[int] = set()
        with fitz.open(audit.file_url) as document:
            if document.needs_pass:
                raise VisualFactsUnavailable("原件访问权限尚不能确认。")
            self.pages = list(range(1, len(document) + 1))
            for number in self.pages:
                page = document[number - 1]
                self.roles[number] = _reference_role(page.get_text())
                self.facts[number] = {}
                signatures, dispositions = page.search_for("经授权的供方代表签字"), page.search_for("认可情况")
                if signatures or dispositions:
                    self.warrants.append(number)
                    if page.search_for("零件提交保证书") or page.search_for("Part Submission Warrant"):
                        self.form_pages.add(number)
                    if len(signatures) > 1 or len(dispositions) > 1:
                        self.ambiguous.add(number)
                    native = read_disposition_facts(
                        [item for item in _native_disposition_texts(page) if valid_pdf_bbox(item["pdf_bbox"], page.rect.width, page.rect.height)],
                        self._note,
                        page=number,
                        source_sha256=digest,
                        visual=False,
                    )
                    self.facts[number].update({field: value for field, value in native.items() if value is not None})
                if page.search_for("部门章授权声明"):
                    self.authorization.append(number)
                if self.roles[number] is True:
                    self._note(
                        {
                            "kind": "pdf_region",
                            "page": number,
                            "bbox": list(page.rect),
                            "source_sha256": digest,
                            "file_version_id": audit.audit_id,
                            "coordinate_system": "pdf_points_unrotated",
                            "fact": REFERENCE,
                            "observed_value": True,
                        },
                        "明确的样例或说明页标题",
                        visual=False,
                    )
            if not self.warrants and len(document) == 1:
                self.warrants = [1]

    def _note(self, locator, excerpt, **kwargs):
        self.sources.append((({**locator, "file_version_id": self.audit.audit_id}, excerpt), kwargs))

    def read(self, pages: list[int], fields: dict[str, str]) -> None:
        from audit.checkers.v4.visual_facts import VisualFactsUnavailable, _pdf_images

        selected: dict[str, list[str]] = {}
        for number in pages:
            if self.roles[number] is True or number in self.ambiguous:
                continue
            for field in fields:
                if field not in self.facts[number]:
                    selected.setdefault(field, []).append(f"pdf:{number}")
            if self.roles[number] is None and REFERENCE not in self.facts[number]:
                selected.setdefault(REFERENCE, []).append(f"pdf:{number}")
        if not selected:
            return
        needed = {field: _REFERENCE_DESCRIPTION if field == REFERENCE else fields[field] for field in selected}
        numbers = sorted({int(scope.split(":")[1]) for scopes in selected.values() for scope in scopes})
        if len(numbers) > 10:
            raise VisualFactsUnavailable("本次所需原页超出有界视觉读取范围。")
        images, context = _pdf_images(self.audit, numbers, self.digest, needed)
        sources = []
        observed = observe_ocr_scopes(images, needed, lambda *a, **kw: sources.append((a, kw)), field_scopes=selected, context_by_scope=context, request_cache=self.cache)
        for scope, values in observed.items():
            number = int(scope.split(":")[1])
            self.facts[number].update(values)
            if REFERENCE in values:
                role = values[REFERENCE]
                self.roles[number] = role if isinstance(role, bool) else None
        self.sources.extend(source for source in sources if source not in self.sources)

    def outcomes(self, rule_no: int, authorization=None):
        outcomes = []
        for number in self.warrants:
            if self.roles[number] is not False or number in self.ambiguous:
                continue
            facts = dict(self.facts[number])
            if authorization is not None:
                facts.update(authorization_table_present=authorization[0], authorization_company_stamp_present=authorization[1])
            outcomes.append((number, evaluate_psw_observation(rule_no, facts)))
        return outcomes

    def evaluate(self, rule_no: int, witness) -> AuditResultItem:
        required = PSW_RULE_FIELDS[rule_no]
        if _STAMP.intersection(required):
            first = {field: PSW_RULE_FIELDS[3][field] for field in PSW_RULE_FIELDS[3] if field in _STAMP}
        elif _DISPOSITION.intersection(required):
            first = {field: PSW_RULE_FIELDS[8][field] for field in PSW_RULE_FIELDS[8] if field in _DISPOSITION}
        else:
            first = {field: text for field, text in required.items() if not field.startswith("authorization_")}
        self.read(self.warrants, first)
        known = all(self.roles[page] is not None and page not in self.ambiguous for page in self.warrants)
        if not self.warrants and not all(role is True for role in self.roles.values()):
            known = False
        outcomes = self.outcomes(rule_no)
        result = _all_results(rule_no, [value for _, value in outcomes], known)
        if result.status == "UNABLE":
            # The company-ending boolean is derived by the existing policy.
            # Asking it again cannot repair unknown canonical seal text.
            remaining = {field: text for field, text in required.items() if not field.startswith("authorization_") and field not in first and field != "psw_stamp_ends_with_company"}
            self.read(self.warrants, remaining)
            outcomes = self.outcomes(rule_no)
            result = _all_results(rule_no, [value for _, value in outcomes], known)
        if rule_no == 3 and result.status == "UNABLE":
            pages = self.authorization or self.pages
            fields = {field: text for field, text in required.items() if field.startswith("authorization_")}
            self.read(pages, fields)
            auth = _exists_authorization([{**self.facts[page], REFERENCE: self.roles[page]} for page in pages], not self.ambiguous.intersection(pages))
            outcomes = self.outcomes(rule_no, auth)
            result = _all_results(rule_no, [value for _, value in outcomes], known)
        for args, kwargs in self.sources:
            locator = args[0]
            if locator.get("fact") == REFERENCE or self.roles.get(locator.get("page")) is False:
                witness(*args, **kwargs)
        diagnostic = {
            "source_sha256": self.digest,
            "rule": rule_no,
            "status": result.status,
            "business_pages": [page for page in self.warrants if self.roles[page] is False and page not in self.ambiguous],
            "reference_pages": [page for page in self.pages if self.roles[page] is True],
            "unresolved_pages": [page for page in self.warrants if self.roles[page] is None or page in self.ambiguous],
            "objects": [{"page": page, "status": value.status, "facts": psw_fact_summary(self.facts[page])} for page, value in outcomes],
        }
        logger.warning("PSW scope decision: %s", json.dumps(diagnostic, sort_keys=True, separators=(",", ":")))
        return result


def evaluate_psw_pdf_rule(audit: AuditInput, rule_no: int, witness, cache: dict, read_single: Callable[[], dict[str, object]]) -> tuple[dict[str, object], AuditResultItem]:
    """Keep single-object reads; plan scope explicitly when references or multiple forms occur."""
    from audit.checkers.v4.visual_facts import _source_identity

    path = Path(audit.file_url)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        facts = read_single()
        return facts, evaluate_psw_observation(rule_no, facts)
    digest = _source_identity(audit, cache)
    key = ("psw_objects", audit.audit_id, digest)
    if key not in cache:
        cache[key] = _Objects(audit, digest, cache)
    objects = cache[key]
    multiple_forms = len(objects.warrants) > 1 and objects.form_pages.issuperset(objects.warrants)
    # Repeated sections alone do not establish separate business objects.
    # The existing one-document reader preserves uncertainty between them.
    if not multiple_forms and len(objects.authorization) <= 1 and not any(role is not False for role in objects.roles.values()) and not objects.ambiguous:
        facts = read_single()
        return facts, evaluate_psw_observation(rule_no, facts)
    from documents.ocr.env import ocr_policy_identity

    identity = ("ocr_policy", audit.audit_id)
    policy = ocr_policy_identity()
    if identity in cache and cache[identity] != policy:
        raise ValueError("OCR configuration changed during the audit")
    cache[identity] = policy
    # A section fragment is not an independently established business form.
    # Reference exclusion must not turn such fragments into ALL objects.
    if len(objects.warrants) > 1:
        objects.ambiguous.update(set(objects.warrants) - objects.form_pages)
    return {}, objects.evaluate(rule_no, witness)
