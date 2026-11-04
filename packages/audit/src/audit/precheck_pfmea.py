"""Fixed PFMEA preparation rules from PPAP-09 v3.1, separate from engineering scores."""

from __future__ import annotations

import re
from collections import defaultdict

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.kinds import PpapDocumentType
from audit.precheck_facts import DefectFact, SourceFacts, UnknownFact, key, state_fact
from audit.precheck_sources import l1_criteria

ALIASES = {
    "step": ("过程步骤", "过程步骤编号", "工序号", "工序名称", "过程编号", "PROCESSSTEP"),
    "function": ("过程项功能", "过程项的功能", "过程项目的功能", "过程名称的功能", "FUNCTIONOFTHEPROCESSITEM", "PROCESSITEMFUNCTION"),
    "effect": ("失效影响", "失效后果", "失效影响FE", "失效影FE", "FAILUREEFFECT"),
    "severity": ("严重度", "严重度S", "严重性", "SEVERITY", "S"),
    "mode": ("失效模式", "潜在失效模式", "失效模式FM", "FAILUREMODE"),
    "occurrence": ("发生度", "频度", "发生度O", "频度O", "O", "OCCURRENCE", "FREQUENCY"),
    "cause": ("失效起因", "潜在失效起因", "失效原因", "失效起因FC", "FAILURECAUSE", "CAUSEOFFAILURE"),
    "prevention": ("现行预防控制", "现行预防控制PC", "现行过程控制预防", "当前的预防措施", "PREVENTIVECONTROL", "PREVENTIONCONTROL"),
    "detection_control": ("现行探测控制", "现行探测控制DC", "现行过程控制探测", "当前的探测措施", "DETECTIONCONTROL"),
    "detection": ("探测度", "探测度D", "D", "DETECTION"),
    "ap": ("AP", "措施优先级", "措施優先度AP", "优先级", "PRIORITYLEVEL"),
    "special": ("特殊特性", "特殊特性分类", "特性分类", "分类", "SPECIALCHARACTERISTIC", "CLASS"),
    "rpn": ("RPN", "风险顺序数", "风险序数", "RISKPRIORITYNUMBER"),
}
_REFERENCE = ("准则", "準則", "评分", "評分", "criteria", "数据库", "database", "查表")
_BLANK_CONTROL = {"", "无", "na", "n/a", "/", "none"}
_SYMBOLS = {"G", "Z", "NA", "N/A", "CC", "SC", "YC", "YS", "KPC", "KCC", "★", "◆", "▼", "▽", "☆", ""}


def score(value):
    text = str(value if value is not None else "").strip()
    return int(float(text)) if re.fullmatch(r"(?:[1-9]|10)(?:\.0+)?", text) else None


def control_scores(value):
    """Only actual measure suffixes count; never OP numbers, dates or percentages."""
    parts = [part.strip() for part in re.split(r"[\n;；]+", str(value or "")) if part.strip()]
    if not parts:
        raise UnknownFact("控制措施为空，尚无可核实的措施分值。")
    result = []
    for part in parts:
        match = re.search(r"(?:[（(\[]\s*)?(10|[1-9])\s*(?:[）)\]])?\s*$", part)
        if not match or match.start() and part[match.start() - 1].isdigit():
            raise UnknownFact("控制措施末尾没有唯一可抽取的1~10分值。")
        description = part[: match.start()].strip()
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", description) or re.search(r"(?:\d{4}[-/.]\d{1,2}[-/.]|\d+%)\s*$", description):
            raise UnknownFact("数字可能是编号、日期或数量，尚不能作为措施分值。")
        if re.search(r"[（(\[]\s*(?:10|[1-9])\s*[）)\]]", description):
            raise UnknownFact("同格多条措施未明确分隔，不能只采用最后一个分值。")
        result.append(int(match[1]))
    return result


