"""Clarifying one version of a customer's rulebook, as a state machine. No I/O happens here.

    apply(state, event)            -> state   what a prepared piece of work changes
    issues(task_id, state, facts)  -> issues  what is open, each with the only actions it accepts
    project(task_id, state, facts) -> view    what a client sees: the phase, progress, issues and the next action

The loop: review every clause of the current revision; put the questions found before the customer; turn their answers
into clearer wording; confirm that as the next revision; repeat until nothing is open; then write a reading beside each
clause. `state` holds only what people prepared here and nobody else owns yet (reviews, proposed wording, readings
waiting for confirmation). Everything confirmed is read from its owner as `facts`: the revisions, the questionnaires and
their answers. So the task never runs ahead of what was approved, and a new answer or a new revision shows by itself.

Clauses do not depend on each other: each ends reviewed or as an issue, and one rejected review holds back no other
clause. Rounds do depend on each other: the next revision starts from the confirmed one before it.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from product import clause_check, rulebooks
from product.workflow import Rejected, _act, _issue

NAMESPACE = uuid.UUID("5d0c6f0e-3b0a-4d55-9a0e-6c6d2f1f0a11")
REVIEW_SCHEMA = "clause-check-review-v1"
WAIT_FOR_ANSWERS_MS = 3_600_000


def task_id_for(tenant_id: str, customer: str, version: str) -> str:
    """One clarification per enterprise, customer and version: whoever opens it finds the same task."""
    return str(uuid.uuid5(NAMESPACE, f"{tenant_id}:{customer}:{version}"))


def new_state(customer: str, version: str) -> dict:
    return {
        "kind": "clarification",
        "source": {"kind": "rulebook", "token": f"rulebook:{customer}:{version}"},
        "customer": customer,
        "version": version,
        "draft": None,  # the clauses of a version that has no revision yet
        "reviews": {},  # clause -> {digest, revision, review, findings, by}
        "settled": {},  # card key -> {how, by, ...}: an answered card that has been dealt with
        "follow_ups": {},  # card key -> card: a further question on an answered card
        "proposals": {},  # clause -> {revision, digest, text, kind, note, sources, by}
        "readings": {},  # clause -> {digest, reading, by}
        "decisions": {},  # request_id -> [issue_id, action_id]
    }


# ---- events ------------------------------------------------------------------------------------------


def apply(state: dict, event: dict) -> dict:
    state = deepcopy(state)
    kind = event["type"]
    if kind == "drafted":
        state["draft"] = {"documents": event["documents"], "by": event["by"]}
    elif kind == "reviewed":
        state["reviews"][event["clause"]] = {key: event[key] for key in ("digest", "revision", "review", "findings", "by")}
    elif kind == "proposed":
        state["proposals"][event["clause"]] = {key: event[key] for key in ("revision", "digest", "text", "kind", "note", "sources", "by")}
        for key in event["cards"]:
            state["settled"][key] = {"how": "proposed", "by": event["by"]}
    elif kind == "kept":
        state["proposals"].pop(event["clause"], None)
        for key in event["cards"]:
            state["settled"][key] = {"how": "kept", "reason": event["reason"], "by": event["by"]}
    elif kind == "asked_further":
        state["follow_ups"][event["card"]["key"]] = event["card"]
        state["settled"][event["card"]["follows"]] = {"how": "asked_further", "by": event["by"]}
    elif kind == "read":
        state["readings"][event["clause"]] = {key: event[key] for key in ("digest", "reading", "by")}
    else:
        raise Rejected("unknown_event")
    if event.get("request_id"):
        state["decisions"][event["request_id"]] = [event["issue_id"], event["action_id"]]
    return state


class Invalid(Rejected):
    """Prepared work that cannot be taken as it is; `problems` says everything wrong with it at once."""

    def __init__(self, code: str, problems: list[str]):
        super().__init__(code)
        self.problems = problems


CONFIRM = {"confirm_clauses", "send_cards", "confirm_revision", "confirm_clarified", "confirm_readings"}
PREPARE = {"submit_clauses", "submit_review", "propose_text", "keep_text", "ask_further", "submit_reading"}


def _words(value: Any, limit: int) -> str:
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
        raise Invalid("invalid_input", ["缺少文字，或文字太长"])
    return value.strip()


def decide(state: dict, facts: dict, issue: dict, action_id: str, value: Any, by: str) -> dict:
    """The event a prepare action stands for, or why its input cannot be taken. Nothing is confirmed by it."""
    value = value if isinstance(value, dict) else {}
    revision = current(state, facts)
    if action_id == "submit_clauses":
        documents = value.get("documents")
        problems = rulebooks.revision_problems(documents, {}, None)
        if problems:
            raise Invalid("clauses_rejected", problems)
        return {
            "type": "drafted",
            "documents": [{"key": d["key"], "title": d["title"].strip(), "clauses": [{"id": c["id"], "category": c["category"], "text": c["text"]} for c in d["clauses"]]} for d in documents],
            "by": by,
        }
    assert revision is not None
    clause = next(row for row in clauses(revision) if row["id"] == issue["subject"]["key"])
    if action_id == "submit_review":
        review = {**value, "schema": value.get("schema", REVIEW_SCHEMA)}
        errors, findings = clause_check.check_review(Path("."), review, {"id": clause["id"], "text": clause["text"]})
        if errors:
            raise Invalid("review_rejected", errors)
        return {"type": "reviewed", "clause": clause["id"], "digest": clause["digest"], "revision": revision["revision"], "review": review, "findings": findings, "by": by}
    if action_id == "submit_reading":
        reading = {part: value.get(part) for part in rulebooks.READING_PARTS}
        problems = rulebooks.reading_problems(clause["text"], reading)
        if problems:
            raise Invalid("reading_rejected", problems)
        return {"type": "read", "clause": clause["id"], "digest": clause["digest"], "reading": reading, "by": by}
    answered = {card["key"]: card for card in _standing(state, facts, revision)["answered"] if card["items"][0].split("#", 1)[0] == clause["id"]}
    if action_id == "keep_text":
        return {"type": "kept", "clause": clause["id"], "cards": sorted(answered), "reason": _words(value.get("reason"), 2000), "by": by}
    if action_id == "propose_text":
        cited = value.get("cards")
        text, kind = _words(value.get("text"), rulebooks.MAX_CLAUSE), value.get("kind")
        problems = []
        if not isinstance(cited, list) or not cited or not set(cited) <= set(answered):
            problems.append("cards 要列出这条条款已答复的卡片")
        if kind not in {"same", "changed"}:
            problems.append("kind 只能是 same 或 changed")
        if text == clause["text"]:
            problems.append("新写法与原文相同；不需要改时用 keep_text")
        if problems:
            raise Invalid("proposal_rejected", problems)
        sources = [{"question_key": key, "answerers": sorted({answer["answerer"] for answer in answered[key]["answers"]})} for key in sorted(set(cited))]
        return {
            "type": "proposed",
            "clause": clause["id"],
            "revision": revision["revision"],
            "digest": clause["digest"],
            "text": text,
            "kind": kind,
            "note": _words(value.get("note"), 2000),
            "sources": sources,
            "cards": sorted(answered),
            "by": by,
        }
    if action_id == "ask_further":
        follows = value.get("follows")
        if follows not in answered:
            raise Invalid("follow_up_rejected", ["follows 要是这条条款一张已答复的卡片"])
        number = 1 + sum(1 for key in state["follow_ups"] if key.startswith(follows + ".f"))
        options = value.get("options") or []
        if not isinstance(options, list) or not all(isinstance(option, dict) and isinstance(option.get("label"), str) and option["label"].strip() for option in options):
            raise Invalid("follow_up_rejected", ["options 的每一项要有 label"])
        key = f"{follows}.f{number}"
        card = {
            "key": key,
            "group": "clause",
            "clauses": answered[follows]["clauses"],
            "question": _words(value.get("question"), 1000),
            "why": _words(value.get("why"), 4000),
            "options": [{"key": f"o{index}", "label": option["label"].strip(), "detail": str(option.get("detail") or "")} for index, option in enumerate(options, start=1)],
            "expect": "" if options else "请给出明确的标准、数值，或是与否。",
            "sources": answered[follows].get("sources", []),
            "items": [key],
            "follows": follows,
        }
        return {"type": "asked_further", "card": card, "by": by}
    raise Rejected("action_not_allowed")


# ---- what the facts say ------------------------------------------------------------------------------


def current(state: dict, facts: dict) -> dict | None:
    """The revision being clarified: the latest one of the task's version."""
    return facts.get("revision")


