"""
基础审核代理类

定义了所有PPAP审核代理的通用接口和共享功能。
"""

import logging
import os
from abc import ABC
from typing import Any, Dict, Optional
from urllib.parse import unquote

from audit.contracts import (
    AuditInput,
    GroupContextInput,
    RuleInput,
    RuleItem,
)
from audit.contracts import (
    AuditResultItem as AuditResult,
)
from audit.kinds import PpapDocumentType

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    PPAP审核代理基类

    所有具体的PPAP文档类型审核代理都应该继承此类并实现相应的方法。
    """

    def __init__(
        self,
        audit_file: AuditInput,
        rule: RuleInput,
        group_context: Optional[GroupContextInput] = None,
    ) -> None:
        """
        初始化代理

        Args:
            audit_file: 要审核的文件
            rule: 审核规则
        """
        self.audit_file = audit_file
        self.rule = rule
        self.group_context = group_context
        self.logger = logging.getLogger(self.__class__.__name__)
        self.agent_rules: list[str] = []

    def get_related_files(self, doc_type: PpapDocumentType) -> list[AuditInput]:
        if not self.group_context:
            return []
        return self.group_context.get_files_by_type(doc_type)

    def check(self, ith: int) -> Optional[AuditResult]:
        """
        执行第 ith 条规则的默认审核逻辑。子类可以重写此方法来实现专门的验证逻辑。如果子类没有实现，则使用默认的AI审核逻辑。

        Args:
            ith: 规则索引（从0开始）

        Returns:
            AuditResult: 审核结果
        """
        try:
            self.log_audit_start(ith)

            # 获取具体规则
            rules = getattr(self.rule, "rules", [])
            if not isinstance(rules, list) or ith >= len(rules):
                return self.create_error_result(f"无法获取规则索引 {ith}", ith)

            specific_rule: RuleItem = RuleItem.model_validate(rules[ith])
            logger.debug(f"开始审核文件: {self.audit_file.audit_id} 使用规则: {specific_rule}")

            # 使用AI服务进行审核
            from audit.engine.checkers.client import AIClient
            from audit.engine.checkers.prompts import PromptManager

            # 使用AI服务进行审核
            client = AIClient()
            prompt_manager = PromptManager()
            system_prompt = prompt_manager.get_system_prompt("audit_expert")

            if specific_rule.type_of_rule in ["image"] and not getattr(self.audit_file, "file_url", "").endswith(".xlsx"):
                # 处理图像类型的规则
                return self._process_image_rule(specific_rule, system_prompt, prompt_manager)
            else:
                # 处理文本和表格类型的规则
                user_prompt = prompt_manager.get_audit_prompt(self.audit_file, self.rule, specific_rule, "")

                # 调用标准AI服务
                response = client.structured_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=3000,
                    temperature=0.1,
                    json_format=True,
                )

            # 解析响应
            assert response != "", "调用文本分析 AI 服务响应为空"
            result = self._parse_ai_response(
                response,
                default={"item": getattr(specific_rule, "category", "默认任务项")},
            )
            logger.debug(f"规则处理成功: {self.audit_file.audit_id}")

            self.log_audit_result(result, ith)
            return result

        except Exception:
            logger.exception("AI审核失败")
            return self._failed_audit_result("大模型引擎未响应")

    @property
    def document_type_name(self) -> str:
        """获取文档类型名称"""
        return self.__class__.__name__.replace("Agent", "")

    def get_rule_by_index(self, ith: int) -> Optional[Dict[str, Any]]:
        """
        获取指定索引的规则

        Args:
            ith: 规则索引

        Returns:
            规则字典或None
        """
        try:
            rules = getattr(self.rule, "rules", [])
            if isinstance(rules, list) and 0 <= ith < len(rules):
                rule = rules[ith]
                if isinstance(rule, dict):
                    return rule
                return RuleItem.model_validate(rule).model_dump()
            return None
        except Exception:
            self.logger.exception("获取规则失败")
            return None

    def create_error_result(self, error_msg: str, ith: int = -1) -> AuditResult:
        """
        创建错误结果

        Args:
            error_msg: 错误消息
            ith: 规则索引

        Returns:
            AuditResult: 错误结果
        """
        return AuditResult(
            classification="规则审核",
            item=f"{self.document_type_name} - 规则{ith + 1}",
            priority="P0",
            status="ERR",
            suggestions=f"审核过程中出现错误: {error_msg}",
        )

    def _process_image_rule(self, specific_rule: Any, system_prompt: str, prompt_manager: Any) -> AuditResult:
        """Keep unsupported image semantics explicit, with no model fallback."""
        return AuditResult(
            classification="图形检查",
            item=getattr(specific_rule, "name", "原件图形证据") or "原件图形证据",
            priority="P0",
            status="UNABLE",
            suggestions="此图形规则缺少可验证的Paddle事实，需人工确认；不自动调用LLM视觉。",
            checks_performed=0,
        )

    def _parse_ai_response(
        self,
        response_text: str,
        default: Optional[Dict[str, Any]] = None,
    ) -> AuditResult:
        """解析AI响应"""
        try:
            import json

            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                raise TypeError("AI响应不是JSON对象")

            payload: Dict[str, Any] = dict(default or {})
            payload.update(parsed)
            return AuditResult(**payload)
        except Exception:
            logger.exception("failed to load response to AuditResult: %s", response_text)
            return self._failed_audit_result("大模型引擎返回了错误信息")

    def _failed_audit_result(self, error_msg: str) -> AuditResult:
        return AuditResult(
            classification="基本信息",
            item="",
            priority="P0",
            status="ERR",
            suggestions=error_msg,
        )

    def log_audit_start(self, ith: int) -> None:
        """记录审核开始"""
        self.logger.debug(f"\t\t开始审核 {self.document_type_name} - 文件: {os.path.basename(unquote(getattr(self.audit_file, 'file_url', '')))}, 规则: {ith + 1}")

    def log_audit_result(self, result: AuditResult, ith: int) -> None:
        """记录审核结果"""
        self.logger.debug(f"\t\t审核完成 {self.document_type_name} - 规则: {ith + 1}, 状态: {result.status}, 项目: {result.item}")
