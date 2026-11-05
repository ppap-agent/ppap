"""
检具验收报告 PDF/Excel 委托审核代理。
"""

import logging
import re
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

from audit.contracts import AuditResultItem as AuditResult

from .checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent
from .pdf_excel_delegator_agent import PdfExcelDelegatorAgent

logger = logging.getLogger(__name__)


class CheckingFixtureApprovalAgent(PdfExcelDelegatorAgent):
    """检具验收报告专业审核代理。"""

    TARGET_EXCEL_AGENT_CLASS = CheckingFixtureApprovalExcelAgent
    FALLBACK_AGENT_RULES = [
        "检具清单与认可报告完整性审核",
        "检具清单标题规范性审核",
        "所附文件与结论一致性审核",
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
        from audit.engine.checkers.document_visual_checks import fixture_visual_check

        return fixture_visual_check(self, ith, attachments=False)

    def rule_3_logic_association(self, ith: int) -> AuditResult:
        from audit.engine.checkers.document_visual_checks import fixture_visual_check

        return fixture_visual_check(self, ith, attachments=True)
