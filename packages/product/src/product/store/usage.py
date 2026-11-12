"""Read-only file obligations from the existing usage ledger."""

import csv
import io
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _time(value) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


SETTLEMENT_LABELS = {"charged": "已扣费", "pending": "待扣费", "processing": "扣费处理中", "unknown": "待确认", "trial": "免费额度已扣"}


def usage_page(rows: list[dict], query: dict, organization: dict, free: dict, archive: dict, as_of: datetime, *, every_entry: bool = False) -> dict:
    """One page of entries, or with `every_entry` every entry matching the query (for export)."""
    local = as_of.astimezone(ZoneInfo("Asia/Shanghai"))
    through = local.date()
    start = through - timedelta(days=int(query["range"][:-1]) - 1) if query["range"] != "all" else None
    month = local.strftime("%Y-%m")
    previous_month = (local.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    groups: dict[tuple, list[dict]] = {}
    unattributed = []
    for row in rows:
        if not row["round_id"] or not row["file_version_id"] or not row["task_id"] or not row["filename"] or not row.get("accepted_at"):
            unattributed.append(row)
        else:
            groups.setdefault((row["round_id"], row["file_version_id"]), []).append(row)

    entries = []
    for (round_id, version_id), slices in groups.items():
        row = min(slices, key=lambda item: item["created_at"])
        accepted_at = _time(row["accepted_at"])
        acceptance_times = {_time(row["accepted_at"]) for row in slices}
        # 500 at acceptance; a committed deep result adds one 250-point surcharge.
        audit_points = sum(item["consumed_points"] for item in slices if (item.get("charge") or "audit") == "audit")
        surcharge_points = sum(item["consumed_points"] for item in slices if item.get("charge") == "deep_surcharge")
        if accepted_at is None or acceptance_times != {accepted_at} or audit_points != 500 or surcharge_points not in {0, 250} or len({row["ai_item_id"] for row in slices}) != 1:
            unattributed.extend(slices)
            continue
        points = audit_points + surcharge_points
        reference = {"task_id": row["task_id"], "round_id": round_id, "file_version_id": version_id, "result_attempt": row["result_attempt"]}
        history = archive.get((row["task_id"], version_id))
        free_points = sum(item["consumed_points"] for item in slices if item["is_free_trial"])
        paid = [item for item in slices if not item["is_free_trial"]]
        statuses = {item["external_status"] for item in paid}
        orders = []
        for item in paid:
            debited = _time(item.get("debited_at"))
            order = item.get("debit_order_id")
            if isinstance(order, str) and order.strip() and debited and accepted_at <= debited <= as_of:
                orders.append({"order_id": order, "confirmed_at": debited.isoformat()})
        debited_at = max((item["confirmed_at"] for item in orders), key=lambda value: datetime.fromisoformat(value)) if paid and len(orders) == len(paid) else None
        if not paid:
            settlement = {"state": "trial", "reason": None}
        elif debited_at:
            settlement = {"state": "charged", "reason": None}
        elif any(item.get("debited_at") is not None or item.get("debit_order_id") is not None for item in paid):
            settlement = {"state": "unknown", "reason": "实际扣费凭据不完整或时间不一致，请核对原凭据。"}
        elif statuses == {"pending"}:
            settlement = {"state": "pending", "reason": "付费部分尚未上报飞书。"}
        elif statuses == {"reporting"}:
            settlement = {"state": "processing", "reason": "正在等待飞书用量回执。"}
        else:
            settlement = {"state": "unknown", "reason": "飞书已接收用量上报，实际扣费仍待对账。" if statuses == {"confirmed"} else "飞书用量上报结果待核对。"}
        entries.append(
            {
                "usage_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ppap-usage:{organization['id']}:{round_id}:{version_id}")),
                "occurred_at": accepted_at.isoformat(),
                "debited_at": debited_at,
                "filename": row["filename"],
                "audit_sequence": history["sequence"] if history else None,
                "initiator": row["initiator"],
                "points": points,
                "allocation": {"free_points": free_points, "paid_points": points - free_points, "reason": None},
                "orders": orders,
                "order_reason": None if debited_at else "全部使用免费额度，无飞书订单。" if not paid else "尚未取得可核对的飞书订单号。",
                "settlement": settlement,
                "reference": reference,
                "document_id": history["document_id"] if history else None,
                "audit_access": {"state": "allowed", "reason": None} if history else {"state": "denied", "reason": "尚未取得这次审核的文件档案访问许可。"},
            }
        )

    def local_day(row):
        occurred = _time(row.get("occurred_at")) or _time(row.get("accepted_at")) or _time(row["created_at"])
        return occurred.astimezone(local.tzinfo).date()

    def in_range(row):
        day = local_day(row)
        return day <= through and (start is None or day >= start)

    filtered = sorted(
        (
            row
            for row in entries
            if in_range(row)
            and query["search"].casefold() in (row["filename"] + " " + row["initiator"]).casefold()
            and (not query.get("settlement") or row["settlement"]["state"] == query["settlement"])
        ),
        key=lambda row: (row["occurred_at"], row["usage_id"]),
        reverse=True,
    )
    omitted = [row for row in unattributed if in_range(row)]
    monthly_unknown = any(local_day(row).strftime("%Y-%m") == month for row in unattributed)
    monthly = [row for row in entries if local_day(row).strftime("%Y-%m") == month]
    previous_unknown = any(local_day(row).strftime("%Y-%m") == previous_month for row in unattributed)
    previous = sum(1 for row in entries if local_day(row).strftime("%Y-%m") == previous_month)
    uncertain = monthly_unknown or any(row["settlement"]["state"] in {"unknown", "processing"} for row in monthly)
    unknown = {"value": None, "reason": "本月受理的费用还有扣费结果待核对，已扣／待扣合计暂不确定。"}
    charged = sum(row["allocation"]["free_points"] + (row["allocation"]["paid_points"] if row["settlement"]["state"] == "charged" else 0) for row in monthly)
    pending = sum(row["allocation"]["paid_points"] for row in monthly if row["settlement"]["state"] == "pending")
    offset = (query["page"] - 1) * 10
    return {
        "organization_id": organization["id"],
        "organization_name": organization["name"],
        "query": query,
        "summary": {
            "month": month,
            "timezone": "Asia/Shanghai",
            "as_of": as_of.isoformat(),
            "consumption_count": {"value": None, "reason": "本月还有无法准确拆分到文件的历史费用。"} if monthly_unknown else {"value": len(monthly), "reason": None},
            "previous_consumption_count": {"value": None, "reason": "上月还有无法准确拆分到文件的历史费用。"} if previous_unknown else {"value": previous, "reason": None},
            "charged_points": unknown if uncertain else {"value": charged, "reason": None},
            "pending_points": unknown if uncertain else {"value": pending, "reason": None},
            **free,
        },
        "entries": filtered if every_entry else filtered[offset : offset + 10],
        "total": len(filtered),
        "from_date": start.isoformat() if start else None,
        "through_date": through.isoformat(),
        "notices": [],
        "unattributed": {
            "slice_count": len(omitted),
            "points": sum(row["consumed_points"] for row in omitted),
            "reason": "此时间范围还有未关联到单份文件的历史费用，已保留在原账本；不能据此表判断全部历史消费。" if omitted else None,
        },
    }


def _cell(value: str) -> str:
    # A spreadsheet must not run a filename or name as a formula.
    return "'" + value if value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value


def usage_csv(page: dict) -> str:
    """UTF-8 with BOM so Excel reads the Chinese headers and names."""
    zone = ZoneInfo(page["summary"]["timezone"])
    out = io.StringIO()
    out.write("\ufeff")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["时间", "订单号", "文档名称", "扣费状态", "发起人", "扣费点数"])
    for row in page["entries"]:
        occurred = datetime.fromisoformat(row["occurred_at"]).astimezone(zone).strftime("%Y-%m-%d %H:%M")
        orders = "；".join(order["order_id"] for order in row["orders"])
        writer.writerow([occurred, _cell(orders), _cell(row["filename"]), SETTLEMENT_LABELS[row["settlement"]["state"]], _cell(row["initiator"]), row["points"]])
    return out.getvalue()
