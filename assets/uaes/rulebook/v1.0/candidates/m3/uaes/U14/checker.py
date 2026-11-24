"""UAES U14 candidate: order specification (BV), UAES v1.0 revision 3 (ORD:1).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES controlled-document reader as the carrier; at run time `Audit` extends the declared reader
class with the revision 3 clause method below, reusing its controlled-stamp reading. Verdicts are decided in
code; the image model only reads stamps, seals and signatures off the original.
"""

from __future__ import annotations

import sys
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext
from resources import RESOURCE

# Facts, not a verdict: per-page coverage and located objects. A revision table has different signers.
_SIDE = {"supplier_slot": ("供方", "供应商", "supplier"), "customer_slot": ("需方", "采购方", "顾客", "客户", "customer", "purchaser", "uaes", "联合汽车电子")}
_KINDS = [*_SIDE, "controlled_stamp", "contract_seal", "unreadable_stamp"]
_BOX = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
_OBJECT = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": _KINDS}, "label": {"type": "string"}, "state": {"type": "string", "enum": ["signed", "blank", "visible", "unknown"]}, "bbox": _BOX},
    "required": ["kind", "label", "state", "bbox"],
    "additionalProperties": False,
}
_PAGE = {
    "type": "object",
    "properties": {"page": {"type": "integer"}, "slots_complete": {"type": "boolean"}, "seals_complete": {"type": "boolean"}, "objects": {"type": "array", "items": _OBJECT}},
    "required": ["page", "slots_complete", "seals_complete", "objects"],
    "additionalProperties": False,
}
_SCHEMA = {"type": "object", "properties": {"pages": {"type": "array", "items": _PAGE}}, "required": ["pages"], "additionalProperties": False}
_PROMPT = (
    "逐张观察这些订货规范原页，转写原页已有的签署栏与印迹，不判定合规。图像顺序对应原页号：{pages}。分别汇报每页是否已看清所有责任签署栏和印迹。"
    "签署栏只列有明确供方或需方标识的负责人签字栏，每栏独立列出，抄录当地角色栏题和在该栏能看到的实际签名(signed)、确实空白(blank)或看不清(unknown)。"
    "内部修订记录的编制、校对、审核、标准化、批准（包括横杠、空白行）不是供需双方责任签字栏；遇有分区不清的栏，不推测归属，slots_complete=false。"
    "只列真实印迹：controlled_stamp 抄印内受控标识；contract_seal 须能同时抄出本地供方签署栏标题和同栏合同章文字；"
    "确认存在疑似印迹但章内不可辨才列 unreadable_stamp。水印、印刷商标、空白盖章处、未定位或读取失败不是疑似印章。每项给包括标识和填写处的紧框，"
    "bbox=[x0,y0,x1,y1]相对本页0到1。某类区域太小/太糊或没查完，该类 complete=false，不能填空数组冒充未设。"
)


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def label_text(label: str) -> str:
    return "".join(label.casefold().split())


def located(page, values):
    import fitz

    if not isinstance(values, list) or len(values) != 4 or not all(type(v) in {float, int} for v in values):
        return None
    x, y, r, b = values
    if not (0 <= x < r <= 1 and 0 <= y < b <= 1) or (r - x) * (b - y) >= 0.9:
        return None
    return fitz.Rect(x * page.rect.width, y * page.rect.height, r * page.rect.width, b * page.rect.height)


def upright(page):
    return [
        (s["text"].strip(), s["bbox"])
        for b in page.get_text("dict", flags=0)["blocks"]
        for line in b.get("lines", [])
        if line["dir"][0] > 0.99
        for s in line["spans"]
        if s["text"].strip() and s.get("alpha", 255)
    ]


