"""Turn clause-check reports into a rule-confirmation questionnaire, load it into the product database, and turn
the answers exported from there into a backfill list.

    python3 questionnaire.py build <工作目录> <整理文件.json> <导入文件.json>
    python3 questionnaire.py load <导入文件.json> <租户编号>      > 导入.sql
    python3 questionnaire.py export <导入文件.json> <租户编号>    > 导出.sql
    python3 questionnaire.py close <导入文件.json> <租户编号>     > 关闭.sql
    python3 questionnaire.py answers <导出文件.json> <工作目录> [<回填清单.md>]

`build` reads validated `<条款序号>.report.json` files, checking each against its `.review.json` source. Each
unanswered ambiguity, undefined case, gap or conflict is one finding, named `<条款 id>#<处>` (`CP:8#1`, `CP:8#8.1`, `CP:8#G1`,
`CP:8#C1`). The curation file says which findings a common question covers, which were settled by an answer already
relayed, which go back to the customer for a conclusion, and the questions about the rulebook's general notes;
every other finding becomes a card of its own.

`load`, `export` and `close` print SQL for the enterprise whose Feishu tenant key is given, run with `psql` as the
database's migration role (the product itself never writes questionnaires): `load` adds the questionnaire and its
cards in one transaction and refuses a second load of the same version; `export` prints every answerer's revisions
of every card as one JSON value (`psql -At`); `close` makes it read only.

`answers` reads that export and lists, per clause file, every answer to every finding with its answerer and time,
and where answers differ. It never writes `.review.json` files: choosing among different answers is not the script's
decision.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
import hashlib

from check import REPORT_SCHEMA, check_review, load_source, resolve
from datetime import datetime
from pathlib import Path

NUMBERS = "一二三四五六七八九十"
GROUPS = ("common", "conclusion", "general", "clause")
RELAYED = "relayed"  # the choice that confirms the answer already relayed on a card
VERDICT_OPTIONS = [{"key": "PASS", "label": "通过"}, {"key": "NG", "label": "不通过"}, {"key": "UNABLE", "label": "待审核（交人工）"}]


def findings(directory: Path, rule_version: str) -> tuple[list[dict], dict[str, str]]:
    """Load checked structured reports; refuse edits that made the reports stale."""
    items: list[dict] = []
    names: dict[str, str] = {}
    paths = sorted(directory.glob("*.report.json"))
    if not paths:
        raise SystemExit("[报告] 没有 *.report.json；先运行 check.py")
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        review_path = path.with_name(path.name.removesuffix(".report.json") + ".review.json")
        if not review_path.is_file():
            raise SystemExit(f"[报告] {path.name} 缺审查原件 {review_path.name}")
        raw = review_path.read_bytes()
        review = json.loads(raw)
        clause, _ = load_source(review_path, review)
        errors, expected = check_review(review_path, review, clause)
        if errors:
            raise SystemExit(f"[报告] {path.name} 审查原件校验失败：" + "；".join(errors))
        if (
            payload.get("schema") != REPORT_SCHEMA
            or payload.get("review_sha256") != hashlib.sha256(raw).hexdigest()
            or payload.get("clause") != clause
            or payload.get("findings") != expected
        ):
            raise SystemExit(f"[报告] {path.name} 已过期或被手工修改；请重跑 check.py")
        for filename, digest in payload.get("policy_sha256", {}).items():
            policy_file = resolve(review_path, filename)
            if not policy_file or hashlib.sha256(policy_file.read_bytes()).hexdigest() != digest:
                raise SystemExit(f"[报告] {path.name} 引用政策已变化；请重新审查并重跑 check.py")
        rule_id = clause["id"]
        title = re.sub(r"（[^（）]*" + re.escape(rule_version) + r"[^（）]*）$", "", clause["title"]).strip()
        name = f"{title}{'' if title.endswith('）') else ' '}规则 {rule_id.split(':', 1)[-1]}"
        if rule_id in names:
            raise SystemExit(f"[报告] 同一条款重复：{rule_id}")
        names[rule_id] = name
        items.extend(item for item in expected if not item.get("resolution"))
    return items, names


def why(item: dict) -> str:
    if item["kind"] == "ambiguity":
        return f"例如：{item['example']}{item['verdict']}" if item["example"] else ""
    if item["kind"] == "gap":
        return f"这种文件原文既没说通过，也没说不通过：{item['title']}。"
    if item["kind"] == "conflict":
        return f"这种文件原文既说通过，又说不通过：{item['title']}。"
    return "原文没写这种情况怎么认定。遇到时程序只能记待审核，人工也会各判各的。"


def options(item: dict) -> list[dict]:
    if item["kind"] == "ambiguity":
        return [{"key": "ABCDEFGHIJ"[index], "label": f"理解{reading['ordinal']}：{reading['label']}", "detail": reading["says"]} for index, reading in enumerate(item["readings"])]
    if item["kind"] in ("gap", "conflict"):
        return [dict(option, detail="") for option in VERDICT_OPTIONS]
    return []


def expect(item: dict) -> str:
    if item["kind"] == "ambiguity":
        return "选一种理解，或写出你的说法。"
    if item["kind"] in ("gap", "conflict"):
        return "选一个结论，或写出你的说法。"
    question = item["question"]
    if re.search(r"几分|多少|几", question):
        return "给出具体的数值或界限。"
    if re.search(r"哪些|哪一|什么|凭什么|怎样|怎么|指哪", question):
        return "给出认定标准，或列出包括哪些。"
    return "是或否；如有条件，写明条件。"


def related(item: dict, names: dict[str, str]) -> dict:
    example = f"{item['example']}{item['verdict']}" if item["kind"] == "ambiguity" and item["example"] else ""
    return {"clause": names[item["clause"]], "question": item["question"], "example": example}


def lettered(texts: list[str]) -> list[dict]:
    return [{"key": "ABCDEFGHIJ"[index], "label": text, "detail": ""} for index, text in enumerate(texts)]


def build(directory: Path, plan_path: Path, out: Path) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    items, names = findings(directory, plan["rule_version"])
    by_id = {item["id"]: item for item in items}
    relayed = plan.get("relayed", {})
    applied = plan.get("applied", {})
    conclusion = plan.get("conclusion") or {"cards": []}
    general = plan.get("general") or {"points": []}
    used = Counter([*applied, *(i for card in plan.get("common", []) for i in card["items"]), *(i for card in conclusion["cards"] for i in card.get("items", []))])
    problems = [f"没有这处发现：{i}" for i in used if i not in by_id] + [f"用了不止一次：{i}" for i, n in used.items() if n > 1]
    problems += [f"没有这条转达的答复：{rule['relayed']}" for rule in applied.values() if rule["relayed"] not in relayed]
    if problems:
        raise SystemExit("[整理] " + "；".join(problems))

    cards: list[dict] = []
    for card in plan.get("common", []):
        rows = [by_id[i] for i in card["items"]]
        cards.append(
            {
                "key": f"common:{card['title']}",
                "group": "common",
                "clauses": list(dict.fromkeys(names[row["clause"]] for row in rows)),
                "question": card["question"],
                "why": card["why"],
                "options": lettered(card["options"]) if card.get("options") else [],
                "expect": card.get("expect") or ("选一个，或写出你的说法。" if card.get("options") else ""),
                "related": [related(row, names) for row in rows],
                "items": card["items"],
            }
        )
    for number, card in enumerate(conclusion["cards"], 1):
        rows = [by_id[i] for i in card.get("items", [])]
        cards.append(
            {
                "key": f"conclusion:{number}",
                "group": "conclusion",
                "clauses": card.get("clauses") or list(dict.fromkeys(names[row["clause"]] for row in rows)),
                "question": card.get("question") or rows[0]["question"],
                "why": card.get("why") or conclusion.get("why", ""),
                "options": lettered(conclusion.get("options", [])),
                "expect": conclusion.get("expect", ""),
                "items": card.get("items", []),
            }
        )
    for number, point in enumerate(general["points"], 1):
        cards.append(
            {
                "key": f"general:{number}",
                "group": "general",
                "question": point,
                "why": general.get("why", ""),
                "options": lettered(general.get("options", [])),
                "expect": general.get("expect", ""),
            }
        )
    order = plan.get("clause_order") or []

    def position(item: dict) -> tuple:
        prefix, _, number = item["clause"].partition(":")
        return (order.index(prefix) if prefix in order else len(order), prefix, int(number) if number.isdigit() else 0)

    for item in sorted((item for item in items if item["id"] not in used or item["id"] in applied), key=position):
        card = {
            "key": item["id"],
            "group": "clause",
            "clauses": [names[item["clause"]]],
            "question": item["question"],
            "why": why(item),
            "options": options(item),
            "expect": expect(item),
            "items": [item["id"]],
        }
        if item["id"] in applied:
            rule = applied[item["id"]]
            card["relayed"] = {**relayed[rule["relayed"]], "applied": rule["result"]}
        cards.append(card)

    questionnaire = {"schema": "rule-questionnaire-v1", "customer_id": plan["customer_id"], "rule_version": plan["rule_version"], "title": plan["title"], "questions": cards}
    out.write_text(json.dumps(questionnaire, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    counts = Counter(card["group"] for card in cards)
    print(
        f"发现 {len(items)} 处；卡片 {len(cards)} 张：共性问题 {counts['common']}（管 {sum(len(c['items']) for c in plan.get('common', []))} 处）、"
        f"交客户补结论 {counts['conclusion']}、通用判定说明 {counts['general']}、逐条问题 {counts['clause']}（其中按转达答复处理 {len(applied)}）"
    )
    leaked = [word for word in ("OWL", "本体", "公理", "推理", "ofn", "__") if word in json.dumps([{k: v for k, v in c.items() if k not in ("key", "items")} for c in cards], ensure_ascii=False)]
    if leaked:
        raise SystemExit(f"[白话] 卡片里有技术词：{'、'.join(leaked)}")


def choice_label(card: dict, choice: str | None) -> str:
    if not choice:
        return ""
    if choice == RELAYED:
        return "确认按共性答复处理" if (card.get("relayed") or {}).get("applied") else "确认已转达的答复"
    return next((option["label"] for option in card.get("options", []) if option["key"] == choice), choice)


def literal(value: str) -> str:
    if "\x00" in value:
        raise SystemExit("[导入] 文本里有空字符")
    return "'" + value.replace("'", "''") + "'"


def validate(questionnaire: dict) -> None:
    """The shape the rules page reads; the database only checks that each card is an object keyed as declared."""
    problems = []
    if questionnaire.get("schema") != "rule-questionnaire-v1":
        problems.append("不是问卷导入文件")
    if not re.fullmatch(r"[a-z0-9-]{1,64}", str(questionnaire.get("customer_id", ""))):
        problems.append("customer_id 不是客户注册表编号")
    for field in ("rule_version", "title"):
        if not isinstance(questionnaire.get(field), str) or not 1 <= len(questionnaire[field].strip()) <= 200:
            problems.append(f"{field} 缺失或太长")
    cards = questionnaire.get("questions") or []
    keys = [card.get("key") for card in cards]
    if not cards or len(set(keys)) != len(keys):
        problems.append("没有卡片，或卡片编号重复")
    for card in cards:
        options = [option.get("key") for option in card.get("options", [])]
        if card.get("group") not in GROUPS or not str(card.get("question", "")).strip() or len(set(options)) != len(options) or RELAYED in options:
            problems.append(f"卡片 {card.get('key')} 的分组、问题或选项不对")
        relayed = card.get("relayed")
        if relayed and not (relayed.get("answer") and relayed.get("source") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(relayed.get("date", "")))):
            problems.append(f"卡片 {card.get('key')} 的转达答复缺来源或日期")
    if problems:
        raise SystemExit("[导入] " + "；".join(problems))


def target(questionnaire: dict, tenant: str) -> str:
    """The questionnaire's row: this enterprise, this customer, this rule version."""
    return (
        f"r.tenant_id = (SELECT id FROM product.tenants WHERE provider = 'feishu' AND external_key = {literal(tenant)}) "
        f"AND r.customer_id = {literal(questionnaire['customer_id'])} AND r.rule_version = {literal(questionnaire['rule_version'])}"
    )