def clauses(revision: dict) -> list[dict]:
    return [{**clause, "document": document["title"]} for document in revision["documents"] for clause in document["clauses"]]


def reviewed(state: dict, clause: dict) -> dict | None:
    """The review of this clause as it reads now; a review of earlier wording no longer counts."""
    review = state["reviews"].get(clause["id"])
    return review if review and review["digest"] == clause["digest"] else None


def card_key(clause: str, revision: int, code: str) -> str:
    return f"{clause}#r{revision}.{code}"


def _card(clause: dict, revision: int, finding: dict) -> dict:
    """A finding as the question card the customer answers."""
    options = [{"key": f"r{index}", "label": reading["label"], "detail": reading["says"]} for index, reading in enumerate(finding.get("readings") or [], start=1)]
    why = finding["example"] + (" " + finding["verdict"] if finding.get("verdict") else "")
    return {
        "key": card_key(clause["id"], revision, finding["id"].split("#", 1)[1]),
        "group": "clause",
        "clauses": [f"{clause['document']} {clause['id']}"],
        "question": finding["question"],
        "why": why,
        "options": options,
        "expect": "" if options else "请给出明确的标准、数值，或是与否。",
        "sources": [{"clause": clause["id"], "text": clause["text"], "quotes": finding["quote"]}],
        "items": [card_key(clause["id"], revision, finding["id"].split("#", 1)[1])],
    }


