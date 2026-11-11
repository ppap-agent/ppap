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
from .psw_visual_evidence import PSW_RULE_FIELDS, evaluate_psw_observation, normalize_psw_stamp_facts

logger = logging.getLogger(__name__)
REFERENCE = "psw_reference_content"
_REFERENCE_DESCRIPTION = (
    "页面用途：true表示仅用作样例、模板展示或填写指导，false表示本次实际提交的业务表，不是问是否存在业务字段。"
    "依据用途标记和整页构成判断，不要求出现EXAMPLE字样；例如背景表样上叠加多处讲解批注，是填写指导的正向证据。"
    "不能因缺少签字、印章、零件号或其他必填值而认定样例，也不能将空白字段的false用于此用途字段。"
    "真实提交即使未填写完整也为false；真实业务与说明混排时不能将整页排除。用途无法确认返回null。"
)
_REFERENCE_HEADING = re.compile(r"^(?:EXAMPLE|SAMPLE|样例|示例|样张|填写说明|填表说明)(?:\s*[:：].*)?$", re.IGNORECASE)
_GUIDANCE = re.compile(r"填写说明|填表说明|填写为|必须手签|样例页|示例页|\b(?:EXAMPLE|SAMPLE)\b", re.IGNORECASE)
_DISPOSITION = frozenset({"disposition_visible", "approved_checked", "conditional_checked", "rejected_checked"})
_STAMP = frozenset({"psw_stamp_present", "psw_stamp_ends_with_company"})


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
        return base.model_copy(update={"suggestions": "无法判断：业务对象范围尚不能确认，请核对样例用途或表单边界。"})
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
        from audit.engine.checkers.pdf_field_region import pdf_field_region
        from audit.engine.checkers.psw_checkbox_evidence import read_disposition_facts
        from audit.engine.checkers.visual_facts import VisualFactsUnavailable, _native_disposition_texts, _region_may_hold_stamp
        from audit.pdf_tables import valid_pdf_bbox

        self.audit, self.digest, self.cache = audit, digest, cache
        self.warrants: list[int] = []
        self.form_pages: set[int] = set()
        self.disposition_forms: set[int] = set()
        self.authorization: list[int] = []
        self.authorization_ambiguous: set[int] = set()
        self.roles: dict[int, bool | None] = {}
        self.facts: dict[int, dict[str, object]] = {}
        self.failed_reads: dict[tuple[int, str], Exception] = {}
        self.sources: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.ambiguous: set[int] = set()
        self.unresolved_pages: set[int] = set()
        with fitz.open(audit.file_url) as document:
            if document.needs_pass:
                raise VisualFactsUnavailable("原件访问权限尚不能确认。")
            self.pages = list(range(1, len(document) + 1))
            for number in self.pages:
                page = document[number - 1]
                page.set_rotation(0)
                text = page.get_text()
                self.roles[number] = _reference_role(text)
                self.facts[number] = {}
                signatures, dispositions = page.search_for("经授权的供方代表签字"), page.search_for("认可情况")
                if signatures or dispositions:
                    self.warrants.append(number)
                    if page.search_for("零件提交保证书") or page.search_for("Part Submission Warrant"):
                        if signatures and dispositions:
                            self.form_pages.add(number)
                        if dispositions:
                            self.disposition_forms.add(number)
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
                authorizations = page.search_for("部门章授权声明")
                if authorizations:
                    self.authorization.append(number)
                    if len(authorizations) > 1:
                        self.authorization_ambiguous.add(number)
                    elif len(page.search_for("公司名称")) == 1:
                        page.set_rotation(0)
                        region = pdf_field_region(page, {"authorization_company_stamp_present"})
                        if region != page.rect and valid_pdf_bbox(list(region), page.rect.width, page.rect.height) and not _region_may_hold_stamp(page, region):
                            self.facts[number]["authorization_company_stamp_present"] = False
                            self._note(
                                {
                                    "kind": "pdf_region",
                                    "page": number,
                                    "bbox": list(region),
                                    "coordinate_system": "pdf_points_unrotated",
                                    "source_sha256": digest,
                                    "element": "authorization_stamp_region",
                                    "fact": "authorization_company_stamp_present",
                                    "observed_value": False,
                                },
                                "完整公司章区域未包含图形印章对象",
                                visual=False,
                            )
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                compact = re.sub(r"\s+", "", text)
                checklist = bool(lines and lines[0] == "生产件批准交付物文件清单" and all(label in compact for label in ("序号", "项目", "确认方法", "交付形式", "附件")))
                non_submission = bool(lines and lines[0] == "PPAP文件无法提交声明" and all(label in compact for label in ("序号", "文件类型", "无法提交", "现场查阅审核")))
                if (checklist or non_submission) and not signatures and not dispositions and not authorizations:
                    self._note(
                        {
                            "kind": "pdf_region",
                            "page": number,
                            "bbox": list(page.search_for(lines[0])[0]),
                            "coordinate_system": "pdf_points_unrotated",
                            "source_sha256": digest,
                            "coverage": "native_document_heading",
                        },
                        f"原生标题及正文结构确认本页为{lines[0]}。",
                        visual=False,
                    )
                elif not signatures and not dispositions and not authorizations and self.roles[number] is not True and _region_may_hold_stamp(page, page.rect):
                    # An unclassified visual page may contain another warrant
                    # or authorization. Do not add scanning or exclude it.
                    self.unresolved_pages.add(number)
                    self.ambiguous.add(number)
                    self.roles[number] = None
                    self.facts[number][REFERENCE] = None
                    self._note(
                        {
                            "kind": "pdf_region",
                            "page": number,
                            "bbox": list(page.rect),
                            "source_sha256": digest,
                            "file_version_id": audit.audit_id,
                            "fact": REFERENCE,
                            "observed_value": None,
                            "coverage": "unreadable",
                        },
                        "此页有未分类图形内容，不能排除额外保证书或授权声明。",
                        visual=False,
                    )
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
        from audit.engine.checkers.visual_facts import VisualFactsUnavailable, _pdf_images

        selected: dict[str, list[str]] = {}
        for number in pages:
            if self.roles[number] is True:
                continue
            if number not in self.ambiguous:
                for field in fields:
                    if (number, field) in self.failed_reads:
                        raise self.failed_reads[number, field]
                    if field not in self.facts[number] and not (field.startswith("authorization_") and number in self.authorization_ambiguous):
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
        try:
            observed = observe_ocr_scopes(images, needed, lambda *a, **kw: sources.append((a, kw)), field_scopes=selected, context_by_scope=context, request_cache=self.cache)
        except Exception as exc:
            for field, scopes in selected.items():
                for scope in scopes:
                    self.failed_reads[int(scope.split(":")[1]), field] = exc
            raise
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
            if self.roles[number] is True or number in self.ambiguous:
                continue
            facts = dict(self.facts[number])
            if authorization is not None:
                facts.update(authorization_table_present=authorization[0], authorization_company_stamp_present=authorization[1])
            outcomes.append((number, evaluate_psw_observation(rule_no, facts)))
        return outcomes

    def aggregate(self, rule_no: int, authorization=None):
        outcomes = self.outcomes(rule_no, authorization)
        actual = [page for page in self.warrants if self.roles[page] is not True]
        if len(actual) > 1 and not self.independent(rule_no, actual):
            combined = self.combined(actual)
            if authorization is not None:
                combined.update(authorization_table_present=authorization[0], authorization_company_stamp_present=authorization[1])
            result = evaluate_psw_observation(rule_no, combined)
            known = not self.unresolved_pages and all(self.roles[page] is False for page in actual)
            return _all_results(rule_no, [result] if known else [], known), outcomes
        business = [value for page, value in outcomes if self.roles[page] is False]
        # ALL is unaffected by an uncertain member whose predicate is true.
        # Such a member cannot establish that any business object exists.
        known = not self.unresolved_pages and not any(self.roles[page] is not True and page in self.ambiguous for page in self.warrants)
        known &= all(value.status == "PASS" for page, value in outcomes if self.roles[page] is None)
        if not business and not all(role is True for role in self.roles.values()):
            known = False
        return _all_results(rule_no, business, known), outcomes

    def combined(self, pages):
        """Repeated sections without distinct form identity need common facts."""
        fields = set().union(*(self.facts[page] for page in pages))
        result = {}
        for field in fields:
            values = [self.facts[page].get(field) for page in pages]
            result[field] = values[0] if all(value == values[0] for value in values) else None
        return result

    def independent(self, rule_no, pages):
        return (self.disposition_forms if rule_no in {8, 9, 10} else self.form_pages).issuperset(pages)

    def evaluate(self, rule_no: int, witness) -> AuditResultItem:
        required = PSW_RULE_FIELDS[rule_no]
        if _STAMP.intersection(required):
            first = {field: description for field, description in required.items() if field in _STAMP}
        elif _DISPOSITION.intersection(required):
            first = {field: PSW_RULE_FIELDS[8][field] for field in PSW_RULE_FIELDS[8] if field in _DISPOSITION}
        else:
            first = {field: text for field, text in required.items() if not field.startswith("authorization_")}
        self.read(self.warrants, first)
        result, outcomes = self.aggregate(rule_no)
        if result.status == "UNABLE":
            remaining = {field: text for field, text in required.items() if not field.startswith("authorization_") and field not in first}
            pages = self.warrants
            if rule_no in {8, 9, 10}:
                branch = "approved_checked" if rule_no == 8 else "conditional_checked"
                pages = [
                    page
                    for page in pages
                    if self.facts[page].get("disposition_visible") is True
                    and self.facts[page].get(branch) is True
                    and all(self.facts[page].get(field) is False for field in ("approved_checked", "conditional_checked", "rejected_checked") if field != branch)
                ]
                actual = [page for page in self.warrants if self.roles[page] is not True]
                if len(actual) > 1 and not self.independent(rule_no, actual):
                    combined = self.combined(actual)
                    pages = (
                        actual
                        if combined.get("disposition_visible") is True
                        and combined.get(branch) is True
                        and all(combined.get(field) is False for field in ("approved_checked", "conditional_checked", "rejected_checked") if field != branch)
                        else []
                    )
                if rule_no in {9, 10}:
                    remaining = {field: PSW_RULE_FIELDS[number][field] for number, field in ((9, "approval_date"), (10, "comments_present"))}
            self.read(pages, remaining)
            result, outcomes = self.aggregate(rule_no)
        if (
            rule_no == 3
            and result.status == "UNABLE"
            and any(normalize_psw_stamp_facts(self.facts[page]).get("psw_stamp_ends_with_company") is False for page, value in outcomes if value.status == "UNABLE")
        ):
            pages = self.authorization or self.pages
            fields = {field: text for field, text in required.items() if field.startswith("authorization_")}
            auth = _exists_authorization(
                [{**self.facts[page], REFERENCE: self.roles[page]} for page in pages],
                not self.unresolved_pages and not any(page in self.ambiguous | self.authorization_ambiguous and self.roles[page] is not True for page in pages),
            )
            result, outcomes = self.aggregate(rule_no, auth)
            if result.status == "UNABLE":
                self.read(pages, fields)
                auth = _exists_authorization(
                    [{**self.facts[page], REFERENCE: self.roles[page]} for page in pages],
                    not self.unresolved_pages and not any(page in self.ambiguous | self.authorization_ambiguous and self.roles[page] is not True for page in pages),
                )
                result, outcomes = self.aggregate(rule_no, auth)
        nondecisive = {page for page, value in outcomes if self.roles[page] is None and value.status == "PASS"}
        for args, kwargs in self.sources:
            locator = args[0]
            if locator.get("fact") == REFERENCE or self.roles.get(locator.get("page")) is False or (result.status == "PASS" and locator.get("page") in nondecisive):
                witness(*args, **kwargs)
        diagnostic = {
            "source_sha256": self.digest,
            "rule": rule_no,
            "status": result.status,
            "business_pages": [page for page in self.warrants if self.roles[page] is False and page not in self.ambiguous],
            "reference_pages": [page for page in self.pages if self.roles[page] is True],
            "unresolved_pages": sorted(self.unresolved_pages | {page for page in self.warrants if self.roles[page] is None or (self.roles[page] is False and page in self.ambiguous)}),
            "unresolved_authorization_pages": [page for page in self.authorization if self.roles[page] is not True and (self.roles[page] is None or page in self.authorization_ambiguous)],
            "nondecisive_pages": sorted(nondecisive),
            "objects": [{"page": page, "business": None if self.roles[page] is None else True, "status": value.status, "facts": psw_fact_summary(self.facts[page])} for page, value in outcomes],
        }
        logger.warning("PSW scope decision: %s", json.dumps(diagnostic, sort_keys=True, separators=(",", ":")))
        return result


def evaluate_psw_pdf_rule(audit: AuditInput, rule_no: int, witness, cache: dict, read_single: Callable[[], dict[str, object]]) -> tuple[dict[str, object], AuditResultItem]:
    """Evaluate all PDF objects through one source-located scope boundary."""
    from audit.engine.checkers.visual_facts import _source_identity

    path = Path(audit.file_url)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        facts = read_single()
        return facts, evaluate_psw_observation(rule_no, facts)
    digest = _source_identity(audit, cache)
    key = ("psw_objects", audit.audit_id, digest)
    if key not in cache:
        cache[key] = _Objects(audit, digest, cache)
    objects = cache[key]
    # Single and multiple PDF warrants use the same object/source boundary.
    from documents.ocr.env import ocr_policy_identity

    identity = ("ocr_policy", audit.audit_id)
    policy = ocr_policy_identity()
    if identity in cache and cache[identity] != policy:
        raise ValueError("OCR configuration changed during the audit")
    cache[identity] = policy
    return {}, objects.evaluate(rule_no, witness)