def load_sql(questionnaire: dict, tenant: str) -> str:
    validate(questionnaire)
    cards = questionnaire["questions"]
    rows = ",\n".join(f"    ({position}, {literal(json.dumps(card, ensure_ascii=False, separators=(',', ':')))}::jsonb)" for position, card in enumerate(cards))
    return f"""\\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM product.tenants WHERE provider = 'feishu' AND external_key = {literal(tenant)}) THEN
        RAISE EXCEPTION '没有这个企业：%', {literal(tenant)};
    END IF;
END $$;
WITH questionnaire AS (
    INSERT INTO product.rule_questionnaires (id, tenant_id, customer_id, rule_version, title)
    SELECT gen_random_uuid(), id, {literal(questionnaire["customer_id"])}, {literal(questionnaire["rule_version"])}, {literal(questionnaire["title"])}
    FROM product.tenants WHERE provider = 'feishu' AND external_key = {literal(tenant)}
    RETURNING id
)
INSERT INTO product.rule_questions (questionnaire_id, question_key, position, card)
SELECT questionnaire.id, cards.card->>'key', cards.position, cards.card
FROM questionnaire, (VALUES
{rows}
) AS cards(position, card);
DO $$
BEGIN
    IF (SELECT count(*) FROM product.rule_questions q JOIN product.rule_questionnaires r ON r.id = q.questionnaire_id WHERE {target(questionnaire, tenant)}) <> {len(cards)} THEN
        RAISE EXCEPTION '导入的卡片数不是 {len(cards)}';
    END IF;
END $$;
SELECT r.id AS questionnaire_id, (SELECT count(*) FROM product.rule_questions q WHERE q.questionnaire_id = r.id) AS cards
FROM product.rule_questionnaires r WHERE {target(questionnaire, tenant)};
COMMIT;
"""