def candidate_cards(state: dict, revision: dict) -> list[dict]:
    """Every question the valid reviews raise on the current wording, and the further questions asked on answers."""
    cards = []
    for clause in clauses(revision):
        review = reviewed(state, clause)
        if review is not None:
            cards += [_card(clause, review["revision"], finding) for finding in review["findings"]]
    by_clause = {clause["id"] for clause in clauses(revision)}
    cards += [card for card in state["follow_ups"].values() if card["items"][0].split("#", 1)[0] in by_clause]
    return cards


def asked(facts: dict) -> dict[str, dict]:
    """Each card put before the customer on this version, as last asked: its questionnaire's revision, whether that
    questionnaire is closed, and every answerer's answer."""
    return {row["card"]["key"]: row for row in sorted(facts.get("cards", []), key=lambda row: row["revision"])}


def _standing(state: dict, facts: dict, revision: dict) -> dict[str, list[dict]]:
    """The candidate cards by where they stand: to send, waiting for an answer, answered and still to deal with, done."""
    sent = asked(facts)
    out: dict[str, list[dict]] = {"unsent": [], "waiting": [], "answered": [], "settled": []}
    for card in candidate_cards(state, revision):
        row = sent.get(card["key"])
        if card["key"] in state["settled"]:
            out["settled"].append(card)
        elif row is None or (not row["answers"] and row["revision"] != revision["revision"]):
            out["unsent"].append(card)  # never asked, or asked on an earlier revision and never answered
        elif not row["answers"]:
            out["waiting"].append(card)
        else:
            out["answered"].append({**card, "answers": row["answers"]})
    return out


def proposals(state: dict, revision: dict) -> dict[str, dict]:
    """Proposed wording for the current revision's clauses as they read now."""
    digests = {clause["id"]: clause["digest"] for clause in clauses(revision)}
    return {clause: row for clause, row in state["proposals"].items() if row["revision"] == revision["revision"] and digests.get(clause) == row["digest"]}


