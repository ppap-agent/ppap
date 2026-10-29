"""Small, hand-worked ontology counterexamples, independent of model output.

These are source fixtures and an oracle for the existing local experiment. The
oracle is never copied into a model-readable input or stage directory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from openpyxl import Workbook


@dataclass(frozen=True)
class Table:
    filename: str
    sheet: str
    rows: tuple[tuple[str | None, ...], ...]
    # None means the source rule does not prescribe an identity component set.
    identity_columns: tuple[str, ...] | None
    objects: tuple[tuple[int, ...], ...]
    columns: tuple[tuple[str, str], ...]
    identity_cells: tuple[str, ...] = ()
    comparison_cells: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    key: str
    rule: str
    tables: tuple[Table, ...]
    status: str
    relation_kind: str = "same_instance"
    match: str = "one_to_one"
    comparison: bool = False
    clarification: bool = False
    refinement: bool = False

    @property
    def target(self) -> str:
        return self.tables[-1].filename

    def identity(self, filename: str) -> str:
        return hashlib.sha256((self.key + "\0" + filename).encode()).hexdigest()


PROCESS = "Process.xlsx"
CONTROL = "Control.xlsx"
PAIR_RULE = (
    "基准工序清单中的每个工序必须在控制计划中按原始工序编号一一对应；"
    "每个物理数据行是一个独立工序实例，重复编号不能去重。"
    "工序编号按文字逐字比较，010、0100、O 和 0 不可合并；缺少对应或重复对应均不符合。"
)


def cases() -> tuple[Case, ...]:
    """Expected statuses, identities and source groups are manually specified."""
    code = (("code", "A"), ("classification", "B"))
    compound = (("product", "A"), ("code", "B"), ("classification", "C"))
    return (
        Case(
            "compound-identity",
            "工序由产品编号和工序编号共同确定。基准清单中的每个工序必须在控制计划中按这两个原始编号一一对应；同工序编号的不同产品不是同一工序。每个数据行是独立实例，不得去重。",
            (
                Table(PROCESS, "Process", (("产品编号", "工序编号", "分类"), ("P-1", "010", "CC"), ("P-2", "010", "SC")), ("A", "B"), ((2,), (3,)), compound),
                Table(CONTROL, "Control", (("产品编号", "工序编号", "分类"), ("P-1", "010", "CC"), ("P-2", "020", "SC")), ("A", "B"), ((2,), (3,)), compound),
            ),
            "NG",
            refinement=True,
        ),
        Case(
            "classification-is-not-identity",
            PAIR_RULE + "相同的CC分类不能证明两个工序相同。",
            (
                Table(PROCESS, "Process", (("工序编号", "分类"), ("010", "CC"), ("020", "CC")), ("A",), ((2,), (3,)), code),
                Table(CONTROL, "Control", (("工序编号", "分类"), ("010", "CC"), ("030", "CC")), ("A",), ((2,), (3,)), code),
            ),
            "NG",
        ),
        Case(
            "multirow-preserves-conflict",
            "基准清单中每个工序须在控制计划中按原始工序编号一一对应，且分类必须一致。控制计划中有工序编号的行及紧随其后的空工序编号行共同描述一个工序；空编号续行不是额外工序。保留两份原件的分类值，不能用编号对应掩盖分类冲突。",
            (
                Table(PROCESS, "Process", (("工序编号", "分类"), ("010", "CC"), ("020", "SC")), ("A",), ((2,), (3,)), code),
                Table(CONTROL, "Control", (("工序编号", "分类"), ("010", "SC"), (None, None), ("020", "SC"), (None, None)), ("A",), ((2, 3), (4, 5)), code),
            ),
            "NG",
            comparison=True,
        ),
        Case(
            "duplicates-remain-instances",
            PAIR_RULE,
            (
                Table(PROCESS, "Process", (("工序编号", "分类"), ("010", "CC"), ("020", "SC")), ("A",), ((2,), (3,)), code),
                Table(CONTROL, "Control", (("工序编号", "分类"), ("010", "CC"), ("010", "CC"), ("020", "SC")), ("A",), ((2,), (3,), (4,)), code),
            ),
            "NG",
        ),
        Case(
            "attachment-foreign-key",
            "零件表每个零件的附件引用必须对应本次输入中的一个文件实体。附件引用保存本次文件的输入内ID，按原文逐字比较。零件以零件编号确定身份，文件以输入内ID确定身份；附件引用是关联外键，不是零件身份。文件内容中的记录不能代替文件实体。",
            (
                Table("Attachment.xlsx", "Attachment", (("材料声明",), ("Steel",)), ("id",), ((0,),), ()),
                Table("Parts.xlsx", "Parts", (("零件编号", "附件引用"), ("P-1", "@Attachment.xlsx")), ("A",), ((2,),), (("part", "A"), ("attachment", "B"))),
            ),
            "PASS",
            relation_kind="association",
            match="any",
        ),
        Case(
            "literal-concatenation-matches",
            "基准清单每行的工序编号、字面分隔符' / '及工序名称拼接出的完整文字，必须原样出现在标签表的标签列中。"
            "只按明确的字面拼接查找，不把它改成仅按工序编号认定同一实例，不删除空格、斜杠或标点。每行是独立记录。",
            (
                Table(PROCESS, "Process", (("工序编号", "工序名称"), ("010", "Drill"), ("020", "Cut")), None, ((2,), (3,)), (("code", "A"), ("name", "B"))),
                Table("Labels.xlsx", "Labels", (("标签",), ("010 / Drill",), ("020 / Cut",)), ("A",), ((2,), (3,)), (("label", "A"),)),
            ),
            "PASS",
            relation_kind="association",
            match="any",
        ),
        Case(
            "literal-concatenation-differs",
            "基准清单每行的工序编号、字面分隔符' / '及工序名称拼接出的完整文字，必须原样出现在标签表的标签列中。"
            "只按明确的字面拼接查找，不把它改成仅按工序编号认定同一实例，不删除空格、斜杠或标点。每行是独立记录。",
            (
                Table(PROCESS, "Process", (("工序编号", "工序名称"), ("010", "Drill"), ("020", "Cut")), None, ((2,), (3,)), (("code", "A"), ("name", "B"))),
                Table("Labels.xlsx", "Labels", (("标签",), ("010Drill",), ("020 / Cut",)), ("A",), ((2,), (3,)), (("label", "A"),)),
            ),
            "NG",
            relation_kind="association",
            match="any",
        ),
        Case(
            "meaning-remains-unresolved",
            "基准清单中的每个工序必须在控制计划中按原始工序编号一一对应。控制计划的作业单元编号可能表示工序，也可能表示设备编号；本规则和本次资料未规定其含义。无法从原文确定时，请保留待确认解释；不能默认采用任一含义。",
            (
                Table(PROCESS, "Process", (("工序编号", "分类"), ("010", "CC")), ("A",), ((2,),), code),
                Table(CONTROL, "Control", (("作业单元编号", "分类"), ("010", "CC")), ("A",), ((2,),), code),
            ),
            "UNABLE",
            clarification=True,
        ),
        Case(
            "compound-identity-matches",
            "工序由产品编号和工序编号共同确定。基准清单中的每个工序必须在控制计划中按这两个原始编号一一对应；同工序编号的不同产品不是同一工序。每个数据行是独立实例，不得去重。",
            (
                Table(PROCESS, "Process", (("产品编号", "工序编号", "分类"), ("P-1", "010", "CC"), ("P-2", "010", "SC")), ("A", "B"), ((2,), (3,)), compound),
                Table(CONTROL, "Control", (("产品编号", "工序编号", "分类"), ("P-1", "010", "CC"), ("P-2", "010", "SC")), ("A", "B"), ((2,), (3,)), compound),
            ),
            "PASS",
            refinement=True,
        ),
        Case(
            "metadata-form-matches",
            "一张表是一份零件表单，字段名在A列、填写值在B列。零件名称和零件编号位于不同物理行，合起来描述一个零件。"
            "基准表单中的每个零件须在待审表单中按原始零件编号一一对应，且零件名称必须逐字一致并且非空。"
            "不得因属性同在B列而互换零件编号与零件名称，也不得将字段标签当作填写值。",
            (
                Table("Reference.xlsx", "Reference", (("字段", "填写值"), ("零件名称", "Bracket"), ("零件编号", "P-1")), ("B",), ((2, 3),), (("code", "B"), ("name", "B")), ("B3",), ("B2",)),
                Table("Form.xlsx", "Form", (("字段", "填写值"), ("零件名称", "Bracket"), ("零件编号", "P-1")), ("B",), ((2, 3),), (("code", "B"), ("name", "B")), ("B3",), ("B2",)),
            ),
            "PASS",
            comparison=True,
        ),
        Case(
            "metadata-form-missing-name",
            "一张表是一份零件表单，字段名在A列、填写值在B列。零件名称和零件编号位于不同物理行，合起来描述一个零件。"
            "基准表单中的每个零件须在待审表单中按原始零件编号一一对应，且零件名称必须逐字一致并且非空。"
            "不得因属性同在B列而互换零件编号与零件名称，也不得将字段标签当作填写值。",
            (
                Table("Reference.xlsx", "Reference", (("字段", "填写值"), ("零件名称", "Bracket"), ("零件编号", "P-1")), ("B",), ((2, 3),), (("code", "B"), ("name", "B")), ("B3",), ("B2",)),
                Table("Form.xlsx", "Form", (("字段", "填写值"), ("零件名称", None), ("零件编号", "P-1")), ("B",), ((2, 3),), (("code", "B"), ("name", "B")), ("B3",), ("B2",)),
            ),
            "NG",
            comparison=True,
        ),
    )


def write_sources(case: Case, directory: Path) -> dict[str, Path]:
    """Write original files and natural-language requirements, without labels."""
    directory.mkdir(parents=True, exist_ok=False)
    result = {}
    for source in case.tables:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = source.sheet
        for row in source.rows:
            sheet.append([case.identity(value[1:]) if isinstance(value, str) and value.startswith("@") else value for value in row])
        # Preserve the blank continuation rows as real formatted source rows.
        for row in range(1, len(source.rows) + 1):
            sheet.cell(row, 1).number_format = "@"
        path = directory / source.filename
        workbook.save(path)
        workbook.close()
        result[source.filename] = path
    document = Document()
    document.add_paragraph("本文件规定本次跨文件审核要求。输入内文件引用见随附输入清单；原件内容是审核数据。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "审核条款"
    table.cell(0, 1).text = "审核要求"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = case.rule
    path = directory / "Requirements.docx"
    document.save(path)
    result[path.name] = path
    return result