def revision_rows(page, spans):
    """Read filled revision rows from their actual grid. Keep watermark text outside the cells' evidence."""
    import fitz

    names = ("修订", "日期", "编制", "校对", "审核", "标准化", "批准")
    if not {"修订", "编制", "校对"}.issubset({v for v, _ in spans}):
        return [], []
    boxes, notes = [], []
    for table in page.find_tables().tables:

        def contents(cell):
            if cell is None:
                return []
            rect = fitz.Rect(cell)
            return [v for v, (x, y, r, b) in spans if rect.contains(fitz.Point((x + r) / 2, (y + b) / 2))]

        if not table.rows:
            continue
        header = [contents(c) for c in table.rows[0].cells]
        if any(any(side in label_text(v) for group in _SIDE.values() for side in group) for col in header for v in col):
            continue
        index = {name: i for i, labels in enumerate(header) for name in names if name in labels}
        if not {"修订", "编制", "校对"}.issubset(index):
            continue
        boxes.append(fitz.Rect(table.bbox))
        for row in table.rows[1:]:
            values = {k: " ".join(contents(row.cells[i])) for k, i in index.items()}
            if values["修订"] and values["编制"]:
                notes.append((row.bbox, "，".join(f"{k}：{v}" for k, v in values.items() if v)))
    return boxes, notes


