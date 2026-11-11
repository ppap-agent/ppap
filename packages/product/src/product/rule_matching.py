"""规则匹配AI服务 - 专门处理规则匹配相关的AI调用"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, List, Optional, Tuple

from audit.kinds import PpapDocumentType, PpapDocumentTypeMatch

logger = logging.getLogger(__name__)


class RuleMatchingAIService:
    """规则匹配AI服务"""

    _UNKNOWN_SENTINEL = "__unknown__"
    _content_excerpt_chars = max(0, int(os.getenv("RULE_MATCH_CONTENT_EXCERPT_CHARS", "2000")))
    _cache_ttl_seconds = max(1, int(os.getenv("RULE_MATCH_CACHE_TTL_SECONDS", "1800")))
    _cache_max_entries = max(1, int(os.getenv("RULE_MATCH_CACHE_MAX_ENTRIES", "2048")))
    _cache_lock = threading.Lock()
    _match_cache: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()

    def __init__(self, ai=None, prompt_manager=None) -> None:
        # ai is documents.ai.run_json or a test double with its signature.
        self.client = ai
        self.prompt_manager = prompt_manager

    @staticmethod
    def _summarize_type_matches(matches: List[PpapDocumentTypeMatch]) -> List[Dict[str, Any]]:
        return [
            {
                "type": match.doc_type.get_type_name(),
                "score": match.score,
                "alias_hits": list(match.alias_hits),
                "keyword_hits": list(match.keyword_hits),
            }
            for match in matches[:3]
        ]

    @staticmethod
    def _score_map(matches: List[PpapDocumentTypeMatch]) -> Dict[PpapDocumentType, int]:
        return {match.doc_type: match.score for match in matches}

    @classmethod
    def _select_preferred_doc_types(
        cls,
        *,
        filename_matches: List[PpapDocumentTypeMatch],
        content_matches: List[PpapDocumentTypeMatch],
    ) -> tuple[set[PpapDocumentType] | None, str | None]:
        filename_alias_matches = [match for match in filename_matches if match.alias_hits]
        if filename_alias_matches:
            top_score = filename_alias_matches[0].score
            preferred = {match.doc_type for match in filename_alias_matches if match.score == top_score}
            return preferred, "filename_alias"

        if filename_matches and filename_matches[0].score >= 18:
            top_score = filename_matches[0].score
            preferred = {match.doc_type for match in filename_matches if match.score == top_score}
            return preferred, "filename"

        if content_matches and content_matches[0].score >= 18:
            top_score = content_matches[0].score
            preferred = {match.doc_type for match in content_matches if match.score == top_score}
            return preferred, "content"

        return None, None

    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())

    @classmethod
    def _build_content_excerpt(cls, audit_file_content: str) -> str:
        if not audit_file_content or cls._content_excerpt_chars <= 0:
            return ""
        return str(audit_file_content)[: cls._content_excerpt_chars]

    @classmethod
    def _build_cache_key(cls, audit_file_name: str, audit_file_content: str, rules: List[Dict[str, str]]) -> str:
        canonical_rules = [
            {
                "candidateId": rule["candidateId"],
                "ruleId": rule["ruleId"],
                "rule_file_name": rule["rule_file_name"],
                "type": rule["type"],
            }
            for rule in rules
        ]
        payload = {
            "audit_file_name": cls._normalize_text(audit_file_name),
            "audit_content_digest": sha256(audit_file_content.encode("utf-8", errors="ignore")).hexdigest(),
            "rules_digest": sha256(
                json.dumps(
                    canonical_rules,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @classmethod
    def _get_cached_candidate_id(cls, cache_key: str) -> Tuple[bool, Optional[str]]:
        now = time.time()
        with cls._cache_lock:
            cached = cls._match_cache.get(cache_key)
            if cached is None:
                return False, None
            expires_at, value = cached
            if expires_at <= now:
                cls._match_cache.pop(cache_key, None)
                return False, None
            cls._match_cache.move_to_end(cache_key)
            if value == cls._UNKNOWN_SENTINEL:
                return True, None
            return True, value

    @classmethod
    def _set_cached_candidate_id(cls, cache_key: str, candidate_id: Optional[str]) -> None:
        value = candidate_id or cls._UNKNOWN_SENTINEL
        expires_at = time.time() + cls._cache_ttl_seconds
        with cls._cache_lock:
            cls._match_cache[cache_key] = (expires_at, value)
            cls._match_cache.move_to_end(cache_key)
            while len(cls._match_cache) > cls._cache_max_entries:
                cls._match_cache.popitem(last=False)

    @classmethod
    def clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._match_cache.clear()

    @staticmethod
    def _infer_candidate_doc_type(rule: Dict[str, str]) -> Optional[PpapDocumentType]:
        doc_type = PpapDocumentType.infer_from_signals(
            filename=rule.get("rule_file_name", ""),
            content=rule.get("type", ""),
            minimum_score=1,
        )
        if doc_type == PpapDocumentType.UNKNOWN:
            return None
        return doc_type

    @classmethod
    def _build_semantic_key(
        cls,
        *,
        candidate_doc_type: Optional[PpapDocumentType],
        rule_file_name: str,
        rule_type: str,
    ) -> tuple[str, str]:
        doc_type_key = candidate_doc_type.name if candidate_doc_type is not None else cls._normalize_text(rule_type)
        file_key = cls._normalize_text(rule_file_name or rule_type)
        return doc_type_key, file_key

    @classmethod
    def _try_deterministic_match(
        cls,
        *,
        audit_file_name: str,
        audit_file_content: str,
        rules: List[Dict[str, str]],
    ) -> Optional[str]:
        filename_matches = PpapDocumentType.rank_from_signals(
            filename=audit_file_name,
        )
        filename_preferred_doc_types, filename_preferred_source = cls._select_preferred_doc_types(
            filename_matches=filename_matches,
            content_matches=[],
        )
        has_strong_filename_evidence = filename_preferred_source in {"filename_alias", "filename"}

        content_matches: List[PpapDocumentTypeMatch] = []
        if has_strong_filename_evidence:
            audit_matches = filename_matches
            preferred_doc_types = filename_preferred_doc_types
            preferred_source = filename_preferred_source
        else:
            content_matches = PpapDocumentType.rank_from_signals(
                content=audit_file_content,
            )
            audit_matches = PpapDocumentType.rank_from_signals(
                filename=audit_file_name,
                content=audit_file_content,
            )
            preferred_doc_types, preferred_source = cls._select_preferred_doc_types(
                filename_matches=filename_matches,
                content_matches=content_matches,
            )

        if not audit_matches:
            logger.info("Rule matching has no deterministic doc-type evidence: file=%s", audit_file_name)
            return None

        filename_scores = cls._score_map(filename_matches)
        content_scores = cls._score_map(content_matches)
        audit_scores = cls._score_map(audit_matches)
        logger.info(
            "Rule matching inferred doc-type evidence: file=%s filename_matches=%s content_matches=%s combined_matches=%s preferred_source=%s preferred_types=%s",
            audit_file_name,
            cls._summarize_type_matches(filename_matches),
            cls._summarize_type_matches(content_matches),
            cls._summarize_type_matches(audit_matches),
            preferred_source,
            [doc_type.get_type_name() for doc_type in sorted(preferred_doc_types, key=lambda item: item.name)] if preferred_doc_types else [],
        )

        evaluations: List[_CandidateScore] = []
        for rule in rules:
            candidate_doc_type = cls._infer_candidate_doc_type(rule)
            score = 0
            evidence: list[str] = []
            if candidate_doc_type is not None:
                filename_score = filename_scores.get(candidate_doc_type, 0)
                content_score = content_scores.get(candidate_doc_type, 0)
                combined_score = audit_scores.get(candidate_doc_type, 0)

                if preferred_doc_types is not None:
                    if candidate_doc_type in preferred_doc_types:
                        score += 1000 + (filename_score * 10) + content_score
                        evidence.append(f"preferred={preferred_source}")
                    else:
                        evidence.append(f"filtered_by={preferred_source}")
                elif filename_score > 0:
                    score += 100 + (filename_score * 10) + content_score
                elif combined_score > 0:
                    score += combined_score

                if filename_score > 0:
                    evidence.append(f"filename={candidate_doc_type.get_type_name()}({filename_score})")
                if content_score > 0:
                    evidence.append(f"content={candidate_doc_type.get_type_name()}({content_score})")
                if not filename_score and combined_score > 0:
                    evidence.append(f"combined={candidate_doc_type.get_type_name()}({combined_score})")

            evaluations.append(
                _CandidateScore(
                    candidate_id=rule["candidateId"],
                    rule_id=rule.get("ruleId", ""),
                    rule_file_name=rule.get("rule_file_name", ""),
                    candidate_doc_type=candidate_doc_type,
                    semantic_key=cls._build_semantic_key(
                        candidate_doc_type=candidate_doc_type,
                        rule_file_name=rule.get("rule_file_name", ""),
                        rule_type=rule.get("type", ""),
                    ),
                    score=score,
                    evidence=tuple(evidence),
                )
            )

        if not evaluations:
            return None

        evaluations.sort(
            key=lambda item: (
                item.score,
                item.candidate_doc_type.get_type_name() if item.candidate_doc_type is not None else "",
                item.rule_file_name,
                item.candidate_id,
            ),
            reverse=True,
        )
        best = evaluations[0]
        minimum_score = 1000 if preferred_doc_types is not None else 18
        if best.score < minimum_score:
            logger.info(
                "Rule matching falls back to LLM because deterministic evidence is weak: file=%s top_candidates=%s",
                audit_file_name,
                [item.as_log_dict() for item in evaluations[:3]],
            )
            return None

        best_candidates = [item for item in evaluations if item.score == best.score]
        semantic_keys = {item.semantic_key for item in best_candidates}
        if len(best_candidates) > 1 and len(semantic_keys) > 1:
            logger.info(
                "Rule matching falls back to LLM because deterministic evidence is ambiguous: file=%s top_candidates=%s",
                audit_file_name,
                [item.as_log_dict() for item in best_candidates[:3]],
            )
            return None

        logger.info(
            "Rule matching resolved deterministically: file=%s candidate_id=%s rule_id=%s score=%s evidence=%s",
            audit_file_name,
            best.candidate_id,
            best.rule_id,
            best.score,
            list(best.evidence),
        )
        return best.candidate_id

    @classmethod
    def _parse_candidate_id(cls, response: Dict[str, Any], candidate_to_rule_id: Dict[str, str]) -> Optional[str]:
        parsed = response
        if not isinstance(parsed, dict):
            logger.error("规则匹配返回内容不是 JSON 对象")
            return None

        candidate_id = parsed.get("candidateId")
        if isinstance(candidate_id, str):
            candidate_id = candidate_id.strip()
            if not candidate_id or candidate_id.lower() == "unknown":
                return None
            if candidate_id in candidate_to_rule_id:
                return candidate_id
            logger.error("返回 candidateId 不在候选集合中: %s", candidate_id)
            return None

        # 兼容旧格式（返回 ruleId）并映射到 candidateId。
        rule_id = parsed.get("ruleId")
        if isinstance(rule_id, str):
            cleaned_rule_id = rule_id.strip()
            if not cleaned_rule_id or cleaned_rule_id.lower() == "unknown":
                return None
            for candidate, mapped_rule_id in candidate_to_rule_id.items():
                if mapped_rule_id == cleaned_rule_id:
                    return candidate
            logger.error("返回 ruleId 不在候选集合中: %s", cleaned_rule_id)
        else:
            logger.error("规则匹配返回缺少 candidateId/ruleId 字段")
        return None

    def get_matched_rule_id(self, audit_file_name: str, audit_file_content: str, rules: List[Dict[str, Any]]) -> Optional[str]:
        """
        使用LLM根据审核文件信息获取最匹配的规则ID

        Args:
            audit_file_name: 审核文件名
            audit_file_content: 审核文件内容
            rules: 包含 file_name, type, ruleId 的可用规则列表

        Returns:
            匹配的ruleId，如果没有匹配则返回None
        """
        try:
            normalized_rules: List[Dict[str, str]] = []
            for index, rule in enumerate(rules, start=1):
                candidate_id = str(rule.get("candidateId") or f"R{index:02d}")
                normalized_rules.append(
                    {
                        "candidateId": candidate_id,
                        "rule_file_name": str(rule.get("rule_file_name", "")),
                        "type": str(rule.get("type", "")),
                        "ruleId": str(rule.get("ruleId", "")),
                    }
                )

            candidate_to_rule_id = {rule["candidateId"]: rule["ruleId"] for rule in normalized_rules if rule["ruleId"]}
            if not candidate_to_rule_id:
                logger.error("规则候选集为空或缺少 ruleId，无法匹配")
                return None

            match_content = self._build_content_excerpt(audit_file_content)
            deterministic_candidate_id = self._try_deterministic_match(
                audit_file_name=audit_file_name,
                audit_file_content=match_content,
                rules=normalized_rules,
            )
            if deterministic_candidate_id is not None:
                self._set_cached_candidate_id(
                    cache_key := self._build_cache_key(
                        audit_file_name=audit_file_name,
                        audit_file_content=match_content,
                        rules=normalized_rules,
                    ),
                    deterministic_candidate_id,
                )
                logger.debug("规则匹配缓存写入（deterministic, candidateId=%s, cache_key=%s）", deterministic_candidate_id, cache_key)
                return candidate_to_rule_id.get(deterministic_candidate_id)

            cache_key = self._build_cache_key(
                audit_file_name=audit_file_name,
                audit_file_content=match_content,
                rules=normalized_rules,
            )
            cache_hit, cached_candidate_id = self._get_cached_candidate_id(cache_key)
            if cache_hit:
                if cached_candidate_id is None:
                    logger.debug("规则匹配缓存命中（unknown）")
                    return None
                matched_rule_id = candidate_to_rule_id.get(cached_candidate_id)
                if matched_rule_id:
                    logger.debug("规则匹配缓存命中（candidateId=%s）", cached_candidate_id)
                    return matched_rule_id

            if self.client is None or self.prompt_manager is None:
                raise ValueError("rule_matching_ai_unavailable")

            # 构建提示
            system_prompt = self.prompt_manager.get_system_prompt("rule_matcher")
            user_prompt = self.prompt_manager.get_rule_matching_prompt(
                audit_file_name,
                match_content,
                [
                    {
                        "candidateId": rule["candidateId"],
                        "rule_file_name": rule["rule_file_name"],
                        "type": rule["type"],
                    }
                    for rule in normalized_rules
                ],
            )

            response = self.client("rule_matching", instructions=system_prompt, prompt=user_prompt, max_tokens=50, timeout=120).output

            if response:
                logger.debug("rule_matching_response_received")
                matched_candidate_id = self._parse_candidate_id(
                    response=response,
                    candidate_to_rule_id=candidate_to_rule_id,
                )
                self._set_cached_candidate_id(cache_key, matched_candidate_id)
                if matched_candidate_id is None:
                    return None
                return candidate_to_rule_id.get(matched_candidate_id)

            return None

        except Exception:
            raise ValueError("rule_matching_failed") from None


@dataclass(frozen=True)
class _CandidateScore:
    candidate_id: str
    rule_id: str
    rule_file_name: str
    candidate_doc_type: Optional[PpapDocumentType]
    semantic_key: tuple[str, str]
    score: int
    evidence: tuple[str, ...]

    def as_log_dict(self) -> Dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "ruleId": self.rule_id,
            "rule_file_name": self.rule_file_name,
            "type": self.candidate_doc_type.get_type_name() if self.candidate_doc_type is not None else None,
            "score": self.score,
            "evidence": list(self.evidence),
        }