def export_sql(questionnaire: dict, tenant: str) -> str:
    return f"""\\set ON_ERROR_STOP on
SELECT jsonb_build_object(
    'schema', 'rule-questionnaire-answers-v1',
    'customer_id', r.customer_id, 'rule_version', r.rule_version, 'title', r.title,
    'closed_at', r.closed_at, 'exported_at', now(),
    'questions', coalesce((
        SELECT jsonb_agg(jsonb_build_object('key', q.question_key, 'card', q.card, 'answers', coalesce((
            SELECT jsonb_agg(jsonb_build_object('answerer', person.answerer, 'revisions', person.revisions) ORDER BY person.first_at)
            FROM (
                SELECT min(a.answered_at) AS first_at,
                       coalesce((SELECT i.display_name FROM product.identities i
                                 WHERE i.user_id = a.answered_by AND i.provider = t.provider AND i.tenant_key = t.external_key
                                 ORDER BY i.display_name <> '' DESC LIMIT 1), '') AS answerer,
                       jsonb_agg(jsonb_build_object('revision', a.revision, 'choice', a.choice, 'text', a.answer_text, 'note', a.note, 'answered_at', a.answered_at) ORDER BY a.revision) AS revisions
                FROM product.rule_question_answers a
                WHERE a.questionnaire_id = q.questionnaire_id AND a.question_key = q.question_key
                GROUP BY a.answered_by
            ) person), '[]'::jsonb)) ORDER BY q.position)
        FROM product.rule_questions q WHERE q.questionnaire_id = r.id), '[]'::jsonb)
)
FROM product.rule_questionnaires r JOIN product.tenants t ON t.id = r.tenant_id
WHERE {target(questionnaire, tenant)};
"""


