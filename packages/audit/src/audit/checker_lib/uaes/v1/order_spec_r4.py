"""UAES U14 decision methods for signed v1.0 revision 4 (ORD:1).

The PDF reading of revision 3 stays: every native page is looked at by the image model in groups of four, each located
signing slot or imprint is confirmed on its own crop, and code decides. Revision 4 changes what a complete reading proves.
The product owner decided (2026-11-24) that an original read in full which still shows no controlled stamp, or no signed
supply/demand responsibility slot, fails the clause. A page counts as looked at when the reading answered for it and every
slot or imprint it located there was confirmed; the model's own doubt about unseen corners does not reopen such a page.
A page that was never sent (scanned, rotated), a failed request or an unconfirmed object keeps the fact for a person.
A pass still needs every page's slots to be reported complete, so a missed blank slot cannot pass.
"""

from __future__ import annotations

from audit.checker_lib.uaes.v1.order_spec import _KINDS, _SIDE, UaesControlledDocumentAgentR3, label_text, located, revision_rows, unable, upright
from audit.engine.rule_engine import Decision, GuardStop


def pages_text(numbers) -> str:
    return "第" + "、".join(map(str, sorted(numbers))) + "页"


class UaesControlledDocumentAgentR4(UaesControlledDocumentAgentR3):
    """Reader for signed UAES v1.0 revision 4."""

    def pdf_order(self):
        import fitz

        witness = self.legacy().witness
        signed, blanks, seals, suspect, notes = set(), [], [], [], []
        # Pages the reading answered for, pages without an unconfirmed slot or imprint, pages whose slots it reported complete.
        looked, slots_clear, seals_clear, slots_complete = set(), set(), set(), set()
        doubts: list[str] = []
        failures: list[GuardStop] = []
        with fitz.open(str(self.audit_file.file_url)) as doc:
            count = len(doc)
            # Inventory every native page before any model call; never send a scanned or rotated page.
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
                    # A failed grant or service is not expected to recover within this clause: the rest stays unread.
                    break
                if not isinstance(response, dict) or not isinstance(response.get("pages"), list):
                    continue
                for n in numbers:
                    matches = [r for r in response["pages"] if isinstance(r, dict) and type(r.get("page")) is int and r["page"] == n]
                    if len(matches) != 1 or not isinstance(matches[0].get("objects"), list):
                        continue
                    row, page = matches[0], doc[n - 1]
                    looked.add(n)
                    # Whether an object located on this page was left unconfirmed: [responsibility slots, imprints].
                    doubt = [False, False]
                    verified = []
                    for obj in row["objects"]:
                        if not isinstance(obj, dict) or obj.get("kind") not in _KINDS:
                            doubt = [True, True]
                            continue
                        kind, state = obj["kind"], obj.get("state")
                        if kind not in _SIDE and state == "signed":
                            state = "visible"  # An imprint reported as applied is one seen there; its crop still confirms it.
                        box = located(page, obj.get("bbox"))
                        raw = obj.get("label")
                        label = label_text(raw) if isinstance(raw, str) else ""
                        domain = int(kind not in _SIDE)
                        if box is None:
                            doubt[domain] = True
                            continue
                        if kind in _SIDE:
                            # The local role must be visible, not invented from the document owner. An actual
                            # internally-labelled revision row cannot be reclassified as a supplier blank.
                            role = next((t for t in _SIDE[kind] if t in label), None)
                            if not role or state not in {"signed", "blank"} or any((rect & box).get_area() > 0.5 * box.get_area() for rect in internal[n]):
                                doubt[0] = True
                                continue
                            if not local_read(page, box, kind, state):
                                doubt[0] = True
                                continue
                        elif (
                            state != "visible"
                            or (kind == "controlled_stamp" and not any(t in label for t in ("受控", "controlled", "encontrol")))
                            or (kind == "contract_seal" and not (any(t in label for t in _SIDE["supplier_slot"]) and any(t in label for t in ("合同", "contract"))))
                        ):
                            doubt[1] = True
                            continue
                        elif kind in {"controlled_stamp", "contract_seal"} and seals:
                            continue  # An already located acceptable stamp needs no second example.
                        elif not local_read(page, box, kind, state):
                            doubt[1] = True
                            continue
                        witness({"kind": "pdf_region", "page": n, "bbox": list(box), "fact": kind}, f"{raw}：{state}", visual=True)
                        verified.append((kind, state, box))
                    # Conflicting observations at the same located slot cannot prove it is signed OR blank.
                    for kind, state, box in verified:
                        if kind in _SIDE:
                            if any(k == kind and s != state and (b & box).get_area() > 0.5 * min(b.get_area(), box.get_area()) for k, s, b in verified):
                                doubt[0] = True
                            elif state == "blank":
                                blanks.append(f"第{n}页{'供方' if kind == 'supplier_slot' else '需方'}责任签署栏空白未签")
                            else:
                                signed.add(kind)
                        else:
                            (suspect if kind == "unreadable_stamp" else seals).append(n)
                    # A native role-labelled signature field that the visual inventory missed is an unconfirmed slot on this
                    # page; mere mentions of suppliers in technical prose do not count.
                    for text, rect in native_roles[n]:
                        if any(t in label_text(text) for t in ("签字", "签名", "签署", "signature")) and not any(kind in _SIDE and box.intersects(rect) for kind, _, box in verified):
                            doubt[0] = True
                    for unsure, bucket in zip(doubt, (slots_clear, seals_clear)):
                        if not unsure:
                            bucket.add(n)
                    if not doubt[0] and row.get("slots_complete") is True:
                        slots_complete.add(n)
                    if doubt[0]:
                        doubts.append(f"第{n}页有未能核实的责任签署栏")
                    if doubt[1]:
                        doubts.append(f"第{n}页有未能核实的印迹")
                    witness({"kind": "pdf_page", "page": n}, f"本页已看过：责任签署栏{'有未核实处' if doubt[0] else '均已核实'}；印迹{'有未核实处' if doubt[1] else '均已核实'}", visual=True)
                    if blanks or suspect:
                        break
                if blanks or suspect or failures:
                    break
        note = "；" + "；".join(notes[-2:]) + "，不是供需责任签署栏" if notes else ""
        if blanks:
            return "NG", "；".join(blanks) + "，签署不闭环" + note + "。"
        if suspect:
            return "NG", f"第{suspect[0]}页已定位疑似印迹，章内受控或合同标识不可辨，请人工复核。"
        if count and len(seals_clear) == count and not seals:
            return "NG", f"已逐页看过原件全部{count}页，未见受控章或供应商签署栏合同章（原件图像目视识读，请对照原件核实）。"
        missing = [side for k, side in (("supplier_slot", "供方"), ("customer_slot", "需方")) if k not in signed]
        if count and len(slots_clear) == count and missing:
            stamp = "" if seals else "；另：受控章或供应商签署栏合同章尚未从原件核实"
            return (
                "NG",
                f"已逐页看过原件全部{count}页，未见{'、'.join(missing)}责任签署栏的负责人签字（未见设栏，不是已设栏空白；原件图像目视识读，请对照原件核实）{stamp}" + note + "。",
            )
        if failures and failures[0].status == Decision.ERR:
            raise GuardStop(Decision.ERR, "原件图像请求失败，未据此判定无章或未签" + note + "。")
        if not count or not seals or missing or len(slots_complete) != count:
            unread = [n for n in range(1, count + 1) if n not in looked]
            gaps = [
                *([f"{pages_text(unread)}未能看过（扫描、旋转页或读取失败）"] if unread else []),
                *doubts[:6],
                *([f"{pages_text(set(looked) - slots_complete)}的责任签署栏未报告看全，不能据此认定签署闭环"] if seals and not missing and set(looked) - slots_complete else []),
            ]
            raise unable(f"原件共{count}页，已看过{len(looked)}页；" + ("已有定位的有效印迹；" if seals else "") + "；".join(gaps or ["证据不全"]) + "，不能据此判定无章、未签或全部签齐" + note + "。")
        return "PASS", f"第{seals[0]}页有受控章或供方签署栏合同章；已核过全部{count}页，供需双方责任签署栏均有签字且无空白栏。"