def pending_readings(state: dict, revision: dict) -> dict[str, dict]:
    """Readings prepared here for clauses that have none confirmed yet."""
    return {clause["id"]: state["readings"][clause["id"]] for clause in clauses(revision) if not clause.get("reading") and state["readings"].get(clause["id"], {}).get("digest") == clause["digest"]}


def next_revision(state: dict, revision: dict) -> dict:
    """The request that writes the next revision: the current clauses with the proposed wording in place."""
    changed = proposals(state, revision)
    documents = [
        {
            "key": document["key"],
            "title": document["title"],
            "clauses": [{"id": c["id"], "category": c["category"], "text": changed[c["id"]]["text"] if c["id"] in changed else c["text"]} for c in document["clauses"]],
        }
        for document in revision["documents"]
    ]
    changes = {clause: {"kind": row["kind"], "note": row["note"], "sources": row["sources"]} for clause, row in changed.items()}
    return {
        "version": revision["version"],
        "revision": revision["revision"] + 1,
        "base": {"version": revision["version"], "revision": revision["revision"]},
        "documents": documents,
        "changes": changes,
        "origin": "clarification",
    }


# ---- issues ------------------------------------------------------------------------------------------

REVIEW_INPUT = {
    "type": "object",
    "description": "A clause-check-review-v1 record for this one clause, without `source` (PPAP binds it to the clause shown). "
    "requirements: every requirement of the text, each {quote, role, scope, meaning} with quote verbatim from the clause. "
    "findings: [] when nothing is unclear; otherwise each {id, kind: ambiguity|undefined|gap|conflict, quotes, title, question, example, challenge} "
    "and, for an ambiguity, readings: [{label, meaning, basis, verdict: 通过|不通过|原文未定}] whose verdicts differ on the example.",
    "required": ["schema", "requirements", "findings"],
}
PROPOSAL_INPUT = {
    "type": "object",
    "description": "The clause's new wording and what it rests on.",
    "properties": {
        "text": {"type": "string", "description": "The whole clause as it should read."},
        "kind": {"enum": ["same", "changed"], "description": "same: the verdict on no document changes; changed: it does."},
        "note": {"type": "string", "description": "What changed, in one or two sentences."},
        "cards": {"type": "array", "items": {"type": "string"}, "description": "Keys of this clause's answered cards the wording rests on."},
    },
    "required": ["text", "kind", "note", "cards"],
}
KEEP_INPUT = {"type": "object", "properties": {"reason": {"type": "string", "description": "Why the answers need no change to the clause."}}, "required": ["reason"]}
FOLLOW_UP_INPUT = {
    "type": "object",
    "description": "A further question on one answered card whose answers left something open.",
    "properties": {
        "follows": {"type": "string", "description": "The answered card's key."},
        "question": {"type": "string"},
        "why": {"type": "string", "description": "What the answers left open, with a concrete case."},
        "options": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "detail": {"type": "string"}}, "required": ["label"]}},
    },
    "required": ["follows", "question", "why"],
}
READING_INPUT = {
    "type": "object",
    "description": "How the clause is judged, for the customer to read: plain sentences, every quoted term and number of the clause kept, nothing the clause and the confirmed answers do not settle.",
    "properties": {part: {"type": "array", "items": {"type": "string"}} for part in rulebooks.READING_PARTS},
    "required": list(rulebooks.READING_PARTS),
}
DRAFT_INPUT = {
    "type": "object",
    "description": "The customer's rules split into clauses, by document type, exactly as the customer wrote them.",
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The document type's abbreviation in the customer's rulebook, e.g. CP."},
                    "title": {"type": "string"},
                    "clauses": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"id": {"type": "string", "description": "<key>:<number>"}, "category": {"type": "string"}, "text": {"type": "string"}}},
                    },
                },
            },
        }
    },
    "required": ["documents"],
}


def _differ(answers: list[dict]) -> bool:
    return len({(answer.get("choice"), answer.get("text")) for answer in answers}) > 1