def dimensions(value):
    text = str(value or "")
    pattern = r"(内部|工厂内|客户工厂|客户|OEM|主机厂|最终用户|终端客户|S|O|C)\s*[:：]"
    matches = list(re.finditer(pattern, text, re.I))
    aliases = {
        "内部": "internal",
        "工厂内": "internal",
        "s": "internal",
        "客户工厂": "customer",
        "客户": "customer",
        "oem": "customer",
        "主机厂": "customer",
        "o": "customer",
        "最终用户": "user",
        "终端客户": "user",
        "c": "user",
    }
    found = {}
    for index, match in enumerate(matches):
        category = aliases[match[1].casefold()]
        content = text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
        if category in found or not content:
            raise UnknownFact("三个维度内容重复或为空，尚不能确定维度归属。")
        found[category] = content
    if set(found) != {"internal", "customer", "user"}:
        raise DefectFact("W01", "过程项功能或失效影响未完整列出内部、客户与最终用户三个维度。")
    return found


class PfmeaFacts(SourceFacts):
    def __init__(self, audit):
        super().__init__(audit)
        candidates = []
        for table in self.data["tables"]:
            if any(token in key(table["id"]) for token in _REFERENCE):
                continue
            for row in range(1, min(table["rows"], 60) + 1):
                columns = {}
                for name, aliases in ALIASES.items():
                    matches = []
                    for column in range(1, min(table["cols"], 160) + 1):
                        text = key(effective(table, row, column).get("value"))
                        # Chinese/current headers take priority. A one-letter row
                        # only supplements missing columns in a second header row.
                        if text in {key(alias) for alias in aliases if len(key(alias)) > 1}:
                            matches.append(column)
                    if len(matches) == 1:
                        columns[name] = matches[0]
                if len(columns) < 5:
                    continue
                for name in ("severity", "occurrence", "detection"):
                    if name not in columns:
                        letter = {"severity": "s", "occurrence": "o", "detection": "d"}[name]
                        matches = [column for column in range(1, min(table["cols"], 160) + 1) if key(effective(table, row, column).get("value")) == letter and column not in columns.values()]
                        if len(matches) == 1:
                            columns[name] = matches[0]
                end = row
                if row < table["rows"]:
                    for name, aliases in ALIASES.items():
                        if name in columns:
                            continue
                        allowed = {key(alias) for alias in aliases}
                        matches = [column for column in range(1, min(table["cols"], 160) + 1) if key(effective(table, row + 1, column).get("value")) in allowed]
                        if len(matches) == 1 and matches[0] not in columns.values():
                            columns[name] = matches[0]
                            end = row + 1
                if len(columns) >= 8 and len(set(columns.values())) == len(columns):
                    data_count = sum(bool(key(effective(table, index, columns.get("mode", columns.get("cause", 1))).get("value"))) for index in range(end + 1, table["rows"] + 1))
                    candidates.append((data_count, len(columns), table, row, end, columns))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not candidates or candidates[0][0] == 0:
            raise UnknownFact("尚不能唯一定位含真实数据的PFMEA主表；参考表和空模板未作为主表。")
        if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2] and candidates[0][2]["id"] != candidates[1][2]["id"]:
            raise UnknownFact("存在多份同等候选PFMEA主表，需确认本次数据范围。")
        _, _, self.table, self.header, self.end, self.columns = candidates[0]
        trigger = self.columns.get("mode") or self.columns.get("cause")
        self.rows = [row for row in range(self.end + 1, self.table["rows"] + 1) if key(effective(self.table, row, trigger).get("value"))]
        if not self.rows:
            raise UnknownFact("PFMEA无可核实的数据行，不能按空范围通过。")

    def values(self, *fields):
        missing = [field for field in fields if field not in self.columns]
        if missing:
            raise UnknownFact("受控模板字段未能定位：" + "、".join(missing) + "；需核对模板，不能归为供应商缺陷。")
        for field in fields:
            self.read(self.table, self.header, self.columns[field])
        return [{field: self.read(self.table, row, self.columns[field], vertical_only=True) for field in fields} | {"_row": row} for row in self.rows]

    def validated_scores(self):
        rows = self.values("severity", "occurrence", "detection")
        for field in ("severity", "occurrence", "detection"):
            if sum(score(row[field]) is not None for row in rows) / len(rows) < 0.85:
                raise UnknownFact("评分列未通过85%取值分布自校验；须重新确认列绑定，不能当作供应商分值缺陷。")
        return rows

    def template(self):
        # At least three independent original features are needed by §4.2.2.
        has = self.columns.__contains__
        if has("function") and has("ap") and not has("rpn"):
            structure = {key(effective(self.table, self.header, column).get("value")) for column in range(1, self.table["cols"] + 1)}
            return "fifth" if {"过程项", "过程步骤", "过程作业要素"}.issubset(structure) else "simplified"
        if has("rpn") and not has("function") and any("频度" in str(effective(self.table, self.header, column).get("value") or "") for column in self.columns.values()):
            return "fourth_ap" if has("ap") else "fourth"
        raise UnknownFact("版本特征不足三项或互相矛盾，R04/R05/R06/R09不能沿用任意模板口径。")


