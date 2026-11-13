"""提示管理器 - 统一管理所有AI提示模板"""

import json
import logging
from typing import Any, Dict, List

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
    def get_submitted_document_classification_prompt(content: str, ppap_types_str: str) -> str:
        """Classify a submitted PPAP document, not a rule document."""
        return f"""判断下面这份提交审核的 PPAP 文件本身属于哪一类，从下列候选中选择。每项冒号后是该类文件审核规则的要点，说明这类文件包含什么。
文件里引用、列举或提到其他文件的名称（如封面清单、参考文件、缩写），不代表本文件就是那一类。

候选类型：
{ppap_types_str}

只返回冒号前的类型名称；没有一项符合时返回 "未知"。

文件内容：
'''
{content[:4000]}
'''"""

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