def issues(task_id: str, state: dict, facts: dict) -> list[dict]:
    roles = facts.get("roles") or {}
    prepare, confirm = bool(roles.get("prepare")), bool(roles.get("confirm"))
    found: list[dict] = []

    def add(code, category, severity, stage, message, impact, actions, *, clause: dict | None = None, key: str = ""):
        subject = {"kind": "clause", "key": clause["id"], "document": clause["document"]} if clause else None
        found.append(_issue(task_id, code, category, severity, stage, message, impact, actions, subject=subject, key=key or (clause["id"] if clause else "")))

    revision = current(state, facts)
    if revision is None:
        draft = state["draft"]
        if draft is None:
            add(
                "CLAUSES_REQUIRED",
                "missing_evidence",
                "blocking",
                "import",
                f"{state['version']} 还没有条款。",
                "按客户原件逐条拆好提交后，才能开始审查。",
                [_act("submit_clauses", "提交拆好的条款", input_schema=DRAFT_INPUT)] if prepare else [],
            )
        else:
            total = sum(len(document["clauses"]) for document in draft["documents"])
            actions = ([_act("confirm_clauses", f"确认作为 {state['version']} 修订 0")] if confirm else []) + ([_act("submit_clauses", "重新提交条款", input_schema=DRAFT_INPUT)] if prepare else [])
            add("CLAUSES_READY", "review", "blocking", "import", f"{draft['by']} 提交了 {len(draft['documents'])} 类文件、{total} 条条款，等平台负责人确认。", "确认后成为修订 0 并冻结。", actions)
        return found

    rows = clauses(revision)
    if revision["clarified"]:
        waiting = pending_readings(state, revision)
        missing = [clause for clause in rows if not clause.get("reading") and clause["id"] not in waiting]
        for clause in missing:
            add(
                "CLAUSE_READING",
                "review",
                "blocking",
                "reading",
                f"{clause['document']} {clause['id']} 还没有解读。",
                "解读写明查什么、何时通过、何时不通过、何时转人工。",
                [_act("submit_reading", "提交这条的解读", input_schema=READING_INPUT)] if prepare else [],
                clause=clause,
            )
        if waiting and not missing:
            add(
                "READINGS_READY",
                "review",
                "blocking",
                "reading",
                f"{len(waiting)} 条解读等平台负责人确认。",
                "确认后显示在规则页原文旁。",
                [_act("confirm_readings", "确认并发布解读")] if confirm else [],
            )
        return found

    for clause in rows:
        if reviewed(state, clause) is None:
            add(
                "CLAUSE_REVIEW",
                "review",
                "blocking",
                "review",
                f"{clause['document']} {clause['id']} 还没有审查。",
                "按 clause-check 审查这条原文：没有问题也要提交，记为已审。",
                [_act("submit_review", "提交这条的审查记录", input_schema=REVIEW_INPUT)] if prepare else [],
                clause=clause,
            )
    standing = _standing(state, facts, revision)
    unreviewed = any(issue["code"] == "CLAUSE_REVIEW" for issue in found)
    if standing["unsent"] and not unreviewed:
        add(
            "CARDS_READY",
            "review",
            "blocking",
            "ask",
            f"有 {len(standing['unsent'])} 张问题卡片待发给客户确认。",
            "发送后客户成员在规则页作答。",
            [_act("send_cards", f"发送这 {len(standing['unsent'])} 张卡片")] if confirm else [],
        )
    by_clause: dict[str, list[dict]] = {}
    for card in standing["answered"]:
        by_clause.setdefault(card["items"][0].split("#", 1)[0], []).append(card)
    for clause in rows:
        cards = by_clause.get(clause["id"])
        if not cards:
            continue
        split = sum(1 for card in cards if _differ(card["answers"]))
        actions = [
            _act("propose_text", "按答复改写这条", input_schema=PROPOSAL_INPUT),
            _act("keep_text", "答复不需要改这条", input_schema=KEEP_INPUT),
            _act("ask_further", "答复没说清，追问", input_schema=FOLLOW_UP_INPUT),
        ]
        add(
            "CLAUSE_ANSWERED",
            "review",
            "blocking",
            "revise",
            f"{clause['document']} {clause['id']} 有 {len(cards)} 张卡片已答复" + (f"，其中 {split} 张的答复不一致" if split else "") + "。",
            "按答复改写条款、说明不需要改，或追问。答复不一致时由平台负责人取舍。",
            actions if prepare else [],
            clause=clause,
        )
    changed = proposals(state, revision)
    if changed and not by_clause:
        add(
            "REVISION_READY",
            "review",
            "blocking",
            "revise",
            f"{len(changed)} 条条款的新写法等平台负责人确认。",
            f"确认后成为 {revision['version']} 修订 {revision['revision'] + 1} 并冻结，本修订的问卷关闭。",
            [_act("confirm_revision", f"确认为修订 {revision['revision'] + 1}")] if confirm else [],
        )
    if not found and not standing["waiting"]:
        add(
            "CLARIFIED_READY",
            "review",
            "blocking",
            "clarify",
            f"{revision['version']} 修订 {revision['revision']} 每条都已审查，没有未决问题。",
            "确认后这一修订标为已澄清，可以为每条配解读。",
            [_act("confirm_clarified", "确认已澄清")] if confirm else [],
        )
    return found


