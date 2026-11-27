"""What the auditor may put on record, and what is read again before it is believed.

A verdict is built from points: the things that must hold for the clause to pass. Each point is
met, unmet, na or unknown. The verdict's status has to follow from its points, so an unknown point
can never end as PASS. A point rests on citations (what a place in an original says or shows) and,
when it claims that something holds everywhere or is nowhere, on the places that were searched.

Nothing here judges whether a point is right. It checks that quotations read back from the
originals, that searched places were in fact looked at, and that the status follows from the points.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from auditor.originals import SPREADSHEETS, WORD, Original, Unreadable, parse_range, squeeze

STATUSES = ("PASS", "NG", "NA", "UNABLE")
RESULTS = ("met", "unmet", "na", "unknown")
STATE = Path(".audit")


def _lines(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


class Task:
    """One work directory: clauses, the batch's originals, what was looked at and what was recorded."""

    def __init__(self, work: Path | str = ".") -> None:
        self.work = Path(work)
        try:
            files = json.loads((self.work / "files.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Unreadable("当前目录不是审核工作目录（缺少 files.json）") from None
        rules = json.loads((self.work / "rules.json").read_text(encoding="utf-8")) if (self.work / "rules.json").is_file() else {"rules": []}
        self.files: dict[str, Path] = {item["name"]: self.work / item["path"] for item in files}
        self.clauses: list[str] = [rule["id"] for rule in rules["rules"]]
        self.state = self.work / STATE
        # read, audit, focus, challenge or arbitrate: which pass of the work a record belongs to.
        self.phase = os.environ.get("AUDIT_PHASE", "audit")
        # What is derived from the originals (renders, transcriptions) is shared by every auditor of the batch.
        self.cache = Path(os.environ.get("AUDIT_CACHE") or self.work / "scratch")
        # The dossier is the batch's too: a fact one auditor notes is there for the next.
        self.dossier = Path(os.environ.get("AUDIT_DOSSIER") or self.state / "notes.jsonl")
        self._originals: dict[str, Original] = {}

    def name(self, file: str) -> str:
        """A file's name in files.json, given that name or its path."""
        if file in self.files:
            return file
        for known, path in self.files.items():
            if Path(file) == path or Path(file).name == known or Path(file).name == path.name:
                return known
        raise Unreadable(f"本批没有这份文件：{file}；本批文件见 files.json")

    def original(self, file: str) -> Original:
        name = self.name(file)
        if name not in self._originals:
            self._originals[name] = Original(self.files[name], self.cache)
        return self._originals[name]

    def _append(self, name: str, entry: dict[str, Any]) -> None:
        self.state.mkdir(exist_ok=True)
        with (self.state / name).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # What was looked at

    def saw(self, file: str, **place: Any) -> None:
        """Record that a place was put in front of the auditor."""
        self._append("seen.jsonl", {"file": self.name(file), "phase": self.phase, **place})

    def looked(self, file: str, where: str) -> str | None:
        """None when ``where`` in ``file`` was looked at in full; otherwise what is missing."""
        name = self.name(file)
        original = self.original(name)
        # Each auditor answers for what it looked at itself, not for what an earlier pass read.
        seen = [entry for entry in _lines(self.state / "seen.jsonl") if isinstance(entry, dict) and entry.get("file") == name and entry.get("phase", self.phase) == self.phase]
        where = where.strip()
        kind, _, rest = where.partition(":")
        if kind == "find":
            return None if any(entry.get("kind") == "find" and squeeze(str(entry.get("text"))) == squeeze(rest) for entry in seen) else f"没有在这份文件里查找过「{rest}」"
        if kind in ("page", "pages"):
            match = re.fullmatch(r"(\d+)(?:-(\d+))?|\*", rest)
            if not match:
                return f"位置写法不对：{where}（应如 page:3、page:1-12、page:*）"
            first, last = (1, original.pages) if rest == "*" else (int(match.group(1)), int(match.group(2) or match.group(1)))
            if not 1 <= first <= last <= original.pages:
                return f"没有这些页：{where}（共 {original.pages} 页）"
            read = {entry.get("page") for entry in seen if entry.get("kind") == "page"}
            missing = [page for page in range(first, last + 1) if page not in read]
            return f"第 {'、'.join(map(str, missing[:20]))} 页没有整页看过（读文字层、整页渲染或整页转写才算）" if missing else None
        if where == "pictures":
            return None if any(entry.get("kind") == "pictures" for entry in seen) else "没有列出过这份文件的内嵌图片和图形（look pictures）"
        if kind == "image":
            return None if any(entry.get("kind") == "image" and entry.get("name") == rest for entry in seen) else f"没有取出看过图片 {rest}"
        if where == "paragraphs" and original.suffix in WORD:
            read = {number for entry in seen if entry.get("kind") == "paragraphs" for number in range(entry["start"], entry["end"] + 1)}
            return None if all(number in read for number in range(1, len(original.paragraphs) + 1)) else "段落没有读完（look paragraphs 读到末尾才算）"
        if kind == "table" and rest.isdigit() and original.suffix in WORD:
            if not 1 <= int(rest) <= len(original.tables):
                return f"没有表 {rest}"
            read = {number for entry in seen if entry.get("kind") == "table" and entry.get("table") == int(rest) for number in range(entry["start"], entry["end"] + 1)}
            return None if all(number in read for number in range(1, len(original.tables[int(rest) - 1]) + 1)) else f"表 {rest} 没有读完"
        if "!" in where and original.suffix in SPREADSHEETS:
            sheet_name, _, area = where.rpartition("!")
            sheet = original.sheet(sheet_name)
            if not sheet.rows:
                return None
            r1, c1, r2, c2 = (1, 1, sheet.rows, sheet.columns) if area == "*" else parse_range(area)
            r2, c2 = min(r2, sheet.rows), min(c2, sheet.columns)
            areas = [entry["area"] for entry in seen if entry.get("kind") == "cells" and entry.get("sheet") == sheet_name]
            for row in range(r1, r2 + 1):
                reach = c1
                for _top, left, _bottom, right in sorted((area for area in areas if area[0] <= row <= area[2]), key=lambda area: area[1]):
                    if left <= reach:
                        reach = max(reach, right + 1)
                if reach <= c2:
                    return f"「{sheet_name}」第 {row} 行没有读全（声明的范围是 {where}）"
            return None
        return f"位置写法不对：{where}（应如 工作表名!A5:Q250、工作表名!*、page:1-12、paragraphs、table:1、pictures、image:名称、find:文字）"

    # Citations

    def citation(self, item: Any) -> dict[str, Any] | str:
        """The citation as recorded, or why it is refused."""
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("file", "where", "quote")):
            return "每项证据要有 file、where、quote"
        where, quote = item["where"].strip(), item["quote"].strip()
        try:
            name = self.name(item["file"])
            text = self.original(name).text_at(where)
        except Unreadable as error:
            return str(error)
        # A stored citation says "verified": "seen"; one being submitted says "seen": true. Both are the same claim.
        if item.get("seen") is True or item.get("verified") == "seen" or text is None:
            # What is seen (a stamp, a signature, a drawn symbol, a scanned page) has no text to compare.
            if not where.startswith(("page:", "image:")):
                return "所见类证据（seen）只能引用 page: 或 image: 位置；单元格和段落要逐字引用原值"
            recorded: dict[str, Any] = {"file": name, "where": where, "quote": quote, "verified": "seen"}
            if isinstance(item.get("clip"), str):
                recorded["clip"] = item["clip"]
            return recorded
        if squeeze(quote) not in squeeze(text):
            return f"{where} 的原文里没有这段引文。该位置原文：{text[:300]!r}"
        return {"file": name, "where": where, "quote": quote, "verified": "text"}

    def _point(self, point: Any) -> tuple[dict[str, Any] | None, list[str]]:
        if not isinstance(point, dict) or not isinstance(point.get("need"), str) or not point["need"].strip():
            return None, ["每个要点要有 need（条款要求的一件事）"]
        problems: list[str] = []
        result = point.get("result")
        if result not in RESULTS:
            problems.append(f"result 只能是 {' / '.join(RESULTS)}")
        evidence = point.get("evidence") or []
        searched = point.get("searched") or []
        if not isinstance(evidence, list) or not isinstance(searched, list) or len(evidence) > 20 or len(searched) > 60:
            return None, [f"要点「{point['need'][:30]}」：evidence 最多 20 项，searched 最多 60 项。只留最能说明问题的几项，其余写进 why"]
        cited = []
        for number, item in enumerate(evidence, 1):
            outcome = self.citation(item)
            if isinstance(outcome, str):
                problems.append(f"第 {number} 项证据：{outcome}")
            else:
                cited.append(outcome)
        looked = []
        for item in searched:
            if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not isinstance(item.get("where"), str):
                problems.append("searched 的每一项要有 file 和 where")
                continue
            try:
                missing = self.looked(item["file"], item["where"])
                looked.append({"file": self.name(item["file"]), "where": item["where"].strip()})
            except Unreadable as error:
                missing = str(error)
            if missing:
                problems.append(f"声明查过 {item['file']} {item['where']}，但{missing}")
        if result == "met" and not cited:
            problems.append("met 至少要一项能在原件定位的证据")
        if result == "unmet" and not cited and not looked:
            problems.append("unmet 要么引用违反之处（evidence），要么列出查过而没有找到的范围（searched）")
        if result in ("unknown", "na") and not str(point.get("why") or "").strip():
            problems.append(f"{result} 要写 why：为什么确定不了，或为什么不适用")
        if problems:
            return None, [f"要点「{point['need'][:30]}」：{problem}" for problem in problems]
        return {"need": point["need"].strip(), "result": result, "why": str(point.get("why") or "").strip(), "evidence": cited, "searched": looked}, []

    def check(self, verdict: Any) -> tuple[dict[str, Any] | None, list[str]]:
        """The verdict as it will be recorded, or why it is refused."""
        if not isinstance(verdict, dict):
            return None, ["结论应是一个对象：clause、status、reason、points"]
        problems: list[str] = []
        clause, status, reason = verdict.get("clause"), verdict.get("status"), verdict.get("reason")
        if clause not in self.clauses:
            problems.append(f"没有这条条款：{clause}；本次条款：{'、'.join(self.clauses)}")
        if status not in STATUSES:
            problems.append(f"status 只能是 {' / '.join(STATUSES)}")
        if not isinstance(reason, str) or not reason.strip():
            problems.append("reason 不能为空")
        raw = verdict.get("points")
        if not isinstance(raw, list) or not raw or len(raw) > 30:
            return None, [*problems, "points 是 1 到 30 个要点：把条款拆成通过所必须成立的每一件事"]
        points = []
        for point in raw:
            checked, found = self._point(point)
            problems += found
            if checked:
                points.append(checked)
        if not problems:
            results = [point["result"] for point in points]
            # The status follows from the points; this is where an unknown is kept from becoming PASS.
            if status == "PASS" and (set(results) - {"met", "na"} or "met" not in results):
                problems.append("PASS 要求每个要点都是 met 或 na，且至少一个 met；有 unknown 就是 UNABLE，有 unmet 就是 NG")
            if status == "NG" and "unmet" not in results:
                problems.append("NG 至少要有一个 unmet 的要点；读不到或确定不了是 UNABLE，不是 NG")
            if status == "NA" and set(results) != {"na"}:
                problems.append("NA 要求每个要点都是 na")
            if status == "UNABLE" and "unknown" not in results:
                problems.append("UNABLE 至少要有一个 unknown 的要点，并在 why 里写明缺什么")
            if status == "UNABLE" and "unmet" in results:
                problems.append("已经有 unmet 的要点（证据证明不符合）时，结论是 NG，其余确定不了的要点照样写 unknown")
        if problems:
            return None, problems
        return {"clause": clause, "status": status, "reason": str(reason).strip(), "points": points}, []

    # Records

    def record(self, verdicts: Any) -> list[tuple[str, list[str]]]:
        """Check and store each verdict; returns (clause, problems) for each, problems empty when stored."""
        outcomes = []
        for verdict in verdicts if isinstance(verdicts, list) else [verdicts]:
            accepted, problems = self.check(verdict)
            clause = str(verdict.get("clause")) if isinstance(verdict, dict) else "?"
            if accepted:
                self._append("verdicts.jsonl", {**accepted, "phase": self.phase})
            else:
                self._append("refused.jsonl", {"phase": self.phase, "verdict": verdict, "problems": problems})
            outcomes.append((clause, problems))
        return outcomes

    def recorded(self, phase: str | None = None) -> dict[str, dict[str, Any]]:
        """The last stored verdict of each clause (of one pass, or overall), read again from the originals."""
        latest: dict[str, Any] = {}
        for verdict in _lines(self.state / "verdicts.jsonl"):
            if isinstance(verdict, dict) and verdict.get("clause") in self.clauses and phase in (None, verdict.get("phase")):
                latest[verdict["clause"]] = verdict
        # The file sits where the auditor works, so nothing in it is believed without a second reading.
        found = {}
        current = self.phase
        try:
            for clause, verdict in latest.items():
                self.phase = verdict.get("phase") or current
                accepted, _problems = self.check(verdict)
                if accepted:
                    found[clause] = {**accepted, "phase": verdict.get("phase")}
        finally:
            self.phase = current
        return found

    def refused(self, phase: str | None = None) -> int:
        return sum(1 for entry in _lines(self.state / "refused.jsonl") if isinstance(entry, dict) and phase in (None, entry.get("phase")))

    # The dossier: facts about the batch, each with a citation, shared by every audit of the batch

    def note(self, topic: str, fact: str, citation: Any) -> str | None:
        outcome = self.citation(citation)
        if isinstance(outcome, str):
            return outcome
        if not topic.strip() or not fact.strip():
            return "topic 和 fact 不能为空"
        self.dossier.parent.mkdir(parents=True, exist_ok=True)
        with self.dossier.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"topic": topic.strip(), "fact": fact.strip(), **outcome, "by": f"{self.work.name}/{self.phase}"}, ensure_ascii=False) + "\n")
        return None

    def notes(self) -> list[dict[str, Any]]:
        """The dossier, each fact read again from its source; the same fact noted twice is kept once."""
        kept: dict[tuple[str, str, str], dict[str, Any]] = {}
        for note in _lines(self.dossier):
            if isinstance(note, dict) and not isinstance(self.citation(note), str):
                kept.setdefault((note["file"], note["where"], squeeze(note["fact"])), note)
        return list(kept.values())

    def remember(self, lesson: str) -> None:
        """A lesson about how to read this kind of document, for the next audit of the kind. Never a verdict."""
        self._append("lessons.jsonl", {"lesson": lesson.strip()[:600]})

    def lessons(self) -> list[str]:
        return [str(entry["lesson"]) for entry in _lines(self.state / "lessons.jsonl") if isinstance(entry, dict) and entry.get("lesson")]

    def uphold(self, clause: str, reason: str) -> str | None:
        if clause not in self.clauses:
            return f"没有这条条款：{clause}"
        if not reason.strip():
            return "要写明复看了哪里、为什么维持"
        self._append("upheld.jsonl", {"clause": clause, "reason": reason.strip()})
        return None

    def upheld(self) -> dict[str, str]:
        return {entry["clause"]: entry["reason"] for entry in _lines(self.state / "upheld.jsonl") if isinstance(entry, dict) and entry.get("clause") in self.clauses}
