"""Part submission warrant originals (U17); labels as printed on the UAES corpus warrant (CFG-1001, scanned).

Nothing on the scanned warrant is a table: each clause asks the image of the original one yes/no question
per field, naming the clause's Chinese term and the label printed on the form.
"""

from . import PER_PAGE, STITCHED

DOCUMENT = ((), (), ())

PDF_SPECS = (STITCHED, PER_PAGE)

_FILLED = "栏中实际填写了内容（打印或手写均可）；栏位空白或不存在为false"

IDENTITY = {
    "part_name": f"“零件名称 / Part Name”{_FILLED}。",
    "customer_part": f"“客户零件号 / Cust. Part Number”{_FILLED}。",
    "drawing_number": f"“图纸号 / Shown on Drawing No.”{_FILLED}。",
    "supplier_part": f"“供应商零件号 / Org. Part Number”{_FILLED}。",
    "change_level": f"“工程更改级别 / Engineering Change Level（Technical change status）”{_FILLED}。",
    "change_date": "“工程更改级别 / Technical change status”同一行的“日期 / Date”栏中实际填写了日期；空白为false。",
    "other_change": f"“其他工程更改 / Additional Engineering Changes”{_FILLED}。",
    "other_date": "“其他工程更改 / Additional Engineering Changes”同一行的“日期 / Date”栏中实际填写了日期；空白为false。",
    "safety_one": "“安全及政府条例 / Safety and/or Government Regulation”的 Yes、No 两个选项中恰好有一个被勾选；都未勾选或都勾选为false。",
}

PARTIES = {
    "supplier_company": "存在“供方制造厂资料 / Organization Manufacturing Information”区域，且其中公司名称（Supplier Name）栏实际填写了内容；否则为false。",
    "buyer_area": "存在“买方资料 / Customer Submittal Information”区域；否则为false。",
    "buyer_name": f"买方资料中“采购方及部门 / Customer Name/Division”{_FILLED}。",
    "buyer_code": f"买方资料中“采购方代码 / Buyer/Buyer Code”{_FILLED}。",
    "application": f"买方资料中“应用 / Application”{_FILLED}。",
    "materials_one": "“是否报告了受客户关注的材料信息 / Has customer-required Substances of Concern information been reported?”的选项（Yes、No、n/a）中恰好有一个被勾选；都未勾选或多个勾选为false。",
    "imds": f"“以IMDS或者其它客户格式递交 / Submitted by IMDS or other customer format”{_FILLED}。",
}

SIGN_OFF = {
    "signature_field": "存在“供方代表签字 / Supplier Authorized Signature”栏；不存在为false。",
    "handwriting": "“供方代表签字 / Supplier Authorized Signature”栏中有实际手写签名；打印姓名或空白为false。",
}

# Asked on its own: a seal is a separate object anywhere on the page.
SEAL = {
    "seal": (
        "页面上盖有实际印章：用印泥盖到表单上的印迹（公章、业务章等，常见红色或蓝色，内有单位名称文字，常与表格线或文字叠压）。"
        "页面最上方表头里印刷的品牌Logo（如椭圆形、方形的车企标志）是表单印刷内容，不是印章；勾选框与叉号、手写签名、电子/数字签名文字框也不是印章。"
        "除这些之外页面上没有印泥印迹为false。"
    ),
}

REASON = {
    "reason_any": "“提交原因 / Reason for Submission”区域至少有一个选项被勾选；全部未勾选为false。",
    "other_explained": "如果勾选了“其它----请说明 / Other - please specify”，其旁边的说明栏实际填写了内容；没有勾选该项时为true；勾选了但说明栏空白为false。",
}

LEVEL = {
    "level_one": "“要求提交的等级 / Requested Submission Level”的 Level 1 至 Level 5 中恰好有一个被勾选；都未勾选或多个勾选为false。",
}