def issue_detail(issue: dict, state: dict, facts: dict) -> dict:
    """Everything needed to act on one issue: the clause as it reads, what was asked about it and what was answered."""
    revision = current(state, facts)
    if revision is None:
        return {**issue, "detail": {"draft": state["draft"]} if state["draft"] else {}}
    detail: dict[str, Any] = {"revision": {"version": revision["version"], "revision": revision["revision"]}}
    if issue["subject"]["kind"] == "clause":
        clause = next(row for row in clauses(revision) if row["id"] == issue["subject"]["key"])
        cards = [row for row in facts.get("cards", []) if row["card"]["items"][0].split("#", 1)[0] == clause["id"]]
        detail["clause"] = {key: clause.get(key) for key in ("id", "document", "category", "text", "digest", "change", "reading")}
        detail["cards"] = [
            {
                "key": row["card"]["key"],
                "asked_on_revision": row["revision"],
                "question": row["card"]["question"],
                "why": row["card"].get("why"),
                "options": row["card"].get("options"),
                "answers": row["answers"],
                "settled": state["settled"].get(row["card"]["key"]),
            }
            for row in cards
        ]
        detail["proposal"] = proposals(state, revision).get(clause["id"])
    elif issue["code"] == "CARDS_READY":
        detail["cards"] = _standing(state, facts, revision)["unsent"]
    elif issue["code"] == "REVISION_READY":
        before = {clause["id"]: clause for clause in clauses(revision)}
        detail["changes"] = [{"clause": clause, "before": before[clause]["text"], **{k: row[k] for k in ("text", "kind", "note", "sources")}} for clause, row in proposals(state, revision).items()]
    elif issue["code"] == "READINGS_READY":
        detail["readings"] = {clause: row["reading"] for clause, row in pending_readings(state, revision).items()}
    return {**issue, "detail": detail}


# ---- projection --------------------------------------------------------------------------------------

_PHASE = {
    "CLAUSES_REQUIRED": "importing",
    "CLAUSES_READY": "importing",
    "CLAUSE_REVIEW": "reviewing",
    "CARDS_READY": "ready_to_send",
    "CLAUSE_ANSWERED": "revising",
    "REVISION_READY": "ready_to_revise",
    "CLARIFIED_READY": "ready_to_clarify",
    "CLAUSE_READING": "reading",
    "READINGS_READY": "ready_to_publish",
}
_ORDER = ["CLAUSES_REQUIRED", "CLAUSES_READY", "CLAUSE_ANSWERED", "REVISION_READY", "CLAUSE_REVIEW", "CARDS_READY", "CLARIFIED_READY", "CLAUSE_READING", "READINGS_READY"]
_ASK = {"CLAUSES_READY", "CARDS_READY", "REVISION_READY", "CLARIFIED_READY", "READINGS_READY"}


