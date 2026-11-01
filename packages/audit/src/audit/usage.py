"""Token使用跟踪器 - 用于收集和聚合LLM调用的token使用情况"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    """单次LLM调用的token使用情况"""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: Optional[int] = None  # 可选拆分；缺少回执不当作零
    total_tokens: int = 0
    model_name: str = ""
    operation: str = ""  # 操作描述，如 "document_analysis", "rule_matching"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RequestTokenSummary:
    """请求级别的token使用汇总"""

    request_id: str
    total_input_tokens: Optional[int] = 0
    total_output_tokens: Optional[int] = 0
    total_thinking_tokens: Optional[int] = 0
    total_tokens: Optional[int] = 0
    unreported_calls_count: int = 0
    llm_calls: List[TokenUsage] = field(default_factory=list)
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "request_id": self.request_id,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_thinking_tokens": self.total_thinking_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls_count": len(self.llm_calls) + self.unreported_calls_count,
            "unreported_calls_count": self.unreported_calls_count,
            "usage_complete": self.unreported_calls_count == 0,
            "llm_calls": [
                {
                    "operation": call.operation,
                    "model_name": call.model_name,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "thinking_tokens": call.thinking_tokens,
                    "total_tokens": call.total_tokens,
                    "timestamp": call.timestamp.isoformat(),
                }
                for call in self.llm_calls
            ],
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": ((self.end_time - self.start_time).total_seconds() if self.end_time else None),
        }


class TokenTracker:
    """线程安全的Token使用跟踪器"""

    def __init__(self) -> None:
        self._request_summaries: Dict[str, RequestTokenSummary] = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    def start_request_tracking(self, request_id: Optional[str] = None) -> str:
        """开始跟踪一个请求的token使用"""
        if request_id is None:
            request_id = str(uuid.uuid4())

        with self._lock:
            self._request_summaries[request_id] = RequestTokenSummary(request_id=request_id)

        # 在线程本地存储中设置当前请求ID
        self._local.current_request_id = request_id
        logger.debug(f"开始跟踪请求token使用: {request_id}")
        return request_id

    def end_request_tracking(self, request_id: str) -> RequestTokenSummary:
        """结束跟踪并返回汇总"""
        with self._lock:
            if request_id in self._request_summaries:
                summary = self._request_summaries[request_id]
                summary.end_time = datetime.now()

                # 计算总计
                summary.total_input_tokens = sum(call.input_tokens for call in summary.llm_calls)
                summary.total_output_tokens = sum(call.output_tokens for call in summary.llm_calls)
                summary.total_thinking_tokens = None if any(call.thinking_tokens is None for call in summary.llm_calls) else sum(call.thinking_tokens or 0 for call in summary.llm_calls)
                summary.total_tokens = sum(call.total_tokens for call in summary.llm_calls)
                if summary.unreported_calls_count:
                    summary.total_input_tokens = None
                    summary.total_output_tokens = None
                    summary.total_thinking_tokens = None
                    summary.total_tokens = None

                logger.debug(
                    f"请求 {request_id} token使用汇总: "
                    f"总计={summary.total_tokens}, "
                    f"输入={summary.total_input_tokens}, "
                    f"输出={summary.total_output_tokens}, "
                    f"思考={summary.total_thinking_tokens}, "
                    f"SDK可见调用次数={len(summary.llm_calls) + summary.unreported_calls_count}, "
                    f"缺用量回执次数={summary.unreported_calls_count}"
                )

                return summary
            else:
                logger.warning(f"未找到请求ID的跟踪记录: {request_id}")
                return RequestTokenSummary(request_id=request_id)

    def get_current_request_id(self) -> Optional[str]:
        """获取当前线程的请求ID"""
        return getattr(self._local, "current_request_id", None)

    def set_current_request_id(self, request_id: Optional[str]) -> None:
        """设置当前线程的请求ID"""
        self._local.current_request_id = request_id

    def record_token_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: Optional[int] = None,
        model_name: str = "",
        operation: str = "",
        request_id: Optional[str] = None,
    ) -> None:
        """记录token使用情况"""
        if request_id is None:
            request_id = self.get_current_request_id()

        if request_id is None:
            # logger.warning("无法记录token使用: 未找到请求ID")
            return

        # Responses includes reasoning tokens in output_tokens.
        total_tokens = input_tokens + output_tokens
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            total_tokens=total_tokens,
            model_name=model_name,
            operation=operation,
        )

        with self._lock:
            if request_id in self._request_summaries:
                self._request_summaries[request_id].llm_calls.append(usage)
                logger.debug(f"记录token使用: {operation} - {total_tokens} tokens")
            else:
                logger.warning(f"未找到请求ID的跟踪记录: {request_id}")

    def record_unreported_usage(self) -> None:
        """A visible SDK call without a valid receipt has unknown token usage."""
        request_id = self.get_current_request_id()
        if request_id is None:
            return
        with self._lock:
            summary = self._request_summaries.get(request_id)
            if summary is not None:
                summary.unreported_calls_count += 1

    def get_request_summary(self, request_id: str) -> Optional[RequestTokenSummary]:
        """获取请求的token使用汇总"""
        with self._lock:
            return self._request_summaries.get(request_id)

    def cleanup_request(self, request_id: str) -> None:
        """清理请求的跟踪数据"""
        with self._lock:
            self._request_summaries.pop(request_id, None)
        logger.debug(f"清理请求跟踪数据: {request_id}")


# 全局单例实例
_token_tracker = TokenTracker()


def get_token_tracker() -> TokenTracker:
    """获取全局token跟踪器实例"""
    return _token_tracker


# 便利函数
def start_request_tracking(request_id: Optional[str] = None) -> str:
    """开始跟踪请求的token使用"""
    return _token_tracker.start_request_tracking(request_id)


def end_request_tracking(request_id: str) -> RequestTokenSummary:
    """结束跟踪并返回汇总"""
    return _token_tracker.end_request_tracking(request_id)


def record_token_usage(
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: Optional[int] = None,
    model_name: str = "",
    operation: str = "",
    request_id: Optional[str] = None,
) -> None:
    """记录token使用情况"""
    _token_tracker.record_token_usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        model_name=model_name,
        operation=operation,
        request_id=request_id,
    )


def record_unreported_usage() -> None:
    """Record one SDK call whose provider usage receipt is unavailable."""
    _token_tracker.record_unreported_usage()


def get_current_request_id() -> Optional[str]:
    """获取当前线程的请求ID"""
    return _token_tracker.get_current_request_id()


def set_current_request_id(request_id: Optional[str]) -> None:
    """设置当前线程的请求ID"""
    _token_tracker.set_current_request_id(request_id)