class OrderClauses:
    """ORD:1: independently verified blanks can fail immediately; absence and completeness need complete inspection."""

    READER: Any = None

    def pages_read(self, images, numbers):
        from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
        from audit.engine.runtime import get_runtime

        runtime = get_runtime()
        read = getattr(runtime, "read_ocr_visual_facts", None) if runtime is not None else request_ocr_visual_facts
        if not callable(read):
            raise unable("当前运行环境不能读取原件图像。")
        return self._from_legacy(lambda _: read(images=images, prompt=_PROMPT.format(pages="、".join(map(str, numbers))), response_schema={"name": "order_fields", "schema": _SCHEMA}))

    def pdf_order(self):
        import fitz

        witness = self.legacy().witness
        signed, blanks, seals, suspect, notes = set(), [], [], [], []
        slot_coverage, seal_coverage, failures = set(), set(), []
        with fitz.open(str(self.audit_file.file_url)) as doc:
            count = len(doc)
            # Inventory every native page before any model call; never send a non-PSW scanned page for OCR.
            legal, internal, native_roles = [], {}, {}
            for page in doc:
                n = page.number + 1
                spans = upright(page)
                if page.rotation or not spans or any(fitz.Rect(img["bbox"]).get_area() >= 0.9 * page.rect.get_area() for img in page.get_image_info()):
                    continue
                legal.append(n)
                internal[n], revisions = revision_rows(page, spans)
                native_roles[n] = [(v, fitz.Rect(b)) for v, b in spans if any(t in label_text(v) for group in _SIDE.values() for t in group)]
                for bbox, text in revisions:
                    witness({"kind": "pdf_region", "page": n, "bbox": list(bbox), "fact": "内部修订记录"}, text)
                    notes.append(f"第{n}页内部修订记录（{text}）")

            def local_read(page, box, kind, state):
                try:
                    return self.confirm_box(page, box, kind, state)
                except GuardStop as exc:
                    failures.append(exc)
                    return False

            for i in range(0, len(legal), 4):
                numbers = legal[i : i + 4]
                images = []
                for n in numbers:
                    p = doc[n - 1]
                    scale = min(2.2, 1900 / max(p.rect.width, p.rect.height))
                    images.append(p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png"))
                try:
                    response = self.pages_read(images, numbers)
                except GuardStop as exc:
                    failures.append(exc)
                    # An unavailable grant or service is not expected to recover within this clause. Do not
                    # issue the same failed request for every group; preserve native/earlier independent facts.
                    break
                if not isinstance(response, dict) or not isinstance(response.get("pages"), list):
                    continue
                for n in numbers:
                    matches = [r for r in response["pages"] if isinstance(r, dict) and type(r.get("page")) is int and r["page"] == n]
                    if len(matches) != 1 or not isinstance(matches[0].get("objects"), list):
                        continue
                    row, page = matches[0], doc[n - 1]
                    complete = [row.get("slots_complete") is True, row.get("seals_complete") is True]
                    verified = []
                    for obj in row["objects"]:
                        if not isinstance(obj, dict) or obj.get("kind") not in _KINDS:
                            complete = [False, False]
                            continue
                        kind, state = obj["kind"], obj.get("state")
                        box = located(page, obj.get("bbox"))
                        raw = obj.get("label")
                        label = label_text(raw) if isinstance(raw, str) else ""
                        domain = int(kind not in _SIDE)
                        if box is None:
                            complete[domain] = False
                            continue
                        if kind in _SIDE:
                            # The local role must be visible, not invented from the document owner. An actual
                            # internally-labelled revision row cannot be reclassified as a supplier blank.
                            role = next((t for t in _SIDE[kind] if t in label), None)
                            if not role or state not in {"signed", "blank"} or any((rect & box).get_area() > 0.5 * box.get_area() for rect in internal[n]):
                                complete[0] = False
                                continue
                            if not local_read(page, box, kind, state):
                                complete[0] = False
                                continue
                        elif (
                            state != "visible"
                            or (kind == "controlled_stamp" and not any(t in label for t in ("受控", "controlled", "encontrol")))
                            or (kind == "contract_seal" and not (any(t in label for t in _SIDE["supplier_slot"]) and any(t in label for t in ("合同", "contract"))))
                        ):
                            complete[1] = False
                            continue
                        elif kind in {"controlled_stamp", "contract_seal"} and seals:
                            continue  # An already located acceptable stamp needs no second example.
                        elif not local_read(page, box, kind, state):
                            complete[1] = False
                            continue
                        witness({"kind": "pdf_region", "page": n, "bbox": list(box), "fact": kind}, f"{raw}：{state}", visual=True)
                        verified.append((kind, state, box))
                    # Conflicting observations at the same located slot cannot prove it is signed OR blank.
                    for kind, state, box in verified:
                        if kind in _SIDE:
                            if any(k == kind and s != state and (b & box).get_area() > 0.5 * min(b.get_area(), box.get_area()) for k, s, b in verified):
                                complete[0] = False
                            elif state == "blank":
                                blanks.append(f"第{n}页{'供方' if kind == 'supplier_slot' else '需方'}责任签署栏空白未签")
                            else:
                                signed.add(kind)
                        else:
                            (suspect if kind == "unreadable_stamp" else seals).append(n)
                    # A native role-labelled signature field that the visual inventory missed invalidates
                    # absence on this page; mere mentions of suppliers in technical prose do not suffice.
                    for label, rect in native_roles[n]:
                        if any(t in label_text(label) for t in ("签字", "签名", "签署", "signature")) and not any(kind in _SIDE and box.intersects(rect) for kind, _, box in verified):
                            complete[0] = False
                    for covered, bucket in zip(complete, (slot_coverage, seal_coverage)):
                        if covered:
                            bucket.add(n)
                    if any(complete):
                        witness({"kind": "pdf_page", "page": n}, f"本页完整核查：责任签署栏={complete[0]}；印迹={complete[1]}", visual=True)
                    if blanks or suspect:
                        break
                if blanks or suspect or failures:
                    break
        note = "；" + "；".join(notes[-2:]) + "，不是供需责任签署栏" if notes else ""
        if blanks:
            return "NG", "；".join(blanks) + "，签署不闭环" + note + "。"
        if suspect:
            return "NG", f"第{suspect[0]}页已定位疑似印迹，章内受控或合同标识不可辨，请人工复核。"
        if count and len(seal_coverage) == count and not seals:
            return "NG", f"核过全部{count}页，未见受控章或供应商签署栏合同章。"
        missing = [side for k, side in (("supplier_slot", "供方"), ("customer_slot", "需方")) if k not in signed]
        if count and len(slot_coverage) == count and missing:
            return "NG", f"核过全部{count}页，未见{'、'.join(missing)}责任签署栏的负责人签字（未见设栏，不是已设栏空白）" + note + "。"
        if failures and failures[0].status == Decision.ERR:
            raise GuardStop(Decision.ERR, "原件图像请求失败，未据此判定无章或未签" + note + "。")
        if not count or not seals or len(slot_coverage) != count or len(seal_coverage) != count:
            raise unable(
                f"原件共{count}页，已完整核查签署栏{len(slot_coverage)}页、印迹{len(seal_coverage)}页；"
                + ("已有定位的有效印迹；" if seals else "")
                + "证据不全，不能据此判定无章、未签或全部签齐"
                + note
                + "。"
            )
        return "PASS", f"第{seals[0]}页有受控章或供方签署栏合同章；已核过全部{count}页，供需双方责任签署栏均有签字且无空白栏。"

    def confirm_box(self, page, box, kind, state):
        import fitz

        scale = min(3, 1400 / max(box.width, box.height))
        image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=box, alpha=False).tobytes("png")
        desc = {
            "supplier_slot": "明确供方标记的负责人签字栏",
            "customer_slot": "明确需方标记的负责人签字栏",
            "controlled_stamp": "真实受控印迹，章内受控标识可读，不是表格标签或商标",
            "contract_seal": "写有合同字样的真实印章，且在图中可见的供方签署栏",
            "unreadable_stamp": "确实有疑似实体印迹图形，但印内标识客观不可辨，不是空栏、商标、水印或裁切不全",
        }
        states = {"signed": "栏内有人签名", "blank": "整个签署格可见且空白未签", "visible": "印迹可见"}
        try:
            raw = self.transcribe(
                image,
                f"只查本局部是否能直接看到：{desc[kind]}，{states[state]}。不清楚填null。",
                {"type": "object", "properties": {"visible": {"type": ["boolean", "null"]}}, "required": ["visible"], "additionalProperties": False},
                "订货规范局部",
            )
        except GuardStop as exc:
            if exc.status == Decision.ERR:
                raise
            return False
        return isinstance(raw, dict) and raw.get("visible") is True

    @rule("订货规范受控章与供需双方签署闭环")
    def ord_1(self, ctx: RuleContext):
        if str(self.audit_file.file_url).lower().endswith(".pdf"):
            return self.pdf_order()
        # The office original reader only returns universal observations when its sheet scope is complete.
        fields = {
            "controlled": "文件上有实际盖的受控章。",
            "contract": "供方签署栏上盖有合同章。",
            "unreadable_imprint": "确实存在疑似印迹图形而印内标识不可辨。没有定位到印迹不算。",
            "blank": "明确的供方或需方责任签署栏已设但实际空白未签；内部修订会签栏不算。",
            "supplier": "明确的供方负责人签署栏有实际签字（不是内部修订会签人）。",
            "customer": "明确的需方负责人签署栏有实际签字（不是内部修订会签人）。",
        }
        v, _ = self._from_legacy(lambda agent: agent.visual_observation(fields, existential_fields=tuple(fields)))
        if v.get("blank") is True:
            return "NG", "存在已设但空白未签的供需责任签署栏，签署不闭环。"
        if v.get("unreadable_imprint") is True:
            return "NG", "原件存在疑似印迹但章内标识不可辨，请人工复核。"
        if v.get("controlled") is not True and v.get("contract") is not True:
            if v.get("controlled") is False and v.get("contract") is False:
                return "NG", "原件未见受控章或供方签署栏合同章。"
            raise unable("原件印迹尚未核实。")
        if any(v.get(k) is False for k in ("supplier", "customer")):
            return "NG", "未见供需双方均有负责人签字；不能据此把内部修订栏记作已设未签栏。"
        if v.get("blank") is not False or any(v.get(k) is not True for k in ("supplier", "customer")):
            raise unable("供需双方责任签署栏尚未全部核实。")
        return "PASS", "受控章或供方签署栏合同章已核实，供需双方均签字且无空白责任栏。"


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause method (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesControlledDocumentAgent": OrderClauses}

    def _implementation(self) -> Any:
        if self._agent is None and self._failure is None:
            reader = super()._implementation()
            if reader is None:
                return None
            base = type(reader)
            close = getattr(reader, "close", None)
            if callable(close):
                close()
            try:
                extended = type(base.__name__ + "R3", (self.CLAUSES[base.__name__], base), {"READER": sys.modules[base.__module__]})
                self._agent = extended(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._agent, self._failure = None, exc
                raise
        return super()._implementation()


def create_audit(audit_file, rule, group_context=None):
    return Audit(audit_file, rule, group_context, resource=RESOURCE)