def project(task_id: str, state: dict, facts: dict) -> dict:
    found = issues(task_id, state, facts)
    revision = current(state, facts)
    result: dict[str, Any] = {"kind": "rule_clarification", "customer": state["customer"], "version": state["version"]}
    lines: list[dict] = []
    waiting = 0
    if revision is not None:
        rows = clauses(revision)
        standing = _standing(state, facts, revision)
        waiting = len(standing["waiting"])
        changed, drafts = proposals(state, revision), pending_readings(state, revision)
        valid = [reviewed(state, clause) for clause in rows]
        result["revision"] = {"version": revision["version"], "revision": revision["revision"], "clarified": revision["clarified"]}
        result["progress"] = {
            "clauses": len(rows),
            "reviewed": sum(1 for review in valid if review),
            "reviewed_without_findings": sum(1 for review in valid if review and not review["findings"]),
            "cards": {name: len(cards) for name, cards in standing.items()},
            "proposed_changes": len(changed),
            "readings": {"confirmed": sum(1 for clause in rows if clause.get("reading")), "waiting_for_confirmation": len(drafts)},
        }
        open_by_clause = {issue["subject"]["key"]: issue for issue in found if issue["subject"]["kind"] == "clause"}
        for clause, review in zip(rows, valid):
            issue = open_by_clause.get(clause["id"])
            lines.append(
                {
                    "clause": clause["id"],
                    "document": clause["document"],
                    "reviewed": bool(review),
                    "findings": len(review["findings"]) if review else None,
                    "proposed": clause["id"] in changed,
                    "reading": "confirmed" if clause.get("reading") else "waiting" if clause["id"] in drafts else None,
                    "issue_id": issue["id"] if issue else None,
                }
            )
    first = next((issue for code in _ORDER for issue in found if issue["code"] == code), None)
    if first is None:
        phase = "awaiting_answers" if waiting else "completed"
    else:
        phase = _PHASE[first["code"]]
    result["phase"] = phase
    result["summary"] = summary(phase, result, first)
    result["clauses"] = lines
    return {"phase": phase, "result": result, "issues": found, "next": next_action(task_id, phase, first, waiting), "available_actions": []}


def summary(phase: str, result: dict, first: dict | None) -> str:
    name = f"{result['version']}" + (f" 修订 {result['revision']['revision']}" if result.get("revision") else "")
    progress = result.get("progress") or {}
    if phase == "awaiting_answers":
        return f"{name}：{progress['cards']['waiting']} 张卡片等客户答复。"
    if phase == "completed":
        return f"{name} 已澄清，每条条款都有解读。"
    if phase == "reviewing":
        return f"{name}：已审 {progress['reviewed']} / {progress['clauses']} 条。"
    return f"{name}：{first['message']}" if first else name


def next_action(task_id: str, phase: str, first: dict | None, waiting: int) -> dict:
    if first is None:
        if waiting:
            return {"kind": "wait", "observe_with": "get_task", "arguments": {"task_id": task_id}, "check_after_ms": WAIT_FOR_ANSWERS_MS, "reason": "waiting_for_customer_answers"}
        return {"kind": "done", "goal_status": "achieved"}
    offered = {action["id"] for action in first["allowed_actions"]}
    if first["code"] in _ASK and not offered & CONFIRM:
        return {"kind": "blocked", "error_code": "role_required", "message": f"{first['message']}这一步要由平台负责人确认。"}
    if not offered:
        return {"kind": "blocked", "error_code": "role_required", "message": f"{first['message']}这一步要由平台负责人或受委托的审查方来做。"}
    if first["code"] in _ASK:
        return {"kind": "user_action", "issue_id": first["id"], "prompt": first["message"] + first["impact"], "resolve_with": "resolve_issue"}
    return {"kind": "call_tool", "tool": "get_issue", "arguments": {"task_id": task_id, "issue_id": first["id"]}, "reason": phase}
