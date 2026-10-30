"""Pure directed relation evaluation over complete use-specific record sets.

The audit evaluator owns source validation, membership, local questions and the
public report. This module owns the candidate graph and matching cardinality.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from .evidence import EvidenceBudget
from .models import Key, Related, ValueComparison
from .values import could_match, normalized, record_key, scalar_key


def _reference(record: dict[str, Any]) -> dict[str, Any]:
    return {"binding_id": record["binding_id"], "row": record["row_id"]}


def _observations(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: {"value": record["values"].get(field), "state": record["states"].get(field, "unresolved"), "evidence": record["evidence"].get(field)} for field in dict.fromkeys(fields)}


def _compare(source: dict[str, Any], target: dict[str, Any], comparisons: list[ValueComparison], budget: EvidenceBudget, judge: Callable | None) -> dict[str, Any]:
    checks = []
    for index, comparison in enumerate(comparisons):
        budget.consume(1)
        left, right = comparison.source_field, comparison.target_field
        if source["states"].get(left) != "known" or target["states"].get(right) != "known":
            state = "unresolved"
        else:
            a, b = (normalized(record["values"][field], comparison.normalization) for record, field in ((source, left), (target, right)))
            if scalar_key(a) == scalar_key(b):
                state = "equal"
            elif comparison.mode == "exact":
                state = "different"
            else:
                answer = judge("value", source, target, index, comparison) if judge else None
                state = "equal" if answer is True else "different" if answer is False else "unresolved"
        checks.append({**comparison.model_dump(), "state": state, "source": _observations(source, [left])[left], "target": _observations(target, [right])[right]})
    states = {check["state"] for check in checks}
    state = "conflict" if "different" in states else "unresolved" if "unresolved" in states else "matched"
    return {"target": _reference(target), "target_key": {}, "state": state, "checks": checks}


def match_rows(
    sources: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    request: Related,
    budget: EvidenceBudget,
    judge: Callable | None = None,
    *,
    identities: tuple[list[str], list[str]] | None = None,
    exhaustive: bool = False,
    pending_questions: Callable[[], int] | None = None,
    target_membership: Callable[[dict[str, Any]], bool | None] | None = None,
) -> dict[str, Any]:
    """Use native witnesses for any; retain complete graphs for stronger matches."""
    if identities is not None:

        def identified(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
            missing = [
                field
                for field in fields
                if field in record.get("identity_unresolved_fields", [])
                or record["states"].get(field) != "known"
                or record["values"].get(field) is None
                or (isinstance(record["values"][field], str) and not record["values"][field].strip())
            ]
            return record if not missing else {**record, "scope_state": "unresolved", "identity_unresolved_fields": missing}

        sources = [identified(row, identities[0]) for row in sources]
        targets = [identified(row, identities[1]) for row in targets]

    def compatible(key: tuple, record: dict[str, Any], projection: Key) -> bool:
        missing = record.get("identity_unresolved_fields", [])
        if missing:
            record = {**record, "states": {**record["states"], **{field: "unresolved" for field in missing}}}
        return could_match(key, record, projection)

    source_keys = [record_key(row, request.source_key) for row in sources]
    target_keys = [record_key(row, request.target_key) for row in targets]
    counts = Counter(key for key in source_keys if key is not None)
    target_index: dict[tuple, list[int]] = defaultdict(list)
    for index, key in enumerate(target_keys):
        if key is not None:
            target_index[key].append(index)
    unknown_targets = [index for index, key in enumerate(target_keys) if key is None]
    confirmed: list[list[int]] = [[] for _ in sources]
    possible: list[set[int]] = [set() for _ in sources]
    native_pairs: list[dict[int, dict[str, Any]]] = [{} for _ in sources]
    native_witness = [False for _ in sources]
    searched = [True for _ in sources]
    waiting_membership = [False for _ in sources]
    membership_cache: dict[int, bool | None] = {}

    def member(index: int) -> bool | None:
        if index not in membership_cache:
            membership_cache[index] = target_membership(targets[index]) if target_membership else True
        return membership_cache[index]

    semantic = request.key_match != "exact"
    for i, (source, key) in enumerate(zip(sources, source_keys, strict=True)):
        if key is None:
            # Unknown source membership/fields can affect another source's
            # uniqueness, even though this source cannot itself be confirmed.
            for j, target_key in enumerate(target_keys):
                budget.consume(1)
                if semantic or target_key is None or compatible(target_key, source, request.source_key):
                    possible[i].add(j)
            continue
        confirmed[i].extend(target_index.get(key, []))
        if request.match == "any" and not exhaustive:
            # Search every exact-key candidate before asking the model. A later
            # native witness can make an earlier semantic comparison irrelevant.
            exact = list(confirmed[i])
            confirmed[i] = []
            for position, j in enumerate(exact):
                budget.consume(1)
                pending_before = pending_questions() if pending_questions else 0
                membership = member(j)
                if membership is None:
                    possible[i].add(j)
                    if pending_questions is None or pending_questions() > pending_before:
                        waiting_membership[i], searched[i] = True, False
                        break
                elif membership is True:
                    confirmed[i].append(j)
                    native_pairs[i][j] = _compare(source, targets[j], request.comparisons, budget, None)
                    if native_pairs[i][j]["state"] == "matched" and target_membership is not None:
                        searched[i] = position == len(exact) - 1
                        break
            native_witness[i] = any(pair["state"] == "matched" for pair in native_pairs[i].values())
        for j in unknown_targets:
            budget.consume(len(request.target_key.fields) + 1)
            if semantic or compatible(key, targets[j], request.target_key):
                possible[i].add(j)
        if request.match == "any" and not exhaustive and not native_witness[i] and not waiting_membership[i]:
            # One undecided candidate is the next batch. A confirmed witness
            # ends this search; unexplored targets remain explicitly unsearched.
            exact = list(confirmed[i])
            confirmed[i] = []
            candidates = exact + ([j for j, target_key in enumerate(target_keys) if target_key is not None and target_key != key] if semantic else [])
            for position, j in enumerate(candidates):
                budget.consume(1)
                pending_before = pending_questions() if pending_questions else 0
                membership = member(j)
                if membership is False:
                    continue
                if membership is None:
                    possible[i].add(j)
                    if pending_questions is None or pending_questions() > pending_before:
                        searched[i] = position == len(candidates) - 1
                        break
                    continue
                value = True if j in exact else judge("key", source, targets[j], 0, None) if judge else None
                if value is None:
                    possible[i].add(j)
                    if pending_questions is None or pending_questions() > pending_before:
                        searched[i] = position == len(candidates) - 1
                        break
                    continue
                if value is False:
                    continue
                confirmed[i].append(j)
                pair = _compare(source, targets[j], request.comparisons, budget, judge)
                native_pairs[i][j] = pair
                pending = pending_questions() > pending_before if pending_questions else any(
                    check["state"] == "unresolved" and check["source"]["state"] == check["target"]["state"] == "known" for check in pair["checks"]
                )
                if pair["state"] == "matched" or pending:
                    searched[i] = position == len(candidates) - 1
                    break
        elif semantic and not native_witness[i] and not waiting_membership[i]:
            for j, target_key in enumerate(target_keys):
                if target_key is None or target_key == key:
                    continue
                budget.consume(1)
                value = judge("key", source, targets[j], 0, None) if judge else None
                if value is True:
                    confirmed[i].append(j)
                elif value is None:
                    possible[i].add(j)

    known_incoming: dict[int, set[int]] = defaultdict(set)
    possible_incoming: dict[int, set[int]] = defaultdict(set)
    for i in range(len(sources)):
        for j in confirmed[i]:
            known_incoming[j].add(i)
        for j in possible[i]:
            possible_incoming[j].add(i)
    duplicate_sources = [key is not None and counts[key] > 1 for key in source_keys]
    possible_source_alias = [False for _ in sources]
    if request.match == "one_to_one":
        for i, key in enumerate(source_keys):
            if key is None:
                continue
            for k in range(i + 1, len(sources)):
                other_key = source_keys[k]
                budget.consume(1)
                if other_key == key:
                    continue  # Already counted as a confirmed duplicate.
                if other_key is None:
                    if semantic or compatible(key, sources[k], request.source_key):
                        possible_source_alias[i] = True
                elif semantic:
                    value = judge("source_key", sources[i], sources[k], 0, None) if judge else None
                    if value is True:
                        duplicate_sources[i] = duplicate_sources[k] = True
                    elif value is None:
                        possible_source_alias[i] = possible_source_alias[k] = True
            # Earlier unknown source rows must also be considered.
            for k in range(i):
                if source_keys[k] is None and (semantic or compatible(key, sources[k], request.source_key)):
                    possible_source_alias[i] = True

    records = []
    for i, (source, key) in enumerate(zip(sources, source_keys, strict=True)):
        budget.consume(1)
        pairs = []
        for j in confirmed[i]:
            budget.consume(1)
            pair = native_pairs[i][j] if j in native_pairs[i] else _compare(source, targets[j], request.comparisons, budget, judge)
            pair["target_key"] = _observations(targets[j], request.target_key.fields)
            pairs.append(pair)
        uncertain = bool(possible[i])
        if request.match == "one_to_one":
            uncertain |= possible_source_alias[i] or any(possible_incoming[j] - {i} for j in confirmed[i])
        if key is None:
            state, reason = "unresolved", "source_key_or_record_role"
        elif request.match == "one_to_one" and (duplicate_sources[i] or len(pairs) > 1 or any(len(known_incoming[j]) > 1 for j in confirmed[i])):
            state, reason = "conflict", "duplicate_or_shared_key"
        elif not pairs:
            state, reason = ("unresolved", "target_key_or_semantic_match") if uncertain else ("missing", "no_matching_key")
        else:
            states = {pair["state"] for pair in pairs}
            if request.match == "any" and "matched" in states:
                state, reason = "matched", "matching_record"
            elif request.match != "any" and "conflict" in states:
                state, reason = "conflict", "different_values"
            elif "unresolved" in states or uncertain:
                state, reason = "unresolved", "value_or_candidate_set"
            else:
                state, reason = ("matched", "matching_records") if "matched" in states else ("conflict", "different_values")
        records.append(
            {
                "source": _reference(source),
                "source_values": _observations(source, request.source_key.fields + [item.source_field for item in request.comparisons]),
                "state": state,
                "reason": reason,
                "candidates": pairs,
                "candidate_search_complete": searched[i] and not (semantic and native_witness[i] and len(target_keys) - len(unknown_targets) > len(confirmed[i])),
                "possible_targets": [_reference(targets[j]) for j in sorted(possible[i])],
                "holes": [{"kind": reason, "source": _reference(source), "target_input": request.target}] if state == "unresolved" else [],
            }
        )
    return {
        "source_rows": len(sources),
        "target_rows": len(targets),
        "source_distinct_known_keys": len(counts),
        "target_distinct_known_keys": len(target_index),
        "unknown_target_keys": [_reference(targets[j]) for j in unknown_targets],
        "counts": dict(Counter(record["state"] for record in records)),
        "records": records,
    }
