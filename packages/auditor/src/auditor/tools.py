"""`look`: the auditor's eyes and hands on the originals.

Each command reads one place and says what it showed and what it left unshown, so that a partial
reading is never mistaken for the whole. `look submit` is the only command that records anything.
Run from the work directory (files.json, rules.json, files/).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import warnings
from pathlib import Path
from typing import Any

from auditor.originals import IMAGES, SPREADSHEETS, WORD, Original, Sheet, Unreadable, cell_name, column_letter, parse_range, shown, squeeze, transcribe
from auditor.verdicts import STATE, Task

# About what one reading puts in front of the model.
BUDGET = 14000


def _flat(text: str, limit: int = 0) -> str:
    text = text.replace("\r", "").replace("\n", "⏎")
    return text[:limit] + "…" if limit and len(text) > limit else text


def _areas(sheet: Sheet, r1: int, c1: int, r2: int, c2: int) -> list[str]:
    return [f"{cell_name(a, b)}:{cell_name(c, d)}" for a, b, c, d in sheet.merged if a <= r2 and c >= r1 and b <= c2 and d >= c1]


def outline(task: Task, args: argparse.Namespace) -> None:
    original = task.original(args.file)
    print(f"{original.path.name}")
    if original.suffix in SPREADSHEETS:
        for sheet in original.sheets:
            notes = [f"{sheet.rows} 行 × {sheet.columns} 列（到 {column_letter(sheet.columns) or 'A'} 列）", f"合并区域 {len(sheet.merged)} 处"]
            if sheet.formulas:
                notes.append(f"无缓存值的公式 {len(sheet.formulas)} 格（值未知，不是空白）")
            if sheet.hidden:
                notes.append("隐藏的工作表")
            print(f"  工作表「{sheet.name}」：{'，'.join(notes)}")
        _drawings(original, brief=True)
        print("表格的图形、图章、签名不在单元格里：用 look pictures 列出，用 look render 看整页。")
    elif original.suffix in WORD:
        filled = sum(1 for text in original.paragraphs if text.strip())
        print(f"  段落 {len(original.paragraphs)} 段（有内容 {filled} 段）")
        for number, table in enumerate(original.tables, 1):
            print(f"  表 {number}：{len(table)} 行 × {max((len(row) for row in table), default=0)} 列")
        _drawings(original, brief=True)
    elif original.suffix in IMAGES:
        print("  图片文件：没有文字层。用 look render 查看，用 look ocr 辅助转写。")
    else:
        scans = [page for page in range(1, original.pages + 1) if not original.page_text(page).strip()]
        print(f"  共 {original.pages} 页；有文字层 {original.pages - len(scans)} 页，扫描页（无文字层）{len(scans)} 页")
        if scans:
            print(f"  扫描页：{_ranges(scans)}。扫描页要 look render 看图，或 look ocr 辅助转写。")
        for page in range(1, original.pages + 1):
            text = original.page_text(page).strip()
            tables = "、".join(f"{len(table['rows'])} 行 × {max((len(row) for row in table['rows']), default=0)} 列" for table in original.page_tables(page))
            notes = [f"{len(text)} 字" if text else "扫描页", f"表格 {tables}" if tables else "没有识别出表格", f"图片 {len(original.page_pictures(page))} 张"]
            print(f"  第 {page} 页：{'，'.join(notes)}。{_flat(text, 60)}")
        print("look page 按表格的行列读一页；页上的图片（图章、签名、勾选框、贴图的尺寸）用 look pictures 列出。")


def _ranges(numbers: list[int]) -> str:
    spans: list[str] = []
    start = last = numbers[0]
    for number in [*numbers[1:], numbers[-1] + 2]:
        if number != last + 1:
            spans.append(str(start) if start == last else f"{start}–{last}")
            start = number
        last = number
    return "、".join(spans)


def cells(task: Task, args: argparse.Namespace) -> None:
    original = task.original(args.file)
    if not args.sheet and len(original.sheets) > 1:
        raise Unreadable(f"请用 --sheet 指定工作表：{'、'.join(sheet.name for sheet in original.sheets)}")
    sheet = original.sheet(args.sheet) if args.sheet else original.sheets[0]
    if not sheet.rows:
        print(f"工作表「{sheet.name}」的单元格没有内容（图形和图片另见 look pictures）。")
        return
    r1, c1, r2, c2 = parse_range(args.range) if args.range else (1, 1, sheet.rows, sheet.columns)
    r2, c2 = min(r2, sheet.rows), min(c2, sheet.columns)
    lines: list[str] = []
    blank: list[int] = []
    used, row = 0, r1
    while row <= r2 and row - r1 < args.rows:
        parts = []
        for column in range(c1, c2 + 1):
            if sheet.anchor(row, column) != (row, column):
                continue
            if (row, column) in sheet.values:
                parts.append(f"{cell_name(row, column)}={_flat(shown(sheet.values[(row, column)]))}")
            elif (row, column) in sheet.formulas:
                parts.append(f"{cell_name(row, column)}=〔公式无缓存值，值未知：{_flat(sheet.formulas[(row, column)], 60)}〕")
        line = " | ".join(parts)
        if used and used + len(line) > BUDGET:
            break
        if parts:
            if blank:
                lines.append(f"（第 {_ranges(blank)} 行在所选列内为空）")
                blank = []
            lines.append(line)
            used += len(line)
        else:
            blank.append(row)
        row += 1
    if blank:
        lines.append(f"（第 {_ranges(blank)} 行在所选列内为空）")
    last = row - 1
    print(f"「{sheet.name}」{cell_name(r1, c1)}:{cell_name(last, c2)}（本表共 {sheet.rows} 行 {sheet.columns} 列）")
    print("\n".join(lines))
    if merged := _areas(sheet, r1, c1, last, c2):
        print(f"合并区域（值只写在左上角，整块同值）：{'、'.join(merged[:60])}{' 等' if len(merged) > 60 else ''}")
    try:
        here = [item for item in original.drawings[0] if item.get("sheet") == sheet.name and item.get("cell")]
    except Unreadable:
        here = []
    inside = [item for item in here if r1 <= parse_range(item["cell"])[0] <= last and c1 <= parse_range(item["cell"])[1] <= c2]
    if inside:
        print("这个区域里的图片（左上角所在的格）：" + "；".join(f"{item['cell']} {_pictured(original, item['name'])}" for item in inside[:30]) + (" 等" if len(inside) > 30 else ""))
    unshown = []
    if last < r2:
        unshown.append(f"第 {last + 1}–{r2} 行。继续：look cells {args.file!r} --sheet {sheet.name!r} --range {cell_name(last + 1, c1)}:{cell_name(r2, c2)}")
    if args.range and (r1 > 1 or r2 < sheet.rows or c1 > 1 or c2 < sheet.columns):
        unshown.append("所选区域之外的行列")
    print("未显示：" + "；".join(unshown) if unshown else "本表的单元格内容已全部显示。")
    task.saw(args.file, kind="cells", sheet=sheet.name, area=[r1, c1, last, c2])


def _hits(task: Task, name: str, needle: str, blind: set[str]) -> list[str]:
    original = task.original(name)
    label = original.path.name
    hits: list[str] = []
    if original.suffix in SPREADSHEETS:
        for sheet in original.sheets:
            hits += [f"{label} {sheet.name}!{cell_name(*key)}={_flat(shown(value), 120)}" for key, value in sorted(sheet.values.items()) if needle in squeeze(shown(value)).lower()]
        try:
            hits += [f"{label} 图形文字（{shape['sheet']} {shape['cell']} 附近）：{_flat(shape['text'], 120)}" for shape in original.drawings[1] if needle in squeeze(shape.get("text") or "").lower()]
        except Unreadable:
            pass
    elif original.suffix in WORD:
        hits += [f"{label} paragraph:{number}：{_flat(text, 120)}" for number, text in enumerate(original.paragraphs, 1) if needle in squeeze(text).lower()]
        hits += [
            f"{label} table:{t}:{r}:{c}：{_flat(text, 120)}"
            for t, table in enumerate(original.tables, 1)
            for r, row in enumerate(table, 1)
            for c, text in enumerate(row, 1)
            if needle in squeeze(text).lower()
        ]
    elif original.suffix in IMAGES:
        blind.add(f"{label}（图片）")
    else:
        for page in range(1, original.pages + 1):
            text = original.page_text(page)
            if not text.strip():
                read = original.transcription(page)
                if read and needle in squeeze(read).lower():
                    hits.append(f"{label} page:{page}（扫描页，转写里有，须看图确认）")
                else:
                    blind.add(f"{label} 第 {page} 页（扫描）")
                continue
            cells = [
                f"{label} page:{page} 表{t} 行{r} 列{c}：{_flat(cell, 120)}"
                for t, table in enumerate(original.page_tables(page), 1)
                for r, row in enumerate(table["rows"], 1)
                for c, cell in enumerate(row, 1)
                if needle in squeeze(cell).lower()
            ]
            if cells:
                hits += cells
            elif needle in (squeezed := squeeze(text).lower()):
                at = squeezed.index(needle)
                hits.append(f"{label} page:{page}：…{squeeze(text)[max(0, at - 30) : at + len(needle) + 30]}…")
            if original.page_pictures(page):
                blind.add(f"{label} 第 {page} 页的图片")
    return hits


def find(task: Task, args: argparse.Namespace) -> None:
    names = list(task.files) if args.file == "*" else [args.file]
    blind: set[str] = set()
    missed: list[str] = []
    for text in args.text:
        needle = squeeze(text).lower()
        if not needle:
            continue
        hits: list[str] = []
        for name in names:
            try:
                hits += _hits(task, name, needle, blind)
                task.saw(name, kind="find", text=text)
            except Unreadable as error:
                blind.add(f"{name}（{error}）")
        if not hits:
            missed.append(text)
            continue
        print(f"「{text}」{len(hits)} 处" + ("（只列前 25 处；缩小到一份文件或换更长的词）" if len(hits) > 25 else ""))
        print("\n".join(f"  {hit}" for hit in hits[:25]))
    if missed:
        print(f"没有命中：{'、'.join(missed)}")
    if blind:
        listed = sorted(blind)
        print(f"查不到的范围（没有文字可查，不代表不存在）：{'；'.join(listed[:30])}{' 等' if len(listed) > 30 else ''}")
    print("图片、图章和扫描页里的字查不到；没有命中不等于原件里没有。")


def _square(original: Original, page: int, rect: tuple[float, float, float, float]) -> str:
    """Where a rectangle sits on the page, in the squares look render --grid draws."""
    area = original._pdf[page - 1].rect
    x, y = (rect[0] + rect[2]) / 2 / area.width, (rect[1] + rect[3]) / 2 / area.height
    return f"{chr(65 + min(9, max(0, int(x * 10))))}{min(10, max(1, int(y * 10) + 1))}"


def _pictured(original: Original, name: str) -> str:
    """A picture as it is shown inside a reading: its name and, when one was already made, its transcription."""
    read = original.picture_reading(name)
    return f"〔图 {name}，转写（可能有错，关键内容看图）：{_flat(read, 90)}〕" if read else f"〔图 {name}〕"


def page(task: Task, args: argparse.Namespace) -> None:
    original = task.original(args.file)
    text = original.page_text(args.page)
    if not text.strip():
        print(f"第 {args.page} 页没有文字层（扫描页或图片）。用 look render 看图；look ocr 给出转写，帮你知道这页写了什么、在哪。")
        if read := original.transcription(args.page):
            print(f"这一页已有的转写（视觉模型读的，可能有错字，关键内容要看图确认）：\n{read[:BUDGET]}")
        return
    tables = original.page_tables(args.page)
    if args.raw or not tables:
        part = text[args.offset : args.offset + BUDGET]
        print(f"{original.path.name} page:{args.page}（共 {original.pages} 页，本页 {len(text)} 字，文字层原顺序）")
        print(part)
        rest = len(text) - args.offset - len(part)
        if rest <= 0:
            task.saw(args.file, kind="page", page=args.page)
        print(
            f"未显示：本页还有 {rest} 字。继续：look page {args.file!r} {args.page} --raw --offset {args.offset + len(part)}"
            if rest > 0
            else "本页文字层已全部显示。文字顺序不等于表格行序，行列关系要对照 look render 的页面图。"
        )
        return
    placed: dict[tuple[int, int, int], list[str]] = {}
    loose = []
    for item in original.page_pictures(args.page):
        if item["cell"]:
            placed.setdefault(item["cell"], []).append(item["name"])
        else:
            loose.append(item)
    print(f"{original.path.name} page:{args.page}（共 {original.pages} 页）。按表格的行列读：每格写成 列号=内容，空格不列出，〔图 名称〕是放在格里的图片。")
    used, whole = 0, not args.table and args.start == 1
    for index, table in enumerate(tables, 1):
        if args.table and index != args.table:
            continue
        rows = table["rows"]
        print(f"表 {index}：{len(rows)} 行 × {max((len(row) for row in rows), default=0)} 列")
        blank: list[int] = []
        number = args.start if args.table == index else 1
        while number <= len(rows):
            cells = {column: [_flat(cell)] for column, cell in enumerate(rows[number - 1], 1) if cell}
            for (t, r, column), names in placed.items():
                if (t, r) == (index, number):
                    cells.setdefault(column, []).extend(_pictured(original, name) for name in names)
            parts = [f"{column}={' '.join(texts)}" for column, texts in sorted(cells.items())]
            line = f"行{number} | " + " | ".join(parts)
            if used and used + len(line) > BUDGET:
                break
            if parts:
                if blank:
                    print(f"（行 {_ranges(blank)} 为空）")
                    blank = []
                print(line)
                used += len(line)
            else:
                blank.append(number)
            number += 1
        if blank:
            print(f"（行 {_ranges(blank)} 为空）")
        if number <= len(rows):
            whole = False
            print(f"未显示：表 {index} 的行 {number}–{len(rows)} 以及之后的内容。继续：look page {args.file!r} {args.page} --table {index} --start {number}")
            break
    else:
        if outside := original.page_outside(args.page):
            print(f"表外文字：\n{outside[:3000]}")
        if loose:
            print("表外图片：" + "、".join(f"{_pictured(original, item['name'])}（网格 {_square(original, args.page, item['rect'])}）" for item in loose))
        if whole:
            task.saw(args.file, kind="page", page=args.page)
            print("本页的表格和表外文字已全部显示。合并的格只在左上角有内容；表格线画得不规整时行列可能错位，拿不准就 look render 看页面图。图片里的内容要看图。")
        else:
            print(f"只显示了一部分；整页读完才能声明查过 page:{args.page}。")


def paragraphs(task: Task, args: argparse.Namespace) -> None:
    original = task.original(args.file)
    total, used, number = len(original.paragraphs), 0, args.start
    while number <= total and used < BUDGET:
        if text := original.paragraphs[number - 1].strip():
            print(f"paragraph:{number} {text}")
            used += len(text)
        number += 1
    task.saw(args.file, kind="paragraphs", start=args.start, end=number - 1)
    print(f"未显示：第 {number}–{total} 段。继续：look paragraphs {args.file!r} --start {number}" if number <= total else f"共 {total} 段，已全部显示（空段略去）。表格另用 look table。")


def table(task: Task, args: argparse.Namespace) -> None:
    original = task.original(args.file)
    if not 1 <= args.table <= len(original.tables):
        raise Unreadable(f"{original.path.name} 没有表 {args.table}（共 {len(original.tables)} 张表）")
    rows, used, number = original.tables[args.table - 1], 0, args.start
    while number <= len(rows) and used < BUDGET:
        line = " | ".join(f"table:{args.table}:{number}:{column}={_flat(text)}" for column, text in enumerate(rows[number - 1], 1) if text.strip())
        print(line or f"（第 {number} 行为空）")
        used += len(line)
        number += 1
    task.saw(args.file, kind="table", table=args.table, start=args.start, end=number - 1)
    print(
        f"未显示：第 {number}–{len(rows)} 行。继续：look table {args.file!r} {args.table} --start {number}"
        if number <= len(rows)
        else f"表 {args.table} 共 {len(rows)} 行，已全部显示。合并的格在每个位置重复同一内容。"
    )


def _drawings(original: Original, brief: bool = False) -> None:
    try:
        pictures, shapes = original.drawings
    except Unreadable as error:
        print(f"  {error}")
        return
    if brief:
        print(f"  内嵌图片 {len(pictures)} 张，绘制的图形 {sum(1 for shape in shapes if shape['kind'] == '图形')} 个、连线 {sum(1 for shape in shapes if shape['kind'] == '连线')} 条")
        return
    print(f"{original.path.name}：内嵌图片 {len(pictures)} 张")
    for item in pictures:
        if "rect" in item:
            x0, y0, x1, y1 = item["rect"]
            at = f"第 {item['page']} 页 " + (f"表{item['cell'][0]} 行{item['cell'][1]} 列{item['cell'][2]}" if item["cell"] else "表外") + f"，网格 {_square(original, item['page'], item['rect'])}"
            small = "，很小，多半是勾选框或符号" if max(x1 - x0, y1 - y0) < 9 else ""
            print(f"  image:{item['name']}  位置：{at}{small}")
        else:
            print(f"  image:{item['name']}  位置：{(item['sheet'] + ' ' + (item['cell'] or '')) if item['sheet'] else '未知'}")
    drawn = [shape for shape in shapes if shape["kind"] == "图形"]
    print(f"绘制的图形 {len(drawn)} 个，连线 {len(shapes) - len(drawn)} 条")
    for shape in drawn[:200]:
        print(f"  {shape['sheet']} {shape['cell']} 附近：{shape.get('shape') or '图形'}　文字：{_flat(shape.get('text') or '', 80) or '（无）'}")
    print("图片用 look picture 取出后查看；图形的形状和位置关系用 look render 看整页。")


def pictures(task: Task, args: argparse.Namespace) -> None:
    _drawings(task.original(args.file))
    task.saw(args.file, kind="pictures")


def picture(task: Task, args: argparse.Namespace) -> None:
    out = task.original(args.file).picture(args.name.removeprefix("image:"))
    task.saw(args.file, kind="image", name=args.name.removeprefix("image:"))
    print(f"已取出：{out}（用读取工具查看这张图）。引用位置写 image:{args.name.removeprefix('image:')}")


def _clip(value: str | None) -> tuple[float, float, float, float] | None:
    """A part of a page: four fractions (left,top,right,bottom) or grid squares as drawn by --grid (C3 or C3:E5)."""
    if not value:
        return None
    if match := re.fullmatch(r"([A-Ja-j])(10|[1-9])(?::([A-Ja-j])(10|[1-9]))?", value.strip()):
        left, top = ord(match.group(1).upper()) - 65, int(match.group(2)) - 1
        right, bottom = (ord(match.group(3).upper()) - 65, int(match.group(4)) - 1) if match.group(3) else (left, top)
        return min(left, right) / 10, min(top, bottom) / 10, (max(left, right) + 1) / 10, (max(top, bottom) + 1) / 10
    try:
        left_, top_, right_, bottom_ = (float(part) for part in value.split(","))
    except ValueError:
        raise Unreadable("clip 写网格（如 C3 或 C3:E5，对应 --grid 画出的格子），或 左,上,右,下 四个 0 到 1 的比例") from None
    return left_, top_, right_, bottom_


def render(task: Task, args: argparse.Namespace) -> None:
    original = task.original(args.file)
    clip = _clip(args.clip)
    out = original.render(args.page, args.dpi, clip, grid=args.grid)
    if not clip:
        task.saw(args.file, kind="page", page=args.page)
    print(f"已渲染：{out}（用读取工具查看这张图）。{original.path.name} 共 {original.pages} 页，这是第 {args.page} 页{'的局部' if clip else ''}。")
    if original.suffix in SPREADSHEETS | WORD:
        print("这是 LibreOffice 排版后的页面，用来看图形、图章和版面；引用单元格内容仍以 look cells 读到的原值为准。")
    print("看不清时先加 --grid 画出 A–J × 1–10 的格子，再用 --clip C3:E5 放大那几格，可同时提高 --dpi。")


def ocr(task: Task, args: argparse.Namespace) -> None:
    original = task.original(args.file)
    clip = _clip(args.clip)
    if args.picture:
        out = original.picture(args.picture.removeprefix("image:"))
        task.saw(args.file, kind="image", name=args.picture.removeprefix("image:"))
    else:
        out = original.render(args.page, args.dpi, clip)
    print(transcribe(out, args.ask or ""))
    if not args.picture and not clip:
        task.saw(args.file, kind="page", page=args.page)
    print(f"——以上是视觉模型对 {out} 的转写，可能有错字（尤其编号里的字母和数字）。它帮你找位置；关键内容放大后自己看图确认，引用时按所见写（seen）。")


def submit(task: Task, args: argparse.Namespace) -> None:
    try:
        verdicts = json.loads(sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Unreadable(f"结论不是合法的 JSON（{type(error).__name__}：{str(error)[:160]}）") from None
    for clause, problems in task.record(verdicts):
        print(f"{clause}：已记录" if not problems else f"{clause}：被退回，没有记录\n  - " + "\n  - ".join(problems))
    status(task, args)


def status(task: Task, _args: argparse.Namespace) -> None:
    recorded = task.recorded()
    upheld = task.upheld() if task.phase == "challenge" else {}
    for clause in task.clauses:
        mark = recorded[clause]["status"] if clause in recorded else "还没有结论"
        if task.phase == "challenge" and clause in recorded:
            mark += "（已改判）" if recorded[clause].get("phase") == "challenge" else "（已维持）" if clause in upheld else "（待质证）"
        print(f"  {clause}：{mark}")
    if task.phase == "challenge":
        waiting = [clause for clause in task.clauses if clause not in upheld and recorded.get(clause, {}).get("phase") != "challenge"]
        print(f"还没有质证的条款：{'、'.join(waiting)}" if waiting else "全部条款都质证过了。")
        return
    missing = [clause for clause in task.clauses if clause not in recorded]
    print(f"还没有结论的条款：{'、'.join(missing)}" if missing else "全部条款都有结论了。")


def note(task: Task, args: argparse.Namespace) -> None:
    problem = task.note(args.topic, args.fact, {"file": args.file, "where": args.where, "quote": args.quote, "seen": args.seen})
    print(f"没有记下：{problem}" if problem else f"已记入案卷：{args.topic} — {args.fact}")


def notes(task: Task, args: argparse.Namespace) -> None:
    kept = [item for item in task.notes() if (not args.file or item["file"] == task.name(args.file)) and (not args.topic or args.topic in item["topic"] or args.topic in item["fact"])]
    for item in kept:
        print(f"[{item['file']} {item['where']}] {item['topic']}：{item['fact']}")
    print(f"案卷里符合的事实 {len(kept)} 条。每条的出处都重新核对过；用到关键事实时仍要自己到出处看一眼。")


def _clock(task: Task) -> None:
    """Tell the auditor how much time is left: a verdict not submitted in time is a clause not audited."""
    try:
        left, total = float(os.environ["AUDIT_DEADLINE"]) - time.time(), float(os.environ["AUDIT_SECONDS"])
    except (KeyError, ValueError):
        return
    if task.phase == "read":
        print(f"〔剩余 {max(0, int(left))} 秒〕" if left > total * 0.25 else f"〔剩余 {max(0, int(left))} 秒：把已经确认的事实记入案卷，然后结束。〕")
    elif left > total * 0.4:
        print(f"〔剩余 {int(left)} 秒〕")
    else:
        print(f"〔剩余 {max(0, int(left))} 秒：现在就按已经读到的写出结论并提交；还确定不了的要点写 unknown 并说明缺什么。没有提交等于没有审。〕")


def remember(task: Task, args: argparse.Namespace) -> None:
    task.remember(args.lesson)
    print("已记下这条读法经验。只记怎么读这类文件，不记本件的结论。")


def uphold(task: Task, args: argparse.Namespace) -> None:
    problem = task.uphold(args.clause, args.reason)
    print(f"没有记下：{problem}" if problem else f"{args.clause}：维持原结论")
    status(task, args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="look", description="查看原件、提交结论")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, run: Any, summary: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=summary)
        sub.set_defaults(run=run)
        return sub

    command("outline", outline, "一份文件有什么：工作表、页、段落、图片").add_argument("file")
    sub = command("cells", cells, "读一张工作表的单元格")
    sub.add_argument("file")
    sub.add_argument("--sheet")
    sub.add_argument("--range", help="如 A1:Q60；不写则从头读")
    sub.add_argument("--rows", type=int, default=80, help="一次最多读多少行")
    sub = command("find", find, "在一份文件（或 * 表示本批全部文件）里查找文字，可一次查多个词")
    sub.add_argument("file")
    sub.add_argument("text", nargs="+")
    sub = command("page", page, "读 PDF 的一页：有表格按行列读，没有表格读文字层")
    sub.add_argument("file")
    sub.add_argument("page", type=int)
    sub.add_argument("--table", type=int, help="只读这一张表")
    sub.add_argument("--start", type=int, default=1, help="配合 --table：从第几行读起")
    sub.add_argument("--raw", action="store_true", help="读文字层的原顺序")
    sub.add_argument("--offset", type=int, default=0)
    sub = command("paragraphs", paragraphs, "读 Word 的段落")
    sub.add_argument("file")
    sub.add_argument("--start", type=int, default=1)
    sub = command("table", table, "读 Word 的一张表")
    sub.add_argument("file")
    sub.add_argument("table", type=int)
    sub.add_argument("--start", type=int, default=1)
    command("pictures", pictures, "列出内嵌图片和绘制的图形").add_argument("file")
    sub = command("picture", picture, "取出一张内嵌图片")
    sub.add_argument("file")
    sub.add_argument("name")
    for name, run, summary in (("render", render, "把一页渲染成图片来看"), ("ocr", ocr, "让视觉模型转写一页或一张图")):
        sub = command(name, run, summary)
        sub.add_argument("file")
        sub.add_argument("--page", type=int, default=1)
        sub.add_argument("--dpi", type=int, default=110 if name == "render" else 200)
        sub.add_argument("--clip", help="放大局部：网格如 C3:E5，或 左,上,右,下 的比例")
        if name == "render":
            sub.add_argument("--grid", action="store_true", help="画出 A–J × 1–10 的格子，便于指位置")
        if name == "ocr":
            sub.add_argument("--picture", help="转写一张内嵌图片，而不是一页")
            sub.add_argument("--ask", help="换一个转写要求")
    sub = command("note", note, "把一条有出处的事实记入案卷")
    sub.add_argument("--topic", required=True, help="这条事实是关于什么的，如 客户零件号、版本、表头位置")
    sub.add_argument("--fact", required=True)
    sub.add_argument("--file", required=True)
    sub.add_argument("--where", required=True)
    sub.add_argument("--quote", required=True)
    sub.add_argument("--seen", action="store_true", help="所见类：图章、签名、扫描页上的内容")
    sub = command("notes", notes, "读案卷：同批审核员记下的、带出处的事实")
    sub.add_argument("file", nargs="?")
    sub.add_argument("--topic")
    command("remember", remember, "记下一条这类文件的读法经验").add_argument("lesson")
    sub = command("uphold", uphold, "质证后维持一条结论")
    sub.add_argument("clause")
    sub.add_argument("reason")
    command("submit", submit, "提交结论：JSON 文件的路径，或 - 表示从标准输入读").add_argument("path")
    command("status", status, "哪些条款还没有结论")

    warnings.filterwarnings("ignore")
    args = parser.parse_args(argv)
    started = time.time()
    outcome = "ok"
    try:
        task = Task()
        args.run(task, args)
        _clock(task)
    except Unreadable as error:
        outcome = "unreadable"
        print(f"读不了：{error}")
    except BrokenPipeError:
        # The auditor piped the reading into something that stopped reading (head): not a failure of the reading.
        outcome = "cut"
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    finally:
        # What was looked at, in order: the record of how far the reading went.
        try:
            (Path(".") / STATE).mkdir(exist_ok=True)
            entry = {key: value for key, value in vars(args).items() if key != "run" and value is not None}
            with (Path(".") / STATE / "looks.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**entry, "phase": os.environ.get("AUDIT_PHASE", "audit"), "outcome": outcome, "seconds": round(time.time() - started, 2)}, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return 0 if outcome == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
