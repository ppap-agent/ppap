"""Explicit 00-A structure observations; no business verdict promotion."""

from audit.contracts import AuditEvidence
from audit.precheck.sources import required_structure

from .native import MissingFact, UnknownFact


def structural_facts(agent):
    entry = agent.rule.preset_key
    if not entry:
        return []
    native = None
    checks = {}

    def facts():
        nonlocal native
        if native is None:
            native = agent.native()
        return native

    def text(value):
        return lambda: facts().require_text(value)

    def field(*names):
        return lambda: facts().field(names)

    def all_of(*operations):
        return lambda: [operation() for operation in operations]

    def formal_structure(method):
        def run():
            status, message = method()
            if status != "PASS":
                raise UnknownFact(message)

        return run

    def selection(*aliases):
        def run():
            if agent.selection(aliases) is None:
                raise UnknownFact("实际选择有多项，需确认。")

        return run

    def column(*aliases, title=()):
        return lambda: facts().columns(facts().table(title), {"field": aliases})

    if entry == "U09":
        phrases = ("包装方案", "零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单")
        checks = {f"{entry}:S{i}": text(value) for i, value in enumerate(phrases, 1)}
        checks["U09:S1"] = lambda: facts().require_title("包装方案")
    elif entry == "U11":
        checks = {"U11:S1": text("工程更改一览表")}
        aliases = [("项目", "项目名称", "项目代号"), ("零件号", "物料编号", "零件编号"), ("变更内容简述", "变更内容", "更改内容"), ("变更原因", "更改原因"), ("时间", "变更日期", "更改日期", "日期")]
        checks.update({f"U11:S{i}": column(*names, title=("工程更改",)) for i, names in enumerate(aliases, 2)})
    elif entry == "U12":
        checks = {"U12:S1": lambda: agent.identity("物料名称"), "U12:S2": lambda: agent.identity("物料编号")}
        aliases = [("编号", "序号"), ("产品特性", "过程特性", "特殊特性名称"), ("特性及公差", "特性值"), ("特性类型", "特性类别")]
        checks.update({f"U12:S{i}": column(*names, title=("特殊特性", "特性清单")) for i, names in enumerate(aliases, 3)})

        def rows_required():
            result = agent.rule_2()
            if result[0] != "PASS":
                raise UnknownFact(result[1])

        checks["U12:S7"] = rows_required
    elif entry == "U15":
        checks = {"U15:S1": text("流转记录"), "U15:S2": text("试装结论"), "U15:S3": text("检验结论"), "U15:S4": all_of(text("流程结束"), text("流程完成"))}
    elif entry == "U18":
        checks = {"U18:S1": text("供应商零件追溯")}
        checks.update({f"U18:S{i}": field(name) for i, name in enumerate(("基本信息", "分供方追溯", "生产过程追溯", "交付追溯", "追溯状态总结"), 2)})
    elif entry == "U17":
        checks = {
            "U17:S2": selection("安全及政府条例"),
            "U17:S6": selection("是否报告了受客户关注的材料信息"),
            "U17:S9": formal_structure(agent.rule_4),
            "U17:S10": formal_structure(agent.rule_5),
            "U17:S3": all_of(text("供方制造厂资料"), text("买方资料")),
            "U17:S4": field("公司名称"),
            "U17:S5": all_of(field("采购方及部门"), field("采购方代码"), field("应用")),
            "U17:S7": field("以IMDS或者其它客户格式递交"),
        }
    elif entry == "U19":
        checks = {"U19:S1": column("否决", "Reject", title=("封面", "cover")), "U19:S2": field("UAES批准的前提/备注")}
    elif entry == "U13":
        result = agent.check(0)
        status = {"PASS": "satisfied", "UNABLE": "unknown", "ERR": "error", "NG": "unknown"}[result.status]
        evidence = result.evidence.model_dump(mode="json") if result.evidence else None
        return [
            {"id": "U13:S1", "source_id": "U13:S1", "status": status, "code": None, "message": result.suggestions, "certified": False, "evidence": evidence},
            {
                "id": "U13:S2",
                "source_id": "U13:S2",
                "status": "satisfied" if status == "satisfied" else status,
                "code": None,
                "message": "需人工确认样品照片是否符合要求",
                "certified": False,
                "evidence": evidence,
            },
        ]
    results = []
    for item in required_structure(entry):
        identifier = item["id"]
        if identifier not in checks:
            results.append(
                {
                    "id": identifier,
                    "source_id": identifier,
                    "status": "unknown",
                    "code": "S03",
                    "message": item["criterion"] + "；本项需核对原件，不以标签或正式方向替代。",
                    "certified": False,
                    "evidence": None,
                }
            )
            continue
        agent._evidence_sources = []
        try:
            checks[identifier]()
            status = "satisfied" if agent._evidence_sources else "unknown"
            message = "已读取原件结构：" + item["criterion"]
        except (UnknownFact, MissingFact) as exc:
            status, message = "unknown", str(exc) + "；缺栏可能是客户受控模板差异，需核对模板（S05）。"
        except Exception as exc:
            status, message = "error", f"原件结构读取失败（{type(exc).__name__}）。"
        evidence = None
        if agent._evidence_sources:
            suffix = agent.audit_file.file_url.rsplit(".", 1)[-1].lower()
            evidence = AuditEvidence(required="structure", used="structure", source_format=suffix, prepared_format=suffix, sources=tuple(agent._evidence_sources)).model_dump(mode="json")
        results.append({"id": identifier, "source_id": identifier, "status": status, "code": None if status == "satisfied" else "S05", "message": message, "certified": False, "evidence": evidence})
    return results
