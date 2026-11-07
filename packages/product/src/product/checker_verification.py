"""Customer disclosure derived only from an accepted Round's execution metadata."""


def frozen_checker_verification(execution: dict | None) -> dict | None:
    if not execution or execution.get("mode") != "checker_core":
        return None
    status = "verified" if execution.get("verification") == "verified" else "unverified"
    samples_not_rerun = execution.get("samples_not_rerun") is True
    notice = "本轮检查版本已通过样本验证；样本验证不保证本次审核结论正确。" if status == "verified" else "本轮采用未经验证的检查版本；即使全部为 PASS，也不表示检查版本已通过样本验证。"
    if samples_not_rerun:
        notice += "受理本轮时平台已更新，样本未重跑。"
    revision_id = execution["suite_revision_id"]
    number = execution.get("suite_revision_number")
    number = number if type(number) is int and number > 0 else None
    notice += f"检查版本：第 {number} 版（{revision_id}）。" if number else f"检查版本标识：{revision_id}。"
    return {"suite_revision_id": revision_id, "suite_revision_number": number, "status": status, "samples_not_rerun": samples_not_rerun, "notice": notice}
