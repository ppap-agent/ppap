"""提示管理器 - 统一管理所有AI提示模板"""

import json
import logging
from typing import Any, Dict, List

from audit.contracts import AuditInput, RuleInput, RuleItem

logger = logging.getLogger(__name__)


class PromptManager:
    """统一的提示管理器"""

    # 系统提示模板
    SYSTEM_PROMPTS = {
        "audit_expert": "你是一个专业的PPAP质量管理文件审核专家，负责根据指定的规则对上传的文件进行合规性检查。",
        "document_analyst": "你是一个专业的文档分析师，擅长从技术文档中提取关键信息。",
        "ppap_expert": "You are an expert in the Production Part Approval Process (PPAP). Your task is to classify documents into one of the 18 official PPAP types.",
        "rule_extractor": (
            "You are a specialized AI agent that converts quality control rule documents into a structured, machine-readable JSON format. Ensure your entire output is a single, valid JSON object."
        ),
        "rule_matcher": "You are an expert in matching audit documents to compliance rules. Your task is to identify the single best rule ID for a given audit file.",
    }

    # specific_rule = {
    #     "id": "1",
    #     "category": "任务项",
    #     "priority": null,
    #     "rule_to_check": "任务项一和任务项二都必须选OK",
    #     "type_of_rule": "text",
    # }
    #
    @staticmethod
    def get_audit_prompt(
        audit_file: AuditInput,
        rule: RuleInput,
        specific_rule: RuleItem,
        agentic_prompt: str = "",
    ) -> str:
        """构建审核提示"""
        try:
            file_content = (getattr(audit_file, "content", "") or "")[:20000]
            agentic_section = ""
            if agentic_prompt:
                agentic_section = (
                    "专业代理审核结果:\n\n" + agentic_prompt + "\n- 如果不满足检查条件，则输出 ERR；\n" + "- 如果满足检查条件，但是检查结果不满足约束，返回 NG\n" + "请严格遵循上面的结果。\n"
                )
            return (
                f"""
请根据以下规则对审核文件进行合规性检查：

规则信息：
- 规则编号：{specific_rule.id}
- 需要检查的具体规则：{specific_rule.rule_to_check}
- 适用客户: {rule.customers}
- 适用车型: {rule.models}
- 适用零件: {rule.parts}

审核文件信息：
- 客户: {audit_file.customer}
- 车型: {audit_file.model}
- 零件: {audit_file.part}
- 文件元数据: {audit_file.file_metadata}

{("文件内容:\n\n" + file_content) if agentic_prompt == "" else ""}

{agentic_section}


请严格按照以下格式返回审核结果，不要添加其他内容：

classification: [内容分类，如：基本信息、质量检查、合规性检查等]
item: [具体审核项，如：文件完整性检查、数据有效性等]
priority: [优先级，只能是P0、P1、P2]
status: [状态，只能是PASS、NG、ERR]
suggestions: [修改建议，如果状态是PASS可以为空]
"""
                + """
return exact json format exactly as below example:
{
    "classification": "基本信息",
    "item": "文件完整性检查",
    "priority": "P0",
    "status": "PASS",
    "suggestions": "修改建议"
}


请进行详细的合规性检查并严格按照上述格式返回结果。
如果你没有能力或者信息不足以完成检查，请务必返回 ERR。
"""
            )
        except Exception:
            logger.exception("构建审核提示失败")
            raise

    @staticmethod
    def get_document_metadata_prompt(file_content: str, file_name: str) -> str:
        """构建文档元数据提取提示"""
        file_name_prompt = f'文件名是: "{file_name}".' if file_name else ""
        return f"""
请分析以下文档内容和文件名（如果提供），提取关键信息：

{file_name_prompt}

文档内容：
{file_content[:4000]}...

请提取以下信息并以JSON格式返回：
- customer: 客户名称
- model: 车型
- part: 零件名称

如果某个信息无法确定，请返回null。
"""

    @staticmethod
    def get_document_classification_prompt(content: str, ppap_types_str: str) -> str:
        """构建文档分类提示"""
        return f"""分析包含规则审核逻辑的文档的 PPAP 类型，从下列候选：

PPAP Types:
{ppap_types_str}

中返回最贴近的规则名称。如果没有匹配的，返回 "未知"

Document Content:
'''
{content[:4000]}
'''"""

    @staticmethod
    def get_rule_extraction_prompt(content: str, doc_type_name: str) -> str:
        """构建规则提取提示"""
        doc_type_label = doc_type_name or "未知文档"
        doc_type_guard = ""
        if doc_type_label != "未知文档":
            doc_type_guard = f"当前文档已经被判定为“{doc_type_label}”规则文件，不要改判成其他文档类型。"

        return f"""
以下文档是一个“{doc_type_label}”的质量控制规则文件。你的任务是从文档中提取元数据和所有规则，并将它们格式化为单个有效的JSON对象。
{doc_type_guard}

该JSON对象必须包含以下键：
- `"rules"`: 规则对象的数组。

**关键拆分 / 合并逻辑：**
1. 文档可能来自 Word、Excel、Markdown 或普通文本。你必须根据源内容本身识别规则边界，不要假设固定文件格式。
2. 如果文档内容包含 `<SHEET>`、`<TABLE>`、`<ROW>`、`<CELL>`、`<P>`，这些只是原始结构，不是已经分类好的规则。请根据标题、编号、表头、行列位置、审核条款、规则说明等源内容判断顶层规则。
3. 同一个 `CELL` 内的换行、列定义、字段说明、规则描述和子编号通常属于同一条规则；不要把“列：...”“字段：...”“检查对象：...”“规则描述：”单独拆成规则。
4. Markdown 表格、编号列表、标题段落也可能表达规则。请优先保留源文档中的顶层编号；没有明确编号时，再按顶层规则顺序生成稳定 id。
5. 结果中的 `rules` 必须按源文档中的顶层规则顺序输出，不要跳号，不要按 `category` 重新从 1 编号。

对于"rules"数组中的每个规则，创建一个包含以下确切键的JSON对象："id"、"category"、"priority"、"rule_to_check"、"type_of_rule"

`rules` 数组中的每个元素都必须是一个 JSON 对象，绝不能是字符串、数字、数组或 `null`。例如，`"PFMEA"` 这种裸字符串是非法输出。

- **字段合并**：
  - **`rule_to_check`**：以该顶层规则对应的规则说明或正文为主，补充同一顶层规则下的未编号子项、步骤或“实施方案策划”内容。
  - **`id`**：优先使用源文档中的顶层规则编号；如果源内容没有明确编号但规则边界清晰，则按顶层规则顺序生成稳定 id。
  - **`category`**：使用该规则的审核主题或最贴近的规则名称，保持简洁，不要把多条平级规则合并成一个笼统名称。
  - **`priority`**：如果文档没有明确优先级，则返回 `null`。

- **`type_of_rule`**："image"、"text"或"table"，根据规则应如何被检查来确定。如果一条规则同时涉及检查文本和表格（如规则1），则优先选择"table"，因为它更具体。

源文本可能来自结构化行列、Markdown、编号列表、标题段落或普通文本，可能存在换行错位、编号缺失、标题缺失等情况。你必须优先识别顶层规则边界，再生成结构化结果。

请只输出一个合法 JSON 对象，不要输出额外解释。

最小合法输出示例：
{{
  "rules": [
    {{
      "id": "1",
      "category": "文件状态审核",
      "priority": null,
      "rule_to_check": "根据文档中的规则说明填写完整检查要求。",
      "type_of_rule": "table"
    }}
  ]
}}

从规则文档中提取规则。
文档内容：

'''
{content[:20000]}
'''
"""

    @staticmethod
    def get_rule_matching_prompt(audit_file_name: str, audit_file_content: str, rules: List[Dict[str, Any]]) -> str:
        """构建规则匹配提示"""
        rules_json = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_excerpt = " ".join(str(audit_file_content or "")[:2000].split())[:600]
        return f"""
Please analyze the following audit file information and determine the most appropriate candidate ID from the provided list.

Audit File Name: "{audit_file_name}"

Available Rules (JSON):
{rules_json}

Audit File Content Excerpt:
{content_excerpt}...

Based on the file name and content, which candidate ID is the best match?
Do not assume the only candidate is always correct. If the evidence is weak or ambiguous, return "unknown".
Return exact json format with candidateId as the only field, no other text or explanation.
{{"candidateId":"R01"}} or {{"candidateId":"unknown"}}
"""

    @staticmethod
    def get_system_prompt(prompt_type: str) -> str:
        """获取系统提示"""
        return PromptManager.SYSTEM_PROMPTS.get(prompt_type, "")

    @staticmethod
    def get_image_audit_prompt(audit_file: Any, rule: Any, specific_rule: RuleItem) -> str:
        """构建图像审核提示"""
        return f"""请分析这些图像并根据以下规则进行审核：

审核规则信息：
- 规则ID: {specific_rule.id}
- 分类: {specific_rule.category}
- 优先级: {specific_rule.priority}
- 检查要求: {specific_rule.rule_to_check}

审核文件信息：
- 文件ID: {audit_file.audit_id}
- 客户: {audit_file.customer}
- 车型: {audit_file.model}
- 零件: {audit_file.part}

请仔细观察图像中的细节，特别关注：
1. 是否有相关的标记、印章、签名等视觉元素
2. 表格或图表的完整性和正确性
3. 图像质量和清晰度
4. 任何可能的缺陷或问题

请按照以下JSON格式返回审核结果：
{{
    "classification": "分类名称",
    "item": "具体审核项目",
    "priority": "优先级(P0/P1/P2)",
    "status": "状态(PASS/NG/ERR)",
    "suggestions": "修改建议",
    "image_analysis": "图像分析详情"
}}"""

    @staticmethod
    def get_rule2_check_prompt(audit_file: Any, rule: Any, specific_rule: Any) -> str:
        return """识别图片中重要度区分是否被标记，是返回PASS，否则返回NG。
请按照以下JSON格式返回审核结果：
{
    "classification": "分类名称",
    "item": "具体审核项目",
    "priority": "优先级(P0/P1/P2)",
    "status": "状态(PASS/NG/ERR)",
    "suggestions": "修改建议(PASS时可以为空)",
    "image_analysis": "图像分析详情"
}
"""

    @staticmethod
    def get_rule11_check_prompt(audit_file: Any, rule: Any, specific_rule: Any) -> str:
        return """请分析图片中是否包含企业印章或签名，有印章或签名的情况下，请返回状态为PASS。如果图像中没有企业印章或签名，请返回状态为NG，并提供修改建议。
请按照以下JSON格式返回审核结果：
{
    "classification": "分类名称",
    "item": "具体审核项目",
    "priority": "优先级(P0/P1/P2)",
    "status": "状态(PASS/NG/ERR)",
    "suggestions": "修改建议(PASS时可以为空)",
    "image_analysis": "图像分析详情"
}
"""