def pfmea_precheck(audit, preset_key, group=None):
    """Run pure preparation facts. Scores' engineering correctness is excluded."""
    customer = {"U07": "UAES", "L07": "理想", "N02": "蔚来"}[preset_key]
    disabled = {"R02", "R04", "R05", "R06", "R09", "R17", "R18"} if customer == "UAES" else {"R19", "R20", "R23"}
    source_ids = [item["criterion_id"] for item in l1_criteria(preset_key)]
    base = {"R08": state_fact(preset_key + ":R08", "S02", "R08全版本停用；探测度口径只采用R09。")}
    for identifier in disabled:
        base[identifier] = state_fact(preset_key + ":" + identifier, "S02", "本客户差异层关闭此项；不记供应商缺陷。")
    try:
        facts = PfmeaFacts(audit)
    except Exception as exc:
        message = str(exc) if isinstance(exc, UnknownFact) else "PFMEA原件读取失败，尚无业务结论。"
        return [base.get(identifier) or state_fact(preset_key + ":" + identifier, None, message, status="unknown") for identifier in source_ids]
    operations = {}

    def r01():
        facts.values("mode")
        if customer != "UAES":
            history = [table for table in facts.data["tables"] if any(token in str(table["id"]) for token in ("履历", "变更", "修订", "历史"))]
            if len(history) != 1:
                raise UnknownFact("受控模板变更履历未唯一定位，需核对原件。")
            table = history[0]
            content = [(row, col) for row in range(2, table["rows"] + 1) for col in range(1, table["cols"] + 1) if key(effective(table, row, col).get("value"))]
            if not content:
                raise UnknownFact("履历只有表头或没有可核实有效数据，不能判为满足。")
            facts.read(table, *content[0])

    operations["R01"] = r01

    def r02():
        labels = [("公司名称",), ("车型项目", "项目名称"), ("零件名称",), ("零件号", "零件编号"), ("制造地址",), ("PFMEA开始日期",), ("PFMEA修订日期",), ("跨职能团队",)]
        empty = [aliases[0] for aliases in labels if not key(facts.label(facts.table, aliases, facts.header))]
        if empty:
            raise DefectFact("W02", "表头字段已定位但尚未填写：" + "、".join(empty))

    operations["R02"] = r02

    def r03():
        history = [table for table in facts.data["tables"] if any(token in str(table["id"]) for token in ("履历", "变更", "修订", "历史"))]
        if len(history) != 1:
            raise UnknownFact("履历日期表未唯一定位；不能推断日期一致。")
        table = history[0]
        heads = [(r, c) for r in range(1, min(20, table["rows"]) + 1) for c in range(1, table["cols"] + 1) if key(effective(table, r, c).get("value")) in {"日期", "修订日期", "修改日期"}]
        if len(heads) != 1:
            raise UnknownFact("履历日期列不能唯一定位。")
        row, column = heads[0]
        dates = []
        for index in range(row + 1, table["rows"] + 1):
            value = facts.read(table, index, column)
            if key(value):
                normalized = normalize_excel_date(value)
                if normalized is None:
                    raise DefectFact("B05", "履历日期已读取但不能解析为有效日期。")
                dates.append(normalized)
        if not dates:
            raise UnknownFact("履历没有可核实日期；不能按空集合通过。")
        start = normalize_excel_date(facts.label(facts.table, ("PFMEA开始日期",), facts.header))
        end = normalize_excel_date(facts.label(facts.table, ("PFMEA修订日期",), facts.header))
        if start is None or end is None:
            raise DefectFact("B05", "PFMEA开始或修订日期已定位但不能解析。")
        if (start, end) != (dates[0], dates[-1]):
            raise DefectFact("B06", "PFMEA开始/修订日期与履历首/末有效日期不一致。")

    operations["R03"] = r03

    def applicable(identifier):
        template = facts.template()
        if identifier == "R04" and template != "fifth" or identifier == "R06" and template != "fifth" or identifier == "R09" and template.startswith("fourth"):
            return False
        return True

    def r04():
        if not applicable("R04"):
            return False
        for row in facts.values("function"):
            dimensions(row["function"])

    operations["R04"] = r04

    def effect_scores(require_scores):
        template = facts.template()
        rows = facts.values("effect", "severity")
        for row in rows:
            values = dimensions(row["effect"])
            if require_scores or template in {"fifth", "fourth_ap"}:
                scores = []
                for value in values.values():
                    matches = re.findall(r"[（(\[]\s*(10|[1-9])\s*[）)\]]|(?<!\d)(10|[1-9])\s*分?\s*$", value)
                    valid = [int(left or right) for left, right in matches]
                    if len(valid) != 1:
                        raise UnknownFact("失效影响维度未带唯一可抽取分值；未从全文其它数字推算。")
                    scores.append(valid[0])
                if require_scores and score(row["severity"]) != max(scores):
                    raise DefectFact("B06", "严重度与三个维度独立分值的最大值不一致。")

    operations["R05"] = lambda: effect_scores(False)
    operations["R06"] = lambda: effect_scores(True) if applicable("R06") else False

    def r07():
        rows = facts.values("detection_control")
        if any(key(row["detection_control"]) in _BLANK_CONTROL for row in rows):
            raise DefectFact("W02", "部分探测控制行为空或声明无措施；须确认，未推断控制有效性。")

    operations["R07"] = r07

    def r09():
        if not applicable("R09"):
            return False
        for row in facts.values("prevention", "detection_control", "detection"):
            scores = control_scores(row["prevention"]) + control_scores(row["detection_control"])
            if score(row["detection"]) is None:
                raise UnknownFact("探测度取值尚不能可靠读取。")
            if score(row["detection"]) != min(scores):
                raise DefectFact("B06", "探测度不等于本行全部预防措施与探测措施分值的最小值；未套用已停用R08。")

    operations["R09"] = r09
    base["R10"] = state_fact(preset_key + ":R10", None, "原始ap_table_v2019.csv尚未随资产提供；按分则禁止近似查表，本项未执行。")

    def r11():
        values = [str(row["special"] or "").strip().upper().replace("【", "").replace("】", "") for row in facts.values("special")]
        if any(value not in _SYMBOLS for value in values):
            raise DefectFact("W03", "特殊特性存在不在源分则白名单内的值。")
        if set(values) & {"G", "Z"} and set(values) & {"CC", "SC"}:
            raise DefectFact("W04", "同一文件混用G/Z与CC/SC两套符号；需说明映射。")

    operations["R11"] = r11

    def r12():
        rows = facts.validated_scores()
        for row in rows:
            if any(score(row[field]) is None for field in ("severity", "occurrence", "detection")):
                raise DefectFact("W02" if any(not key(row[field]) for field in ("severity", "occurrence", "detection")) else "B05", "已绑定评分列存在空值、非整数或超出1~10的值。")
        if "rpn" in facts.columns:
            for row in facts.values("severity", "occurrence", "detection", "rpn"):
                try:
                    actual = float(row["rpn"])
                except (TypeError, ValueError):
                    raise UnknownFact("RPN派生字段尚不能读取为数值。") from None
                if actual != score(row["severity"]) * score(row["occurrence"]) * score(row["detection"]):
                    raise DefectFact("B06", "卷面RPN不等于本行S×O×D。")

    operations["R12"] = r12

    def r15():
        for row in facts.values("detection_control", "detection"):
            if key(row["detection_control"]) in _BLANK_CONTROL and score(row["detection"]) is not None and score(row["detection"]) < 10:
                raise DefectFact("B06", "原件声明无探测措施，但探测度小于10。")

    operations["R15"] = r15

    def r16():
        groups = defaultdict(set)
        for row in facts.values("mode", "cause", "prevention", "detection_control", "severity", "occurrence", "detection"):
            identity = tuple(key(row[field]) for field in ("mode", "cause", "prevention", "detection_control"))
            if not all(identity):
                raise UnknownFact("相同控制条件分组键未完整，不能归并失效模式。")
            groups[identity].add(tuple(score(row[field]) for field in ("severity", "occurrence", "detection")))
        if any(len(scores) > 1 for scores in groups.values()):
            raise DefectFact("W06", "完全相同的失效模式与控制条件下，S/O/D取值不一致。")

    operations["R16"] = r16

    def r23():
        matches = [
            (table, r, c)
            for table in facts.data["tables"]
            for r in range(1, table["rows"] + 1)
            for c in range(1, table["cols"] + 1)
            if any(token in str(effective(table, r, c).get("value") or "") for token in ("经验教训", "历史问题", "客户抱怨", "过往失效"))
        ]
        if not matches:
            raise UnknownFact("尚未定位到经验教训引用；不能推断是否使用了历史案例。")
        for table, row, column in matches:
            text = str(facts.read(table, row, column) or "")
            if len(text) > 8:
                return
            right = facts.read(table, row, min(column + 1, table["cols"]))
            if key(right):
                return
        raise DefectFact("W01", "经验教训字段已定位，但未填写实际案例引用。")

    operations["R23"] = r23

    def pfd_rows():
        if not group:
            raise UnknownFact("缺比对文件《过程流程图》，需提交方补交同零件原件。")
        peers = group.get_files_by_type(PpapDocumentType.PROCESS_FLOW_CHART)
        if not peers:
            raise UnknownFact("缺比对文件《过程流程图》，需提交方补交同零件原件。")
        if len(peers) != 1:
            raise UnknownFact("存在多个过程流程图候选，需确认原件，未任取文件。")
        peer = SourceFacts(peers[0])
        aliases = {
            "number": {"工序号", "工序编号", "过程编号", "过程步骤编号"},
            "name": {"工序名称", "过程名称", "操作名称"},
            "product": {"产品特性类别", "产品特殊特性类别", "产品特性分类"},
            "process": {"过程特性类别", "过程特殊特性类别", "过程特性分类"},
        }
        candidates = []
        for table in peer.data["tables"]:
            for header in range(1, min(table["rows"], 40) + 1):
                columns = {}
                for field, names in aliases.items():
                    matched = [column for column in range(1, table["cols"] + 1) if key(effective(table, header, column).get("value")) in {key(name) for name in names}]
                    if len(matched) == 1:
                        columns[field] = matched[0]
                if "name" in columns:
                    candidates.append((table, header, columns))
        if len(candidates) != 1:
            raise UnknownFact("过程流程图数据表或列不能唯一定位，尚不能跨文件判断。")
        table, header, columns = candidates[0]
        # Pair on independently read original part numbers. File names and the
        # Task customer selection alone do not prove the two originals match.
        left = facts.label(facts.table, ("零件号", "零件编号"), facts.header)
        right = peer.label(table, ("零件号", "零件编号"), header)

        def identity(value):
            return "".join(str(value or "").split()).casefold()

        if not identity(left) or identity(left) != identity(right):
            raise UnknownFact("两份原件零件号不完整或不一致，尚不能归为同一零件。")

        def optional_label(reader, original, names, before):
            present = any(key(effective(original, r, c).get("value")) in {key(name) for name in names} for r in range(1, before) for c in range(1, original["cols"] + 1))
            return reader.label(original, names, before) if present else None

        for names in [("项目", "项目名称", "车型项目", "项目代号"), ("版本", "版本号", "修订版次")]:
            left_value = optional_label(facts, facts.table, names, facts.header)
            right_value = optional_label(peer, table, names, header)
            if left_value is not None or right_value is not None:
                if not identity(left_value) or identity(left_value) != identity(right_value):
                    raise UnknownFact("两份原件项目或版本缺失/不一致，不能作跨文件冲突判断。")
        customer_aliases = {"理想": {"理想", "理想汽车", "liauto"}, "蔚来": {"蔚来", "蔚来汽车", "nio"}}[customer]
        for reader, original, before in [(facts, facts.table, facts.header), (peer, table, header)]:
            declared = optional_label(reader, original, ("客户", "客户名称", "Customer", "Customer Name"), before)
            if declared is not None and key(declared) not in customer_aliases:
                raise UnknownFact("原件明确客户标识与当前客户冲突，须确认或拆分任务。")
        rows = []
        for index in range(header + 1, table["rows"] + 1):
            if not key(effective(table, index, columns["name"]).get("value")):
                continue
            rows.append({field: peer.read(table, index, column, vertical_only=True) for field, column in columns.items()})
        if not rows:
            raise UnknownFact("对端过程流程图没有可核实数据行，不能按空集合通过。")
        for source in peer.sources:
            if source not in facts.sources and len(facts.sources) < 100:
                facts.sources.append(source)
        return rows

    def process_key(value, *, name_only=False):
        text = str(value or "").strip()
        match = re.fullmatch(r"((?:OP)?\d+(?:[.-]\d+)*)\s+(.+)", text, re.I)
        if match and name_only:
            text = match[2]
        return "".join(text.split()).casefold()

    def r17():
        targets = facts.values("step")
        rows = pfd_rows()
        expected = {process_key(row["step"], name_only=customer == "理想") for row in targets}
        for row in rows:
            if customer == "蔚来" and not key(row.get("number")):
                raise UnknownFact("蔚来PFD工序编号缺失，不能仅凭同名工序配对。")
            value = row["name"] if customer == "理想" else str(row["number"]) + " " + str(row["name"])
            if process_key(value, name_only=customer == "理想") not in expected:
                raise DefectFact("B06", "同零件PFD工序未在PFMEA过程步骤中体现：" + str(value))

    operations["R17"] = r17

    def r18():
        target = facts.values("step", "special")
        rows = pfd_rows()

        def normalize(value):
            value = str(value or "").strip().strip("【】").upper()
            return {"G": "CC", "Z": "SC"}.get(value, value)

        for row in rows:
            if "product" not in row or "process" not in row:
                raise UnknownFact("PFD产品与过程特性类别列未全部定位。")
            symbols = {normalize(row["product"]), normalize(row["process"])} & {"CC", "SC"}
            if not symbols:
                continue
            value = row["name"] if customer == "理想" else str(row.get("number") or "") + " " + str(row["name"])
            matched = [entry for entry in target if process_key(entry["step"], name_only=customer == "理想") == process_key(value, name_only=customer == "理想")]
            if not matched:
                raise UnknownFact("特性所在PFD工序没有唯一可靠的PFMEA对端，须先确认步骤映射。")
            if not symbols.issubset({normalize(entry["special"]) for entry in matched}):
                raise DefectFact("B06", "同零件同工序PFD与PFMEA特殊特性标识不一致（G/CC、Z/SC已归一）。")

    operations["R18"] = r18

    result = []
    for identifier in source_ids:
        if identifier in base:
            result.append(base[identifier])
        elif identifier in operations:

            def execute(identifier=identifier):
                value = operations[identifier]()
                if value is False:
                    raise UnknownFact("本模板体系未承载此项，按S02关闭；不构成文件缺陷。")

            observed = facts.observe(preset_key + ":" + identifier, execute)
            if "按S02关闭" in observed["message"]:
                observed.update(status="not_checked", code="S02")
            if observed["message"].startswith("缺比对文件"):
                observed.update(status="not_checked", code="S04")
            result.append(observed)
        else:
            related = {"R17": "过程流程图", "R18": "过程流程图", "R19": "特殊特性清单", "R20": "采购件质量计划", "R21": "三大文件", "R22": "三大文件履历"}.get(identifier)
            disabled_pair = customer == "UAES" and identifier in {"R17", "R18"} or customer != "UAES" and identifier in {"R19", "R20"}
            result.append(
                state_fact(
                    preset_key + ":" + identifier,
                    "S02" if disabled_pair else None,
                    "本客户不直接要求此对端。" if disabled_pair else "关联纯规则尚未执行，需核实同零件与版本的" + str(related) + "；未判为缺陷。",
                )
            )
    return result
