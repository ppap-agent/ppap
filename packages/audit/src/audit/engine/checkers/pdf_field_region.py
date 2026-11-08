"""Page geometry for labelled form regions.

Shared by the Excel whole-field reader and historical v4 visual facts.
Field names select a clip. They do not decide PASS, NG, or UNABLE.
"""

from __future__ import annotations

import fitz

DISPOSITION_FIELDS = frozenset({"disposition_visible", "approved_checked", "conditional_checked", "rejected_checked"})


def _pdf_section(page, starts, stops):
    anchors = [box for label in starts for box in page.search_for(label)]
    if not anchors:
        return page.rect
    # Translations on the same heading form one anchor. Distinct headings on
    # one page retain the whole page rather than dropping a business form.
    if max(box.y0 for box in anchors) - min(box.y0 for box in anchors) > max(box.height for box in anchors) * 2:
        return page.rect
    anchor = fitz.Rect(anchors[0])
    for box in anchors[1:]:
        anchor |= box
    ends = [box.y0 for label in stops for box in page.search_for(label) if box.y0 > anchor.y1]
    if not ends:
        return page.rect
    return fitz.Rect(max(0, anchor.x0 - 12), max(0, anchor.y0 - 8), page.rect.x1, min(ends) - 2)


def pdf_field_region(page, fields):
    requested = set(fields or ())
    primary = page.rect
    if requested and requested.issubset({"submission_any_checked", "submission_other_present"}):
        primary = _pdf_section(page, ("提交原因", "REASON FOR SUBMISSION"), ("提交结果", "SUBMISSION RESULTS", "供方声明", "DECLARATION"))
    elif requested and requested.issubset({"dimensional_checked", "appearance_checked", "material_functional_checked", "statistical_checked", "requirements_yes_checked"}):
        primary = _pdf_section(page, ("提交结果", "SUBMISSION RESULTS"), ("供方声明", "DECLARATION", "解释/说明", "EXPLANATION/COMMENTS", "经授权的供方代表签字"))
    elif requested and requested.issubset({"approval_date", "comments_present"}):
        anchors = page.search_for("认可情况")
        if len(anchors) == 1:
            anchor = anchors[0]
            primary = fitz.Rect(max(0, anchor.x0 - 12), max(0, anchor.y0 - 8), page.rect.x1, page.rect.y1)
    elif requested and requested.issubset({"authorized_signature_present"}):
        anchors = page.search_for("经授权的供方代表签字")
        if anchors:
            anchor = anchors[0]
            dates = [box for box in page.search_for("日期") if box.x0 > anchor.x1 and abs(box.y0 - anchor.y0) < anchor.height * 2]
            right = min((box.x0 for box in dates), default=page.rect.x1)
            primary = fitz.Rect(max(0, anchor.x0 - 4), max(0, anchor.y0 - anchor.height), right, min(page.rect.y1, anchor.y1 + anchor.height * 0.6))
    elif requested and requested.issubset(DISPOSITION_FIELDS):
        anchors = page.search_for("认可情况")
        if anchors:
            anchor = anchors[0]
            primary = fitz.Rect(max(0, anchor.x0 - 8), max(0, anchor.y0 - anchor.height * 2), page.rect.x1, min(page.rect.y1, anchor.y1 + anchor.height * 2))
    elif requested == {"authorization_company_stamp_present"}:
        anchors = page.search_for("公司名称")
        if anchors:
            anchor = anchors[-1]
            statements = [box.y0 for box in page.search_for("特此声明") if box.y0 < anchor.y0]
            top = max(statements, default=anchor.y0 - anchor.height * 12)
            primary = fitz.Rect(max(0, anchor.x0 - 12), max(0, top), page.rect.x1, page.rect.y1)
    elif requested and requested.issubset({"psw_stamp_present", "psw_stamp_ending_text"}):
        anchors = page.search_for("公司名称")
        if anchors:
            anchor = anchors[-1]
            following = [box.y0 for label in ("认可情况", "仅供理想使用") for box in page.search_for(label) if box.y0 > anchor.y1]
            bottom = min(following, default=page.rect.y1) - 2
            primary = fitz.Rect(max(0, anchor.x0 - 8), max(0, anchor.y0 - 8), page.rect.x1, bottom)
    return primary
