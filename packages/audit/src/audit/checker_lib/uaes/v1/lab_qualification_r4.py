"""LAB:1: read the certificate's actual standard, holder and issued marks, not every body named in its text."""

import re

from .lab_qualification import LAB_NAME, UaesLabQualificationAgentR3, UaesLabQualificationDocxAgentR3, compact

_MARKS = {
    "cnas": "CNAS（含中国合格评定国家认可委员会全称）",
    "dakks": "DAkkS（含 Deutsche Akkreditierungsstelle）",
    "a2la": "A2LA",
    "ukas": "UKAS",
    "ilac_mra": "ILAC-MRA互认标志（不是单独的ILAC字样）",
    "other": "其他机构的认可标识，原件同时明确说明该签发机构是ILAC互认成员；成员资格无法从证书确认时返回未知，不靠常识猜",
}
_ISSUERS = {compact(x) for x in ("出具实验室", "出具实验室名称", "检测实验室名称", "检测机构", "报告签发机构", "Issuing Laboratory", "Testing Laboratory")}
_HOLDERS = {compact(x) for x in LAB_NAME if x != "机构名称"}


class CertificateRoles:
    STOP_ON_EXPIRY = True

    @staticmethod
    def labelled_names(text, roles):
        names = set()
        for line in text.splitlines():
            field, *rest = re.split(r"\s*[:：]\s*", line.strip(), maxsplit=1)
            if rest and rest[0] and compact(field) in roles:
                names.add(rest[0])
        return names

    def holder(self, text):
        names = self.labelled_names(text, _HOLDERS)
        return next(iter(names)) if len(names) == 1 else None

    def report_names_holder(self, text, name):
        # A client name anywhere in the report is not evidence of the issuing lab.
        return {compact(x) for x in self.labelled_names(text, _ISSUERS | {compact("实验室名称"), compact("Laboratory Name")})} == {compact(name)}

    def accreditation(self, text, problems):
        # Read together with the seal, on the original certificate. No additional request per branch.
        fields = {
            "iso17025": "原件明确以ISO/IEC 17025认可本证书所列实验室，而非引用试验方法、其他机构资质、未取得或待申请的认可。",
            "lab_name": "原件写明本证书实际认可的实验室名称且可读。空栏、占位词、客户名称或发证机关名称不能代替。",
        }
        for key, label in _MARKS.items():
            fields[key] = f"原件上是本证书为所列实验室实际签发的认可标识或等效签发字样：{label}。否定、示例、申请、无关机构的标识均不算。"
        return None, fields

    def read_marks(self, values, unreadable, problems, open_):
        if values.get("iso17025") is False:
            problems.append("本证书未按ISO/IEC 17025认可所列实验室")
        elif values.get("iso17025") is not True:
            (problems if "iso17025" in unreadable else open_).append("本证书的认可标准无法辨识" if "iso17025" in unreadable else "本证书的认可标准尚未核实")
        for key, label in _MARKS.items():
            if values.get(key) is True:
                return label.split("（")[0] if key != "other" else "其他明确的ILAC互认成员机构"
        if all(values.get(key) is False for key in _MARKS):
            problems.append("原件没有本证书向所列实验室实际签发的任一认可标识")
        elif all(values.get(key) is False or key in unreadable for key in _MARKS):
            problems.append("本证书的认可标识无法辨识")
        else:
            open_.append("尚未核实本证书的实际认可机构或互认标识")
            return "尚未核实"
        return None


class UaesLabQualificationAgentR4(CertificateRoles, UaesLabQualificationAgentR3):
    pass


class UaesLabQualificationDocxAgentR4(CertificateRoles, UaesLabQualificationDocxAgentR3):
    pass
