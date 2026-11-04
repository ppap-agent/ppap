"""
检具验收报告 PDF/Excel 委托审核代理。
"""

import json
import logging
import re
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

from audit.contracts import AuditResultItem as AuditResult
from audit.kinds import PpapDocumentType

from .checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent
from .pdf_excel_delegator_agent import PdfExcelDelegatorAgent

logger = logging.getLogger(__name__)


class CheckingFixtureApprovalAgent(PdfExcelDelegatorAgent):
    """检具验收报告专业审核代理。"""

    DOC_TYPE = PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT
    TARGET_EXCEL_AGENT_CLASS = CheckingFixtureApprovalExcelAgent
    FALLBACK_AGENT_RULES = [
        "文件存在性与完整性检查",
        "前缀检查",
        "不合格项与结论的关联性检查",
    ]

    def fallback_rule_checkers(self, ith: int) -> Optional[AuditResult]:
        if ith == 0:
            return self.rule_1_completeness(ith)
        if ith == 2:
            return self.rule_3_logic_association(ith)
        return None

    def target_page_selector(self, target_keywords: List[str]) -> List[int]:
        found_pages = self._find_target_pages(
            target_keywords,
            strategies=[
                self._find_target_pages_by_textboxes,
                partial(
                    self._find_target_pages_by_text,
                    page_texts=self._get_pdf_page_texts(),
                ),
            ],
        )

        if not found_pages:
            logger.warning("未在PDF中找到 %s 相关页面, 将检查所有页面作为后备方案", target_keywords)
            return []

        target_page_indices = sorted(set(found_pages.values()))
        logger.debug("找到目标页面: %s, 页面索引: %s", found_pages, target_page_indices)

        # 分析PDF结构以提供额外的诊断信息
        structure_info = self._analyze_pdf_structure()
        if structure_info:
            logger.debug(
                "PDF结构信息: %s 个文本框, %s 个绘图元素",
                structure_info["total_text_boxes"],
                structure_info["total_drawings"],
            )

        return target_page_indices

    def _extract_document_structure(self) -> Dict[str, Any]:
        """
        分析PDF文档结构，提取检具清单和检具认可报告的位置信息

        Returns:
            包含文档结构信息的字典
        """
        try:
            file_content = self._get_pdf_file_content()
            if not file_content:
                raise OSError("无法读取检具认可报告原件")
            # Keyword search suppresses parser failures; validate the source first.
            self.pdf_extractor.extract_text_boxes(file_content)

            # 搜索关键术语
            key_terms = [
                "检具清单",
                "项目检具清单",
                "检具认可报告",
                "Checking Fixture Approval Report",
                "检具认可负责人",
                "所附文件",
                "Attachment",
                "结论",
                "Conclusion",
                "序号",
                "零件号",
                "零件名称",
                "检具号",
                "检具名称",
                "检具数量",
            ]

            search_results = self.pdf_extractor.search_text_in_boxes(file_content, key_terms, case_sensitive=False, no_whitespace=True)
            fixture_list_pages: set[int] = set()
            approval_report_pages: set[int] = set()
            attachment_section_pages: set[int] = set()

            # 分析文档结构和类型
            structure = {
                "has_fixture_list": len(search_results.get("检具清单", [])) > 0 or len(search_results.get("项目检具清单", [])) > 0,
                "has_approval_report": len(search_results.get("检具认可报告", [])) > 0,
                "has_responsible_person": len(search_results.get("检具认可负责人", [])) > 0,
                "has_attachment_section": len(search_results.get("所附文件", [])) > 0,
                "has_conclusion_section": len(search_results.get("结论", [])) > 0,
                "fixture_list_pages": fixture_list_pages,
                "approval_report_pages": approval_report_pages,
                "search_results": search_results,
            }

            # 额外检查制造检查表等其他文档类型
            text_content = str(file_content).lower()
            if "制造检查表" in text_content or "检查表" in text_content:
                structure["is_manufacturing_checklist"] = True
            else:
                structure["is_manufacturing_checklist"] = False

            # 判断是否为纯检具清单（没有认可报告部分）
            if structure["has_fixture_list"] and not structure["has_approval_report"]:
                structure["is_pure_checklist"] = True
            else:
                structure["is_pure_checklist"] = False

            # 确定关键页面
            for term in ["检具清单", "项目检具清单"]:
                for box in search_results.get(term, []):
                    fixture_list_pages.add(box.page)

            for term in ["检具认可报告"]:
                for box in search_results.get(term, []):
                    approval_report_pages.add(box.page)

            # 确定包含所附文件的页面
            structure["attachment_section_pages"] = attachment_section_pages
            for term in ["所附文件", "Attachment"]:
                for box in search_results.get(term, []):
                    attachment_section_pages.add(box.page)

            logger.debug(
                f"文档结构分析: 检具清单={structure['has_fixture_list']}, "
                f"认可报告={structure['has_approval_report']}, "
                f"负责人字段={structure['has_responsible_person']}, "
                f"制造检查表={structure['is_manufacturing_checklist']}, "
                f"纯检具清单={structure['is_pure_checklist']}, "
                f"所附文件页面={structure.get('attachment_section_pages', set())}, "
            )

            return structure

        except Exception as e:
            logger.error(f"文档结构分析失败: {e}")
            raise

    def _analyze_fixture_list_completeness(self, structure: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        分析检具清单的完整性

        Returns:
            (is_complete, issues): 是否完整和问题列表
        """
        issues = []

        if not structure.get("has_fixture_list"):
            issues.append("未找到检具清单部分")
            return False, issues

        try:
            file_content = self._get_pdf_file_content()
            text_boxes = self.pdf_extractor.extract_text_boxes(file_content)

            # 查找检具清单页面的文本框
            fixture_list_pages = structure.get("fixture_list_pages", set())

            for page_num in fixture_list_pages:
                page_boxes = [box for box in text_boxes if box.page == page_num]

                # 查找包含序号的行
                serial_numbers = []
                for box in page_boxes:
                    if re.search(r"^[1-9]\d*$", box.text.strip()):
                        serial_numbers.append((box.text.strip(), box.y0))

                logger.debug(f"检具清单第{page_num + 1}页找到{len(serial_numbers)}个序号")

                # 检查每行的完整性
                for serial_num, y_pos in serial_numbers:
                    # 查找同一行的其他字段（基于Y坐标相近性）
                    row_boxes = [box for box in page_boxes if abs(box.y0 - y_pos) < 5 and box.text.strip() != serial_num]

                    row_texts = [box.text.strip() for box in row_boxes if box.text.strip()]

                    # 检查是否有足够的非空字段（应该有零件号、零件名称、检具号、检具名称、检具数量）
                    if len(row_texts) < 4:  # 至少应该有4个必填字段
                        issues.append(f"序号{serial_num}行缺少必要字段，只有{len(row_texts)}个字段")

        except Exception as e:
            logger.error(f"检具清单完整性分析失败: {e}")
            issues.append(f"检具清单分析过程中发生错误: {e}")

        return len(issues) == 0, issues

    def _analyze_approval_report_completeness(self, structure: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        分析检具认可报告的完整性

        Returns:
            (is_complete, issues): 是否完整和问题列表
        """
        issues = []

        if not structure.get("has_approval_report"):
            issues.append("未找到检具认可报告部分")
            return False, issues

        # 检查必要字段
        search_results = structure.get("search_results", {})

        required_fields = {
            "零件号": ["零件号", "Part #"],
            "检具号": ["检具号", "Tool #"],
            "检具认可负责人": ["检具认可负责人"],
        }

        for field_name, search_terms in required_fields.items():
            found = any(len(search_results.get(term, [])) > 0 for term in search_terms)
            if not found:
                issues.append(f"检具认可报告中缺少必需字段: {field_name}")

        return len(issues) == 0, issues

    def rule_1_completeness(self, ith: int) -> AuditResult:
        """
        规则1：文件完整性检查

        识别是否有"检具清单"和"检具认可报告"，如果没有，则该项判定为NG；如果有则继续根据下述规则判定：
        1. "检具认可报告"中除"备注"一列之外的其他内容是否为空，如果为空，则该项判定为NG，如果不为空，则为PASS；
        2. "检具清单"中，“零件号”有内容时，对应行中除备注可以为空，其他信息均不可为空；否则判定为NG；
        """
        rule_data = self.get_rule_by_index(ith)
        if not rule_data:
            return self.create_error_result("无法获取规则数据", ith)

        try:
            # 分析文档结构
            structure = self._extract_document_structure()
            has_fixture_list = structure.get("has_fixture_list", False)
            has_approval_report = structure.get("has_approval_report", False)

            # 如果缺少任一部分，直接判定为NG
            if not has_fixture_list or not has_approval_report:
                missing_sections = []
                if not has_fixture_list:
                    missing_sections.append("检具清单")
                if not has_approval_report:
                    missing_sections.append("检具认可报告")

                return AuditResult(
                    classification="文件完整性",
                    item=self.agent_rules[0],
                    priority=rule_data.get("priority", "P0"),
                    status="NG",
                    suggestions=f"缺少必要部分: {', '.join(missing_sections)}",
                )

            # 如果两个部分都存在，进行详细的AI检查
            target_keywords = ["检具清单", "检具认可报告"]
            images = self.get_images_for_keywords(target_keywords)
            if not images:
                logger.warning("未能获取到检具清单和认可报告的相关图像，使用全部文档图像进行分析")
                images = self._get_images_for_analysis()

            # 使用AI进行详细的内容完整性检查
            system_prompt = """
请仔细检查以下图片中的“检具认可报告”和“检具清单”部分（除了“备注”行）。
你的任务是列出所有未填写内容的单元格。

请严格遵循以下规则判断“未填写”：
1.  **明确的空白单元格**：单元格内没有任何字符。
2.  **占位符内容**：单元格内只包含单一的斜杠"/"，或者“NA”、“N/A”这类表示“不适用”或“无”的文本。
3.  **视为“填写”的情况**：
    *   单元格内包含任何有意义的文本、数字、字母、符号组合（例如“A公司”、“123456”、“ABC”、“2025年6月10日”）都视为**已填写**。
    *   “理想采购技术中心检具负责人”一行中的文本，如“张三”、“2025年6月10日”等，都视为**已填写**。
    *   “备注Remark”列允许为空，不计入未填写范围。
    *   单元格内包含任何文本、数字、字母、符号组合。
    *   特别是，包含**字母、数字和连字符“-”的组合**（例如 “LAC-3506011”、“LAC-3506011-1 检具”、“LAC-3506011-2 JJ”、“3D REV 05”）都视为**已填写**。
    *   单元格内包含纯数字（例如“1”）。
    *   “备注”列允许为空，不计入未填写范围。

只检查有序号的行，空行可以忽略"""

            user_prompt = """请根据上述规则进行详细检查，严格按照以下JSON格式返回：
            {
                "status": "PASS/NG",
                "suggestions": "如果 status 为 NG，详细说明哪些字段为空或不完整"
            }"""

            response = self._analyze_images_with_prompt(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                images=images,
                json_format=True,
            )

            result_dict = json.loads(response.replace("```json", "").replace("```", "").strip())
            result_dict.update(
                {
                    "classification": "文件完整性",
                    "item": self.agent_rules[0],
                    "priority": rule_data.get("priority", "P0"),
                }
            )
            return AuditResult(**result_dict)

        except Exception as e:
            logger.error(f"规则1检查失败: {e}")
            return self.create_error_result(f"文件完整性检查过程中发生错误: {e}", ith)

    def rule_3_logic_association(self, ith: int) -> AuditResult:
        """
        逻辑关联性检查

        若'所附文件'区域，有对应文件勾选'不合格'或'非完成'，则'结论'区域内容必须不能为空

        关键理解：
        - 只有当所附文件中有否定状态时，才要求结论必须有内容
        - 如果所有文件都是正常状态，结论区域可以为空 - 这应该是PASS
        - 可读原件缺少必需结构为NG；读取、调用或输出协议失败为ERR
        """
        rule_data = self.get_rule_by_index(ith)
        if not rule_data:
            return self.create_error_result("无法获取规则数据", ith)

        try:
            # 首先检查文档结构来判断是否适用此规则
            structure = self._extract_document_structure()
            is_pure_checklist = structure.get("is_pure_checklist", False)
            is_manufacturing_checklist = structure.get("is_manufacturing_checklist", False)
            has_approval_report = structure.get("has_approval_report", False)
            has_attachment_section = structure.get("has_attachment_section", False)

            # 如果是纯检具清单或制造检查表，则不适用此规则
            if is_pure_checklist or is_manufacturing_checklist:
                return AuditResult(
                    classification="逻辑关联性",
                    item=self.agent_rules[2],
                    priority=rule_data.get("priority", "P0"),
                    status="NG",
                    suggestions="文档类型不符合逻辑关联性检查要求：需要包含检具认可报告和所附文件部分",
                )

            # 如果文档没有认可报告结构或缺少关键部分，则不适用此规则
            if not has_approval_report or not has_attachment_section:
                return AuditResult(
                    classification="逻辑关联性",
                    item=self.agent_rules[2],
                    priority=rule_data.get("priority", "P0"),
                    status="NG",
                    suggestions="缺少逻辑关联性检查的必要部分：检具认可报告或所附文件",
                )

            # 获取包含所附文件部分的页面图像
            attachment_pages = structure.get("attachment_section_pages", set())
            if not attachment_pages:
                logger.warning("未找到包含所附文件的具体页面，使用全部图像")
                images = self._get_images_for_analysis()
            else:
                logger.debug(f"找到包含所附文件的页面: {attachment_pages}")
                # 只获取包含所附文件部分的页面图像
                attachment_page_indices = list(attachment_pages)
                images = self._get_specific_page_images(attachment_page_indices)

            if not images:
                return AuditResult(
                    classification="逻辑关联性",
                    item=self.agent_rules[2],
                    priority=rule_data.get("priority", "P0"),
                    status="ERR",
                    suggestions="无法获取相关信息进行逻辑关联性检查",
                )

            system_prompt = """你是一位专业的PPAP质量审核员，专门检查检具验收报告的逻辑关联性。

核心规则理解：
1. 查找"所附文件"区域或相关的附件清单
2. NG: if any attachment is marked as "不合格（√ ）" or "未完成（√ ）" and the "结论" is empty.
3. PASS: if all attachments are normal (合格（√ ）/完成（√ ）/OK（√ ）) or 无（√ ）or  (合格（Y ）/完成（Y ）/OK（Y ）) or 无（Y ）

特别注意文档类型判断：
- 如果文档主要是"检具清单"且没有"认可报告"部分，应返回NG（缺少必需结构）
- 如果文档是"制造检查表"类型，应返回NG（文档类型不符合要求）
- 只有包含完整认可报告结构的文档才适用此规则
"""

            user_prompt = """
请严格按照以下JSON格式返回：
{
    "status": "PASS/NG/ERR",
    "suggestions": "详细说明检查结果：文档类型判断，是否有否定状态勾选，结论区域内容情况，判定理由"
}"""

            response = self._analyze_images_with_prompt(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                images=images,
                json_format=True,
            )

            result_dict = json.loads(response.replace("```json", "").replace("```", "").strip())
            result_dict.update(
                {
                    "classification": "逻辑关联性",
                    "item": self.agent_rules[2],
                    "priority": rule_data.get("priority", "P0"),
                }
            )
            return AuditResult(**result_dict)

        except Exception as e:
            logger.error(f"规则3检查失败: {e}")
            # 发生异常时，尝试基于文档内容进行简单判断
            try:
                structure = self._extract_document_structure()
                if not structure.get("has_approval_report", False):
                    return AuditResult(
                        classification="逻辑关联性",
                        item=self.agent_rules[2],
                        priority=rule_data.get("priority", "P0"),
                        status="ERR",
                        suggestions="文档结构不适用于逻辑关联性检查，处理异常",
                    )
            except Exception as fallback_exc:
                logger.debug("规则3逻辑关联性异常回退失败: %s", fallback_exc)

            return self.create_error_result(f"逻辑关联性检查过程中发生错误: {e}", ith)