def close_sql(questionnaire: dict, tenant: str) -> str:
    # Closing waits for answers being saved; a closed questionnaire keeps its first closing.
    return f"""\\set ON_ERROR_STOP on
UPDATE product.rule_questionnaires r SET closed_at = now() WHERE {target(questionnaire, tenant)} AND r.closed_at IS NULL;
SELECT r.closed_at FROM product.rule_questionnaires r WHERE {target(questionnaire, tenant)};
"""


def where(item_id: str) -> tuple[str, str]:
    clause, _, place = item_id.partition("#")
    file = clause.replace(":", "-", 1) + ".review.json"
    if place.startswith("G"):
        return file, f"原文没有给出结论的文件 {place[1:]}"
    if place.startswith("C"):
        return file, f"原文同时给出通过和不通过的文件 {place[1:]}"
    if "." in place:
        number, question = place.split(".", 1)
        return file, f"第 {number} 处（原文没有说）第 {question} 问"
    return file, f"第 {place} 处（歧义）"


def local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


def described(answer: dict) -> str:
    parts = []
    if answer.get("choice_label"):
        parts.append(f"选择「{answer['choice_label']}」" + (f"（{answer['choice']}）" if answer.get("choice") and answer["choice"] != answer["choice_label"] else ""))
    if answer.get("text"):
        parts.append(f"原话「{answer['text']}」")
    if answer.get("note"):
        parts.append(f"备注「{answer['note']}」")
    return "；".join(parts)


