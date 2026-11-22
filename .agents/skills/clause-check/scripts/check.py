"""Validate a clause review against its source and render human- and machine-readable reports.

    python3 check.py X01-1.review.json [--report X01-1.report.md] [--data X01-1.report.json]

This is deliberately NOT a theorem prover. It validates provenance and report structure;
it cannot prove that an interpretation of natural language is correct.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "clause-check-review-v1"
REPORT_SCHEMA = "clause-check-report-v1"
ROLES = {"necessary", "sufficient", "prohibition", "applicability", "exception", "definition", "outcome"}
KINDS = {"ambiguity", "undefined", "gap", "conflict"}
VERDICTS = {"通过", "不通过", "原文未定"}
ORDINALS = "一二三四五六七八九十"


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def resolve(path: Path, name: str) -> Path | None:
    source = Path(name)
    if source.is_absolute():
        return source if source.is_file() else None
    for root in (Path.cwd(), *path.resolve().parents):
        candidate = root / source
        if candidate.is_file():
            return candidate
    return None


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def require_text(value: object, location: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{location} 缺少实质文字")
        return ""
    return value.strip()


def quoted(value: object, source: str, location: str, errors: list[str]) -> str:
    quote = require_text(value, location, errors)
    if quote and norm(quote) not in norm(source):
        errors.append(f"{location} 不属于所引用的原文：「{quote}」")
    return quote


def load_source(review_path: Path, review: dict) -> tuple[dict, Path]:
    target = review.get("source", "")
    if not isinstance(target, str) or "#" not in target:
        raise ValueError("source 应为 rules.json#条款ID")
    filename, rule_id = target.rsplit("#", 1)
    path = resolve(review_path, filename)
    if not path:
        raise ValueError(f"找不到规则原件 {filename}")
    document = json.loads(path.read_text(encoding="utf-8"))
    matches = [item for item in document.get("rules", []) if item.get("id") == rule_id]
    if len(matches) != 1 or not nonempty(matches[0].get("rule_to_check")):
        raise ValueError(f"{filename} 中找不到唯一的有效条款 {rule_id}")
    return {
        "id": rule_id,
        "title": document.get("title", ""),
        "category": matches[0].get("category", ""),
        "text": matches[0]["rule_to_check"],
        "source": target,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }, path


def check_review(review_path: Path, review: dict, clause: dict) -> tuple[list[str], list[dict]]:
    errors: list[str] = []
    original = clause["text"]
    if review.get("schema") != SCHEMA:
        errors.append(f"schema 必须是 {SCHEMA}")

    requirements = review.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("requirements 必须逐句列出原文要求，不能留空")
    else:
        for i, req in enumerate(requirements, 1):
            at = f"requirements[{i}]"
            if not isinstance(req, dict):
                errors.append(f"{at} 必须是对象")
                continue
            quoted(req.get("quote"), original, f"{at}.quote", errors)
            if req.get("role") not in ROLES:
                errors.append(f"{at}.role 不在允许的条件关系中")
            require_text(req.get("scope"), f"{at}.scope", errors)
            require_text(req.get("meaning"), f"{at}.meaning", errors)

    policies = review.get("policies", [])
    if not isinstance(policies, list):
        errors.append("policies 必须是数组")
        policies = []
    for i, policy in enumerate(policies, 1):
        at = f"policies[{i}]"
        if not isinstance(policy, dict):
            errors.append(f"{at} 必须是对象")
            continue
        name = require_text(policy.get("source"), f"{at}.source", errors)
        file = resolve(review_path, name) if name else None
        if file is None:
            errors.append(f"{at} 找不到明确引用的政策文件 {name}")
        else:
            quoted(policy.get("quote"), file.read_text(encoding="utf-8"), f"{at}.quote", errors)

    excluded = review.get("excluded", [])
    if not isinstance(excluded, list):
        errors.append("excluded 必须是数组")
        excluded = []
    for i, item in enumerate(excluded, 1):
        at = f"excluded[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{at} 必须是对象")
            continue
        require_text(item.get("issue"), f"{at}.issue", errors)
        require_text(item.get("reason"), f"{at}.reason", errors)
        quoted(item.get("quote"), original, f"{at}.quote", errors)

    findings = review.get("findings", [])
    if not isinstance(findings, list):
        return [*errors, "findings 必须是数组"], []
    seen: set[str] = set()
    normalized: list[dict] = []
    for i, item in enumerate(findings, 1):
        at = f"findings[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{at} 必须是对象")
            continue
        code = require_text(item.get("id"), f"{at}.id", errors)
        kind = item.get("kind")
        if kind not in KINDS:
            errors.append(f"{at}.kind 只能是 ambiguity/undefined/gap/conflict")
            continue
        pattern = {"ambiguity": r"[1-9]\d*", "undefined": r"[1-9]\d*\.[1-9]\d*", "gap": r"G[1-9]\d*", "conflict": r"C[1-9]\d*"}[kind]
        if not re.fullmatch(pattern, code):
            errors.append(f"{at}.id 与 {kind} 的编号格式不符")
        if code in seen:
            errors.append(f"重复发现编号 {code}")
        seen.add(code)
        quotes = item.get("quotes")
        if not isinstance(quotes, list) or not quotes:
            errors.append(f"{at}.quotes 必须至少引用一处原文")
            quotes = []
        for j, quote in enumerate(quotes, 1):
            quoted(quote, original, f"{at}.quotes[{j}]", errors)
        title = require_text(item.get("title"), f"{at}.title", errors)
        question = require_text(item.get("question"), f"{at}.question", errors)
        if question and not question.endswith(("？", "?")):
            errors.append(f"{at}.question 必须是一个可答复的问句")
        example = require_text(item.get("example"), f"{at}.example", errors)
        challenge = require_text(item.get("challenge"), f"{at}.challenge", errors)
        if challenge and len(challenge) < 12:
            errors.append(f"{at}.challenge 必须解释反例为什么没有违反原文其他明确要求")
        entry = {
            "id": f"{clause['id']}#{code}",
            "clause": clause["id"],
            "kind": "open" if kind == "undefined" else kind,
            "title": title,
            "question": question,
            "quote": quotes,
            "example": example,
            "challenge": challenge,
        }
        if kind == "ambiguity":
            readings = item.get("readings")
            if not isinstance(readings, list) or not 2 <= len(readings) <= len(ORDINALS):
                errors.append(f"{at}.readings 必须包含至少两种、最多十种合法理解")
                readings = []
            outcomes: set[str] = set()
            clean = []
            for j, reading in enumerate(readings, 1):
                loc = f"{at}.readings[{j}]"
                if not isinstance(reading, dict):
                    errors.append(f"{loc} 必须是对象")
                    continue
                label = require_text(reading.get("label"), f"{loc}.label", errors)
                meaning = require_text(reading.get("meaning"), f"{loc}.meaning", errors)
                basis = require_text(reading.get("basis"), f"{loc}.basis", errors)
                verdict = reading.get("verdict")
                if verdict not in VERDICTS:
                    errors.append(f"{loc}.verdict 必须是通过、不通过或原文未定")
                else:
                    outcomes.add(verdict)
                clean.append({"ordinal": ORDINALS[j - 1], "label": label, "says": meaning, "basis": basis, "verdict": verdict})
            if len(outcomes) < 2:
                errors.append(f"{at} 各种理解对同一个例子的判定没有差异，应删除歧义")
            entry["readings"] = clean
            entry["verdict"] = "；".join(f"按理解{r['ordinal']}判{r['verdict']}" for r in clean) + "。"
        resolution = item.get("resolution")
        if resolution is not None:
            if not isinstance(resolution, dict):
                errors.append(f"{at}.resolution 必须是对象")
            else:
                require_text(resolution.get("answer"), f"{at}.resolution.answer", errors)
                require_text(resolution.get("source"), f"{at}.resolution.source", errors)
                entry["resolution"] = resolution
        normalized.append(entry)

    questions = [entry["question"] for entry in normalized]
    if len(set(questions)) != len(questions):
        errors.append("重复问题：多个发现问了同一件事")
    return errors, normalized


def render(clause: dict, review: dict, findings: list[dict]) -> str:
    lines = [
        f"# {clause['id']} 条款语义检查报告",
        "",
        f"- **规则文件**：{clause['title']}：{clause['category']}（{clause['id']}）",
        f"- **规则原文**：{' '.join(clause['text'].split())}",
        f"- **结论**：{len(findings)} 处候选问题；结构与原文引用已校验，语义仍须人工审阅。",
        "",
    ]
    if findings:
        lines.extend(["## 逐条发现", ""])
    for row in findings:
        lines.extend([f"### {row['id'].split('#', 1)[1]}. {row['title']}", "", f"原文：「{'」「'.join(row['quote'])}」", "", f"例子：{row['example']}", ""])
        if row["kind"] == "ambiguity":
            for reading in row["readings"]:
                lines.append(f"- 理解{reading['ordinal']}（{reading['label']}）：{reading['says']}（该例判{reading['verdict']}）")
            lines.append("")
        elif row["kind"] == "gap":
            lines.extend(["原文没有明确规定这个例子的判定结论。", ""])
        elif row["kind"] == "conflict":
            lines.extend(["原文对这个例子给出了相互冲突的结论。", ""])
        lines.extend([f"反例复核：{row['challenge']}", ""])
        if row.get("resolution"):
            lines.extend([f"**已答复**：{row['resolution']['answer']}（来源：{row['resolution']['source']}）", ""])
        else:
            lines.extend([f"**请确认**：{row['question']}", ""])
    excluded = review.get("excluded", [])
    if excluded:
        lines.extend(["## 已排除的疑似问题（不进入问卷）", ""])
        for row in excluded:
            lines.append(f"- **{row['issue']}**：{row['reason']}")
        lines.append("")
    if not findings:
        lines.append("未发现需要客户确认的条款表述问题。\n")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args(argv)
    review_path: Path = args.review
    prefix = review_path.name.removesuffix(".review.json") if review_path.name.endswith(".review.json") else review_path.stem
    report_path = args.report or review_path.with_name(prefix + ".report.md")
    data_path = args.data or review_path.with_name(prefix + ".report.json")
    # Invalid input must not leave an old, apparently valid report for the questionnaire importer.
    report_path.unlink(missing_ok=True)
    data_path.unlink(missing_ok=True)
    try:
        raw = review_path.read_bytes()
        review = json.loads(raw)
        if not isinstance(review, dict):
            raise ValueError("审查文件必须是 JSON 对象")
        clause, _ = load_source(review_path, review)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[输入] {error}", file=sys.stderr)
        return 2
    problems, findings = check_review(review_path, review, clause)
    if problems:
        for problem in problems:
            print(f"[校验] {problem}", file=sys.stderr)
        return 1
    payload = {
        "schema": REPORT_SCHEMA,
        "clause": clause,
        "review_sha256": hashlib.sha256(raw).hexdigest(),
        "findings": findings,
        "excluded": review.get("excluded", []),
        # check_review already refused a policy whose file cannot be found.
        "policy_sha256": {policy["source"]: hashlib.sha256(Path(str(resolve(review_path, policy["source"]))).read_bytes()).hexdigest() for policy in review.get("policies", [])},
    }
    report_path.write_text(render(clause, review, findings), encoding="utf-8")
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    answered = sum(bool(item.get("resolution")) for item in findings)
    print(f"检查完成：{len(findings) - answered} 处待确认、{answered} 处已答复、{len(review.get('excluded', []))} 处已排除；写入 {report_path} 和 {data_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
