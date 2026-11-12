"""Part submission warrant originals (U17): the content one image transcription returns.

The UAES corpus warrant (CFG-1001) is a scan. Its page is transcribed once into this schema: what is printed
or written in each field and which boxes are marked. The model transcribes; the clauses decide in code.
The transcription also says whether an ink seal is seen; Paddle's seal detection locates one.
"""

from . import PER_PAGE, STITCHED

DOCUMENT = ((), (), ())

PDF_SPECS = (STITCHED, PER_PAGE)

_TEXT = {"type": ["string", "null"]}
_MARK = {"type": ["boolean", "null"]}


def _object(**properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


# A field: whether its label is printed on the form, and what is filled in ("" when blank, null when unreadable).
_FIELD = _object(present=_MARK, value=_TEXT)
# A choice: whether its label is printed, and every option in form order with its mark.
_CHOICE = _object(present=_MARK, options={"type": "array", "items": _object(label=_TEXT, checked=_MARK)})

# Field names, with the label printed on the form and the clause's Chinese term.
IDENTITY = {
    "part_name": "零件名称（Part Name）",
    "customer_part_number": "客户零件号（Cust. Part Number）",
    "drawing_number": "图纸号（Shown on Drawing No.）",
    "organization_part_number": "供应商零件号（Org. Part Number）",
    "engineering_change_level": "工程更改级别（Engineering Change Level / Technical change status）",
    "engineering_change_level_date": "工程更改级别的日期（Date）",
    "additional_engineering_changes": "其他工程更改（Additional Engineering Changes）",
    "additional_engineering_changes_date": "其他工程更改的日期（Date）",
}
SAFETY = ("safety_government_regulation", "安全及政府条例（Safety and/or Government Regulation）")
SUPPLIER_AREA = ("organization_area", "供方制造厂资料（Organization Manufacturing Information）")
SUPPLIER_NAME = ("supplier_name", "公司名称（Supplier Name）")
CUSTOMER_AREA = ("customer_area", "买方资料（Customer Submittal Information）")
CUSTOMER = {
    "customer_name_division": "采购方及部门（Customer Name/Division）",
    "buyer_code": "采购方代码（Buyer/Buyer Code）",
    "application": "应用（Application）",
}
SUBSTANCES = ("substances_of_concern_reported", "是否报告了受客户关注的材料信息（Substances of Concern）")
IMDS = ("imds_submission", "以IMDS或者其它客户格式递交（Submitted by IMDS or other customer format）")
SIGNATURE = ("supplier_authorized_signature", "供方代表签字（Supplier Authorized Signature）")
REASON = ("reason_for_submission", "提交原因（Reason for Submission）")
REASON_OTHER = ("reason_other_text", "“其它----请说明（Other - please specify）”旁边的说明")
LEVEL = ("requested_submission_level", "要求提交的等级（Requested Submission Level）")
SEAL = ("ink_seal", "页面上用印泥盖的实际印章")

SCHEMA = _object(
    **{name: _FIELD for name in IDENTITY},
    **{SAFETY[0]: _CHOICE},
    **{SUPPLIER_AREA[0]: _object(present=_MARK), SUPPLIER_NAME[0]: _FIELD},
    **{CUSTOMER_AREA[0]: _object(present=_MARK)},
    **{name: _FIELD for name in CUSTOMER},
    **{SUBSTANCES[0]: _CHOICE, IMDS[0]: _FIELD},
    **{SIGNATURE[0]: _object(present=_MARK, handwritten=_MARK, value=_TEXT)},
    **{REASON[0]: _CHOICE, REASON_OTHER[0]: _FIELD, LEVEL[0]: _CHOICE},
    **{SEAL[0]: _MARK},
)

PROMPT = (
    "这是一页零件提交保证书（PSW）的原件图像。请把图中可见内容按 JSON Schema 转写，只转写，不判断是否合格。\n"
    "present：表单上印有该栏位、区域或选项组的标签为 true，没有为 false，看不清为 null。\n"
    "value：照原样转写栏中填写的内容（打印或手写均可），栏位空白写空字符串，看不清写 null。\n"
    "options：按表单顺序列出该组的每一个选项；实际被勾选（√、×、涂黑等）为 true，明确未勾选为 false，看不清为 null。\n"
    "供方代表签字：handwritten 只在签字栏中有实际手写签名时为 true；打印姓名、电子/数字签名文字或空白为 false；value 转写签字栏中的可见文字。\n"
    "ink_seal：页面上任何位置有用印泥盖的实际印章为 true；表头印刷的品牌标志、电子/数字签名文字框、勾选框不算印章；确认没有为 false，看不清为 null。\n"
    "字段与表单标签的对应：\n"
    + "\n".join(
        f"{name}: {label}"
        for name, label in (
            *IDENTITY.items(),
            SAFETY,
            SUPPLIER_AREA,
            SUPPLIER_NAME,
            CUSTOMER_AREA,
            *CUSTOMER.items(),
            SUBSTANCES,
            IMDS,
            SIGNATURE,
            REASON,
            REASON_OTHER,
            LEVEL,
        )
    )
)