def answers(export_path: Path, directory: Path, out: Path) -> None:
    export = json.loads(export_path.read_text(encoding="utf-8"))
    if export.get("schema") != "rule-questionnaire-answers-v1":
        raise SystemExit("[导出] 不是规则确认问卷的导出文件")
    per_file: dict[str, list[str]] = {}
    unanswered, general = [], []
    for entry in export["questions"]:
        card = entry["card"]
        # Each answerer's current answer is their latest revision; the earlier ones are its history.
        rows = [{**person["revisions"][-1], "answerer": person["answerer"] or "未署名成员", "history": person["revisions"][:-1]} for person in entry["answers"]]
        for row in rows:
            row["choice_label"] = choice_label(card, row.get("choice"))
        lines = []
        for row in rows:
            when = local_time(row["answered_at"])
            lines.append(f"- {row['answerer']}，{when}：{described(row)}" + (f"（改过 {len(row['history'])} 次）" if row["history"] else ""))
        distinct = {(row.get("choice"), (row.get("text") or "").strip()) for row in rows}
        if len(distinct) > 1:
            lines.append("- **答复不一致**：不替客户选，交产品负责人决定。")
        if rows and not card.get("items"):
            general += [f"### {card['question']}", *lines, ""]
        for item in card.get("items", []):
            file, place = where(item)
            if not (directory / file).exists():
                raise SystemExit(f"[导出] 工作目录里没有 {file}")
            if not rows:
                unanswered.append(f"- {file} {place}：{card['question']}")
                continue
            source = "本条问题" if card["group"] == "clause" else f"共性问题「{card['key'].split(':', 1)[1]}」" if card["group"] == "common" else f"交客户补结论「{card['question']}」"
            per_file.setdefault(file, []).append("\n".join([f"### {place}", f"来源：{source}", *lines, ""]))
    title = f"# {export['title']} · 问卷答复回填清单\n\n导出时间：{local_time(export['exported_at'])}" + ("（问卷已关闭）" if export.get("closed_at") else "（问卷仍开放，之后可能还有答复）")
    body = [title, "", "回填时在 .review.json 中记录 resolution.answer（客户原话）和 resolution.source（答复人、时间与渠道），答复不一致的不回填，交产品负责人决定。", ""]
    for file in sorted(per_file):
        body += [f"## {file}", "", *per_file[file]]
    if general:
        body += ["## 不属于某条条款的答复", "", *general]
    if unanswered:
        body += ["## 还没有答复", "", *unanswered, ""]
    out.write_text("\n".join(body), encoding="utf-8")
    print(f"有答复的条款文件 {len(per_file)} 个，没有答复的发现 {len(unanswered)} 处；写入 {out}")


def main(argv: list[str]) -> None:
    if len(argv) >= 4 and argv[0] == "build":
        build(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    elif len(argv) == 3 and argv[0] in ("load", "export", "close"):
        questionnaire = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        print({"load": load_sql, "export": export_sql, "close": close_sql}[argv[0]](questionnaire, argv[2]), end="")
    elif len(argv) in (3, 4) and argv[0] == "answers":
        directory = Path(argv[2])
        answers(Path(argv[1]), directory, Path(argv[3]) if len(argv) == 4 else directory / "问卷答复.md")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
