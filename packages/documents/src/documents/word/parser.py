"""DOCX OOXML parser that preserves ordered blocks and logical table grids."""

from __future__ import annotations

import base64
import mimetypes
import posixpath
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Iterable, Literal, cast
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .models import (
    WordBlock,
    WordDiagnostic,
    WordDocument,
    WordImageEvidence,
    WordParagraphBlock,
    WordSource,
    WordTableBlock,
    WordTableCell,
    WordVisualEvidence,
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_MC = f"{{{_MC_NS}}}"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL = f"{{{_REL_NS}}}"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_R = f"{{{_R_NS}}}"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_W14 = f"{{{_W14_NS}}}"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_A = f"{{{_A_NS}}}"
_V_NS = "urn:schemas-microsoft-com:vml"
_V = f"{{{_V_NS}}}"
_REGION = Literal["body", "header", "footer", "textbox", "cell"]
_SUPPORTED_MC_NAMESPACES = {
    _W_NS,
    _W14_NS,
    "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "urn:schemas-microsoft-com:vml",
}
_RELATIONSHIP_TYPES = {
    "header": {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/header",
    },
    "footer": {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/footer",
    },
}
_IMAGE_RELATIONSHIP_TYPES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/image",
}
_CHECKED_CHARACTERS = frozenset("☒☑✓✔■")
_UNCHECKED_CHARACTERS = frozenset("☐□")


def _tag(name: str) -> str:
    return f"{_W}{name}"


class _DocxStructureParser:
    def __init__(self) -> None:
        self.diagnostics: list[WordDiagnostic] = []
        self.element_namespaces: dict[int, dict[str, str]] = {}
        self.visual_evidence: list[WordVisualEvidence] = []
        self.relationships_by_part: dict[str, dict[str, tuple[str, str | None, str]]] = {}
        self.package_names: set[str] = set()

    def parse(self, file_content: bytes) -> WordDocument:
        try:
            with ZipFile(BytesIO(file_content), "r") as archive:
                names = set(archive.namelist())
                self.package_names = names
                if "word/document.xml" not in names:
                    self._diagnose(
                        "missing_document_part",
                        "DOCX 包缺少 word/document.xml。",
                        WordSource("word/document.xml", "body", "document"),
                    )
                    return self._document(())

                document_root = self._parse_xml(archive.read("word/document.xml"), "word/document.xml", "body")
                if document_root is None:
                    return self._document(())

                blocks = self._parse_document_root(document_root, "word/document.xml")
                relationships = self._document_relationships(archive, names)
                self.relationships_by_part["word/document.xml"] = relationships
                header_parts = self._referenced_parts(document_root, relationships, names, "header")
                footer_parts = self._referenced_parts(document_root, relationships, names, "footer")
                headers = self._parse_parts(archive, header_parts, "header")
                footers = self._parse_parts(archive, footer_parts, "footer")
                for part in (*header_parts, *footer_parts):
                    self.relationships_by_part[part] = self._part_relationships(archive, names, part)
                return self._document(blocks, headers, footers)
        except (BadZipFile, KeyError, OSError, RuntimeError, ValueError) as exc:
            self._diagnose(
                "invalid_docx_package",
                f"无法读取 DOCX 包: {exc}",
                WordSource("package", "body", "package"),
            )
            return self._document(())

    def _document(
        self,
        blocks: tuple[WordBlock, ...],
        headers: tuple[WordBlock, ...] = (),
        footers: tuple[WordBlock, ...] = (),
    ) -> WordDocument:
        visual_evidence = tuple(self._resolved_visual_evidence())
        diagnostics = tuple(self.diagnostics)
        return WordDocument(
            valid=not any(item.severity == "error" for item in diagnostics),
            blocks=blocks,
            headers=headers,
            footers=footers,
            visual_evidence=visual_evidence,
            diagnostics=diagnostics,
        )

    def _part_relationships(
        self,
        archive: ZipFile,
        names: set[str],
        part: str,
    ) -> dict[str, tuple[str, str | None, str]]:
        rels_part = posixpath.join(posixpath.dirname(part), "_rels", f"{posixpath.basename(part)}.rels")
        if rels_part not in names:
            return {}
        try:
            root = ET.fromstring(archive.read(rels_part))
        except ET.ParseError as exc:
            self._diagnose(
                "invalid_part_relationships",
                f"Word 部件关系无法解析: {exc}",
                WordSource(rels_part, "body", "relationships"),
            )
            return {}
        return {
            relationship_id: (target, relationship.get("TargetMode"), relationship.get("Type", ""))
            for relationship in root.findall(f"./{_REL}Relationship")
            if (relationship_id := relationship.get("Id")) and (target := relationship.get("Target"))
        }

    def _resolved_visual_evidence(self) -> list[WordVisualEvidence]:
        resolved: list[WordVisualEvidence] = []
        for evidence in self.visual_evidence:
            relationship_id = evidence.relationship_id
            if evidence.kind != "embedded_image" or not relationship_id:
                resolved.append(evidence)
                continue
            relationship = self.relationships_by_part.get(evidence.source.part, {}).get(relationship_id)
            if relationship is None:
                self._diagnose(
                    "missing_visual_relationship",
                    f"视觉证据引用缺少关系 '{relationship_id}'。",
                    evidence.source,
                    severity="warning",
                )
                resolved.append(evidence)
                continue
            target, target_mode, relationship_type = relationship
            if target_mode == "External" or relationship_type not in _IMAGE_RELATIONSHIP_TYPES:
                self._diagnose(
                    "unsupported_visual_relationship",
                    f"视觉证据关系 '{relationship_id}' 不是可读取的内嵌图片。",
                    evidence.source,
                    severity="warning",
                )
                resolved.append(evidence)
                continue
            target_part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(evidence.source.part), target))
            if target_part not in self.package_names:
                self._diagnose(
                    "missing_visual_part",
                    f"视觉证据关系 '{relationship_id}' 指向缺失部件 '{target_part}'。",
                    evidence.source,
                    severity="warning",
                )
                resolved.append(evidence)
                continue
            resolved.append(replace(evidence, target_part=target_part))
        return resolved

    def _parse_parts(
        self,
        archive: ZipFile,
        parts: tuple[str, ...],
        region: Literal["header", "footer"],
    ) -> tuple[WordBlock, ...]:
        blocks: list[WordBlock] = []
        for part in parts:
            blocks.extend(self._parse_part(archive.read(part), part, region))
        return tuple(blocks)

    def _parse_xml(self, xml: bytes, part: str, region: Literal["body", "header", "footer"]) -> ET.Element | None:
        try:
            pending_namespaces: dict[str, str] = {}
            namespace_stack: list[dict[str, str]] = [{}]
            iterator = ET.iterparse(BytesIO(xml), events=("start-ns", "start", "end"))
            root: ET.Element | None = None
            for event, value in iterator:
                if event == "start-ns":
                    prefix, namespace = cast(tuple[str, str], value)
                    pending_namespaces[prefix or ""] = namespace
                elif event == "start":
                    element = cast(ET.Element, value)
                    namespaces = dict(namespace_stack[-1])
                    namespaces.update(pending_namespaces)
                    pending_namespaces.clear()
                    self.element_namespaces[id(element)] = namespaces
                    namespace_stack.append(namespaces)
                else:
                    root = cast(ET.Element, value)
                    namespace_stack.pop()
            return root
        except ET.ParseError as exc:
            self._diagnose(
                "invalid_xml_part",
                f"Word XML 部件无法解析: {exc}",
                WordSource(part, region, "part"),
            )
            return None

    def _parse_document_root(self, root: ET.Element, part: str) -> tuple[WordBlock, ...]:
        container = root.find(f"./{_tag('body')}")
        if container is None:
            self._diagnose(
                "missing_document_body",
                "word/document.xml 缺少 w:body。",
                WordSource(part, "body", "body"),
            )
            return ()
        return tuple(self._parse_container(container, part, "body", "body"))

    def _parse_part(self, xml: bytes, part: str, region: Literal["header", "footer"]) -> tuple[WordBlock, ...]:
        root = self._parse_xml(xml, part, region)
        if root is None:
            return ()
        return tuple(self._parse_container(root, part, region, region))

    def _document_relationships(self, archive: ZipFile, names: set[str]) -> dict[str, tuple[str, str | None, str]]:
        relationship_part = "word/_rels/document.xml.rels"
        if relationship_part not in names:
            return {}
        try:
            root = ET.fromstring(archive.read(relationship_part))
        except ET.ParseError as exc:
            self._diagnose(
                "invalid_document_relationships",
                f"文档关系部件无法解析: {exc}",
                WordSource(relationship_part, "body", "relationships"),
            )
            return {}

        relationships: dict[str, tuple[str, str | None, str]] = {}
        for relationship in root.findall(f"./{_REL}Relationship"):
            relationship_id = relationship.get("Id")
            target = relationship.get("Target")
            if relationship_id and target:
                relationships[relationship_id] = (
                    target,
                    relationship.get("TargetMode"),
                    relationship.get("Type", ""),
                )
        return relationships

    def _referenced_parts(
        self,
        document_root: ET.Element,
        relationships: dict[str, tuple[str, str | None, str]],
        names: set[str],
        kind: Literal["header", "footer"],
    ) -> tuple[str, ...]:
        parts: list[str] = []
        seen: set[str] = set()
        for reference in self._iter_selected_elements(document_root):
            if reference.tag != _tag(f"{kind}Reference"):
                continue
            relationship_id = reference.get(f"{_R}id")
            source = WordSource("word/document.xml", "body", f"{kind}Reference")
            if not relationship_id or relationship_id not in relationships:
                self._diagnose(
                    "missing_part_relationship",
                    f"{kind} 引用缺少可解析的关系 '{relationship_id or ''}'。",
                    source,
                )
                continue
            target, target_mode, relationship_type = relationships[relationship_id]
            if relationship_type not in _RELATIONSHIP_TYPES[kind]:
                self._diagnose(
                    "relationship_type_mismatch",
                    f"{kind} 引用 '{relationship_id}' 的关系类型为 '{relationship_type}'。",
                    source,
                )
                continue
            if target_mode == "External":
                self._diagnose(
                    "unsupported_external_part",
                    f"不支持外部 {kind} 关系 '{relationship_id}'。",
                    source,
                )
                continue
            part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("word", target))
            if part not in names:
                self._diagnose(
                    "missing_related_part",
                    f"{kind} 关系 '{relationship_id}' 指向缺失部件 '{part}'。",
                    source,
                )
                continue
            if part not in seen:
                seen.add(part)
                parts.append(part)
        return tuple(parts)

    def _iter_selected_elements(self, element: ET.Element) -> Iterable[ET.Element]:
        yield element
        if element.tag == f"{_MC}AlternateContent":
            branch = self._select_mc_branch(element)
            if branch is not None:
                yield from self._iter_selected_elements(branch)
            return
        for child in list(element):
            yield from self._iter_selected_elements(child)

    def _iter_paragraph_evidence_elements(self, element: ET.Element) -> Iterable[ET.Element]:
        if element.tag == _tag("txbxContent"):
            return
        yield element
        if element.tag == f"{_MC}AlternateContent":
            branch = self._select_mc_branch(element)
            if branch is not None:
                yield from self._iter_paragraph_evidence_elements(branch)
            return
        for child in list(element):
            yield from self._iter_paragraph_evidence_elements(child)

    def _parse_container(
        self,
        container: ET.Element,
        part: str,
        region: _REGION,
        base_path: str,
        *,
        suppress_checkbox_characters: bool = False,
    ) -> list[WordBlock]:
        blocks: list[WordBlock] = []
        paragraph_index = 0
        table_index = 0
        for child_position, child in enumerate(list(container), start=1):
            if child.tag == _tag("p"):
                paragraph_index += 1
                path = f"{base_path}/p[{paragraph_index}]"
                paragraph_text = self._paragraph_text(child, exclude_textboxes=True)
                blocks.append(
                    WordParagraphBlock(
                        source=WordSource(part, region, path),
                        text=paragraph_text,
                    )
                )
                if not suppress_checkbox_characters:
                    checkbox_text = self._text_without_checkbox_controls(child)
                    self._record_checkbox_characters(checkbox_text, WordSource(part, region, path))
                self._record_inline_content_controls(child, WordSource(part, region, path))
                self._record_drawing_evidence(child, WordSource(part, region, path))
                for textbox_index, textbox in enumerate(self._iter_textboxes(child), start=1):
                    textbox_path = f"{path}/textbox[{textbox_index}]"
                    blocks.extend(
                        self._parse_container(
                            textbox,
                            part,
                            "textbox",
                            textbox_path,
                            suppress_checkbox_characters=suppress_checkbox_characters,
                        )
                    )
            elif child.tag == _tag("tbl"):
                table_index += 1
                path = f"{base_path}/tbl[{table_index}]"
                blocks.append(
                    self._parse_table(
                        child,
                        WordSource(part, region, path),
                        suppress_checkbox_characters=suppress_checkbox_characters,
                    )
                )
            elif child.tag == _tag("sdt"):
                content = child.find(f"./{_tag('sdtContent')}")
                wrapper_path = f"{base_path}/sdt[{child_position}]"
                is_checkbox_control = self._record_content_control(child, WordSource(part, region, wrapper_path))
                if content is None:
                    self._diagnose(
                        "missing_content_control_body",
                        "块级内容控件缺少 w:sdtContent。",
                        WordSource(part, region, wrapper_path),
                    )
                else:
                    blocks.extend(
                        self._parse_container(
                            content,
                            part,
                            region,
                            f"{wrapper_path}/sdtContent",
                            suppress_checkbox_characters=suppress_checkbox_characters or is_checkbox_control,
                        )
                    )
            elif child.tag in {
                _tag("customXml"),
                _tag("ins"),
                _tag("del"),
                _tag("moveFrom"),
                _tag("moveTo"),
            }:
                wrapper_name = child.tag.removeprefix(_W)
                wrapper_path = f"{base_path}/{wrapper_name}[{child_position}]"
                blocks.extend(
                    self._parse_container(
                        child,
                        part,
                        region,
                        wrapper_path,
                        suppress_checkbox_characters=suppress_checkbox_characters,
                    )
                )
            elif child.tag == f"{_MC}AlternateContent":
                branch = self._select_mc_branch(child)
                alternate_path = f"{base_path}/AlternateContent[{child_position}]"
                if branch is None:
                    self._diagnose(
                        "empty_alternate_content",
                        "mc:AlternateContent 没有可用分支。",
                        WordSource(part, region, alternate_path),
                    )
                else:
                    blocks.extend(
                        self._parse_container(
                            branch,
                            part,
                            region,
                            alternate_path,
                            suppress_checkbox_characters=suppress_checkbox_characters,
                        )
                    )
            elif child.tag not in {
                _tag("bookmarkEnd"),
                _tag("bookmarkStart"),
                _tag("permEnd"),
                _tag("permStart"),
                _tag("proofErr"),
                _tag("sectPr"),
                _tag("tcPr"),
                _tag("trPr"),
            }:
                unsupported_path = f"{base_path}/unsupported[{child_position}]"
                self._diagnose(
                    "unsupported_block_element",
                    f"无法确认块级元素 '{child.tag}' 的语义，已递归保留可识别内容。",
                    WordSource(part, region, unsupported_path),
                )
                blocks.extend(
                    self._parse_container(
                        child,
                        part,
                        region,
                        unsupported_path,
                        suppress_checkbox_characters=suppress_checkbox_characters,
                    )
                )
        return blocks

    def _text_without_checkbox_controls(self, element: ET.Element) -> str:
        chunks: list[str] = []

        def visit(candidate: ET.Element) -> None:
            if candidate.tag == _tag("txbxContent"):
                return
            if candidate.tag == _tag("sdt"):
                properties = candidate.find(f"./{_tag('sdtPr')}")
                if properties is not None and properties.find(f".//{_W14}checkbox") is not None:
                    return
            if candidate.tag == f"{_MC}AlternateContent":
                branch = self._select_mc_branch(candidate)
                if branch is not None:
                    visit(branch)
                return
            if candidate.tag == _tag("t") and candidate.text:
                chunks.append(candidate.text)
                return
            for child in list(candidate):
                visit(child)

        visit(element)
        return "".join(chunks)

    def _record_checkbox_characters(self, text: str, source: WordSource) -> None:
        for character_index, character in enumerate(text, start=1):
            if character not in _CHECKED_CHARACTERS and character not in _UNCHECKED_CHARACTERS:
                continue
            self.visual_evidence.append(
                WordVisualEvidence(
                    source=WordSource(source.part, source.region, f"{source.path}/char[{character_index}]"),
                    kind="checkbox_character",
                    checked=character in _CHECKED_CHARACTERS,
                    label=text,
                )
            )

    def _record_content_control(self, control: ET.Element, source: WordSource) -> bool:
        properties = control.find(f"./{_tag('sdtPr')}")
        content = control.find(f"./{_tag('sdtContent')}")
        alias = properties.find(f"./{_tag('alias')}") if properties is not None else None
        tag = properties.find(f"./{_tag('tag')}") if properties is not None else None
        content_text = self._paragraph_text(content, exclude_textboxes=False) if content is not None else ""
        label = (alias.get(_tag("val"), "") if alias is not None else "") or (tag.get(_tag("val"), "") if tag is not None else "") or content_text
        checkbox = properties.find(f".//{_W14}checkbox") if properties is not None else None
        checked: bool | None = None
        if checkbox is not None:
            checked_node = checkbox.find(f"./{_W14}checked")
            raw_checked = checked_node.get(f"{_W14}val", "0") if checked_node is not None else "0"
            checked = str(raw_checked).strip().lower() in {"1", "true", "on", "checked"}
        self.visual_evidence.append(
            WordVisualEvidence(
                source=source,
                kind="content_control",
                checked=checked,
                label=label,
            )
        )
        return checkbox is not None

    def _record_inline_content_controls(self, paragraph: ET.Element, source: WordSource) -> None:
        control_index = 0
        for candidate in self._iter_paragraph_evidence_elements(paragraph):
            if candidate.tag != _tag("sdt"):
                continue
            control_index += 1
            self._record_content_control(
                candidate,
                WordSource(
                    source.part,
                    source.region,
                    f"{source.path}/sdt[{control_index}]",
                ),
            )

    def _record_drawing_evidence(self, element: ET.Element, source: WordSource) -> None:
        image_index = 0
        for candidate in self._iter_paragraph_evidence_elements(element):
            relationship_id: str | None = None
            if candidate.tag == f"{_A}blip":
                relationship_id = candidate.get(f"{_R}embed") or candidate.get(f"{_R}link")
            elif candidate.tag == f"{_V}imagedata":
                relationship_id = candidate.get(f"{_R}id")
            if not relationship_id:
                continue
            image_index += 1
            self.visual_evidence.append(
                WordVisualEvidence(
                    source=WordSource(source.part, source.region, f"{source.path}/image[{image_index}]"),
                    kind="embedded_image",
                    relationship_id=relationship_id,
                )
            )

        drawing_index = 0
        for candidate in self._iter_paragraph_evidence_elements(element):
            if candidate.tag not in {_tag("drawing"), _tag("pict"), _tag("object")}:
                continue
            drawing_index += 1
            self.visual_evidence.append(
                WordVisualEvidence(
                    source=WordSource(source.part, source.region, f"{source.path}/drawing[{drawing_index}]"),
                    kind="drawing",
                )
            )

    def _parse_table(
        self,
        table: ET.Element,
        source: WordSource,
        *,
        suppress_checkbox_characters: bool = False,
    ) -> WordTableBlock:
        diagnostic_start = len(self.diagnostics)
        grid = table.find(f"./{_tag('tblGrid')}")
        grid_column_count = len(grid.findall(f"./{_tag('gridCol')}")) if grid is not None else 0
        if grid_column_count == 0:
            self._diagnose("missing_table_grid", "表格缺少可用的 w:tblGrid。", source)

        rows = self._wrapped_table_children(table, "tr", source)
        if not rows:
            self._diagnose("empty_table", "表格没有可用的 w:tr 行。", source)
        cells: list[WordTableCell] = []
        active_vertical_merges: dict[int, int] = {}

        for row_index, row in enumerate(rows):
            row_path = f"{source.path}/tr[{row_index + 1}]"
            row_properties = row.find(f"./{_tag('trPr')}")
            grid_before = self._integer_property(row_properties, "gridBefore", source, row_path)
            grid_after = self._integer_property(row_properties, "gridAfter", source, row_path)
            if grid_before < 0:
                self._diagnose(
                    "negative_grid_offset",
                    "w:gridBefore 不能为负数，已按 0 处理。",
                    WordSource(source.part, source.region, row_path),
                )
                grid_before = 0
            if grid_after < 0:
                self._diagnose(
                    "negative_grid_offset",
                    "w:gridAfter 不能为负数，已按 0 处理。",
                    WordSource(source.part, source.region, row_path),
                )
                grid_after = 0
            col = grid_before
            continued_origins: set[int] = set()

            row_source = WordSource(source.part, source.region, row_path)
            row_cells = self._wrapped_table_children(row, "tc", row_source)
            for physical_cell_index, cell_element in enumerate(row_cells, start=1):
                cell_path = f"{row_path}/tc[{physical_cell_index}]"
                cell_source = WordSource(source.part, "cell", cell_path)
                properties = cell_element.find(f"./{_tag('tcPr')}")
                colspan = self._integer_property(properties, "gridSpan", cell_source, cell_path, default=1)
                if colspan < 1:
                    self._diagnose("invalid_grid_span", "w:gridSpan 必须是正整数，已按 1 处理。", cell_source)
                    colspan = 1
                if properties is not None and properties.find(f"./{_tag('hMerge')}") is not None:
                    self._diagnose(
                        "unsupported_legacy_horizontal_merge",
                        "不支持旧式 w:hMerge；请使用 w:gridSpan。",
                        cell_source,
                    )

                cell_blocks = tuple(
                    self._parse_container(
                        cell_element,
                        source.part,
                        "cell",
                        cell_path,
                        suppress_checkbox_characters=suppress_checkbox_characters,
                    )
                )
                paragraphs = tuple(block.text for block in cell_blocks if isinstance(block, WordParagraphBlock))
                text = "\n".join(paragraphs)
                vertical_merge = properties.find(f"./{_tag('vMerge')}") if properties is not None else None
                merge_value = vertical_merge.get(_tag("val"), "continue") if vertical_merge is not None else None

                if merge_value == "continue":
                    origin_indices = {active_vertical_merges.get(column) for column in range(col, col + colspan)}
                    if len(origin_indices) == 1 and None not in origin_indices:
                        origin_index = next(iter(origin_indices))
                        assert origin_index is not None
                        origin = cells[origin_index]
                        if origin.colspan != colspan:
                            self._diagnose(
                                "vertical_merge_span_mismatch",
                                "跨行合并续格的列跨度与起点不一致。",
                                cell_source,
                            )
                            cells.append(self._cell(cell_source, row_index, col, colspan, text, paragraphs, cell_blocks, False))
                        else:
                            cells[origin_index] = replace(origin, rowspan=origin.rowspan + 1)
                            continued_origins.add(origin_index)
                            if self._has_visible_content(cell_blocks):
                                self._diagnose(
                                    "vertical_merge_continuation_content",
                                    "跨行合并续格包含内容，已保留为非起点单元格。",
                                    cell_source,
                                )
                                cells.append(self._cell(cell_source, row_index, col, colspan, text, paragraphs, cell_blocks, False))
                    else:
                        self._diagnose("orphan_vertical_merge", "跨行合并续格没有唯一的起点。", cell_source)
                        cells.append(self._cell(cell_source, row_index, col, colspan, text, paragraphs, cell_blocks, False))
                else:
                    for column in range(col, col + colspan):
                        active_vertical_merges.pop(column, None)
                    origin_index = len(cells)
                    cells.append(self._cell(cell_source, row_index, col, colspan, text, paragraphs, cell_blocks, True))
                    if merge_value == "restart":
                        for column in range(col, col + colspan):
                            active_vertical_merges[column] = origin_index
                        continued_origins.add(origin_index)
                    elif merge_value not in {None, "restart"}:
                        self._diagnose(
                            "invalid_vertical_merge_value",
                            f"无法识别 w:vMerge 值 '{merge_value}'。",
                            cell_source,
                        )

                col += colspan

            row_width = col + grid_after
            if grid_column_count and row_width != grid_column_count:
                self._diagnose(
                    "table_row_width_mismatch",
                    f"逻辑行宽 {row_width} 与 w:tblGrid 列数 {grid_column_count} 不一致。",
                    WordSource(source.part, source.region, row_path),
                )
            active_vertical_merges = {column: origin_index for column, origin_index in active_vertical_merges.items() if origin_index in continued_origins}

        table_diagnostics = tuple(self.diagnostics[diagnostic_start:])
        return WordTableBlock(
            source=source,
            grid_column_count=grid_column_count,
            row_count=len(rows),
            cells=tuple(cells),
            valid=not any(item.severity == "error" for item in table_diagnostics),
            diagnostics=table_diagnostics,
        )

    def _wrapped_table_children(
        self,
        container: ET.Element,
        expected_name: Literal["tr", "tc"],
        source: WordSource,
    ) -> list[ET.Element]:
        expected_tag = _tag(expected_name)
        children: list[ET.Element] = []
        for child_position, child in enumerate(list(container), start=1):
            if child.tag == expected_tag:
                children.append(child)
                continue
            if child.tag == _tag("sdt"):
                content = child.find(f"./{_tag('sdtContent')}")
                if content is None:
                    self._diagnose(
                        "missing_content_control_body",
                        f"表格中的 w:sdt 缺少 w:sdtContent，无法读取 w:{expected_name}。",
                        source,
                    )
                else:
                    children.extend(self._wrapped_table_children(content, expected_name, source))
                continue
            if child.tag in {
                _tag("customXml"),
                _tag("ins"),
                _tag("del"),
                _tag("moveFrom"),
                _tag("moveTo"),
            }:
                children.extend(self._wrapped_table_children(child, expected_name, source))
                continue
            if child.tag == f"{_MC}AlternateContent":
                branch = self._select_mc_branch(child)
                if branch is None:
                    self._diagnose(
                        "empty_alternate_content",
                        f"表格中的 mc:AlternateContent 没有可用的 w:{expected_name} 分支。",
                        source,
                    )
                else:
                    children.extend(self._wrapped_table_children(branch, expected_name, source))
                continue
            if child.find(f".//{expected_tag}") is not None:
                self._diagnose(
                    "unsupported_table_wrapper",
                    f"无法确认表格包装元素 '{child.tag}' 的语义，已递归保留 w:{expected_name}。",
                    WordSource(source.part, source.region, f"{source.path}/wrapper[{child_position}]"),
                )
                children.extend(self._wrapped_table_children(child, expected_name, source))
        return children

    @staticmethod
    def _cell(
        source: WordSource,
        row: int,
        col: int,
        colspan: int,
        text: str,
        paragraphs: tuple[str, ...],
        blocks: tuple[WordBlock, ...],
        origin: bool,
    ) -> WordTableCell:
        return WordTableCell(
            source=source,
            row=row,
            col=col,
            rowspan=1,
            colspan=colspan,
            text=text,
            paragraphs=paragraphs,
            blocks=blocks,
            origin=origin,
        )

    def _integer_property(
        self,
        properties: ET.Element | None,
        name: str,
        source: WordSource,
        path: str,
        default: int = 0,
    ) -> int:
        element = properties.find(f"./{_tag(name)}") if properties is not None else None
        if element is None:
            return default
        raw_value = element.get(_tag("val"), str(default))
        try:
            return int(raw_value)
        except ValueError:
            self._diagnose(
                "invalid_integer_property",
                f"{path} 的 w:{name} 值 '{raw_value}' 不是整数。",
                source,
            )
            return default

    def _paragraph_text(self, paragraph: ET.Element, *, exclude_textboxes: bool) -> str:
        chunks: list[str] = []

        def visit(element: ET.Element) -> None:
            if exclude_textboxes and element.tag == _tag("txbxContent"):
                return
            if element.tag == f"{_MC}AlternateContent":
                branch = self._select_mc_branch(element)
                if branch is not None:
                    visit(branch)
                return
            if element.tag == _tag("t"):
                chunks.append(element.text or "")
                return
            if element.tag == _tag("tab"):
                chunks.append("\t")
                return
            if element.tag in {_tag("br"), _tag("cr")}:
                chunks.append("\n")
                return
            for child in list(element):
                visit(child)

        visit(paragraph)
        return "".join(chunks)

    def _select_mc_branch(self, alternate_content: ET.Element) -> ET.Element | None:
        choices = alternate_content.findall(f"./{_MC}Choice")
        for choice in choices:
            required_prefixes = choice.get("Requires", "").split()
            namespaces = self.element_namespaces.get(id(choice), {})
            if required_prefixes and all(namespaces.get(prefix) in _SUPPORTED_MC_NAMESPACES for prefix in required_prefixes):
                return choice
        fallback = alternate_content.find(f"./{_MC}Fallback")
        return fallback

    def _iter_textboxes(self, element: ET.Element) -> Iterable[ET.Element]:
        if element.tag == _tag("txbxContent"):
            yield element
            return
        if element.tag == f"{_MC}AlternateContent":
            branch = self._select_mc_branch(element)
            if branch is not None:
                yield from self._iter_textboxes(branch)
            return
        for child in list(element):
            yield from self._iter_textboxes(child)

    @staticmethod
    def _has_visible_content(blocks: Iterable[WordBlock]) -> bool:
        for block in blocks:
            if isinstance(block, WordParagraphBlock) and block.text:
                return True
            if isinstance(block, WordTableBlock):
                return True
        return False

    def _diagnose(self, code: str, message: str, source: WordSource, severity: Literal["error", "warning"] = "error") -> None:
        self.diagnostics.append(WordDiagnostic(code=code, message=message, source=source, severity=severity))


def extract_docx_structure(file_content: bytes | bytearray | memoryview | str | Path) -> WordDocument:
    """Return ordered DOCX blocks and logical tables with explicit diagnostics."""
    if isinstance(file_content, (str, Path)):
        source_path = Path(file_content)
        try:
            data = source_path.read_bytes()
        except OSError as exc:
            source = WordSource(str(source_path), "body", "source")
            diagnostic = WordDiagnostic(
                code="unreadable_docx_source",
                message=f"无法读取 DOCX 来源: {exc}",
                source=source,
            )
            return WordDocument(valid=False, blocks=(), diagnostics=(diagnostic,))
    else:
        data = bytes(file_content)
    return _DocxStructureParser().parse(data)


def resolve_docx_image_evidence(
    file_content: bytes | bytearray | memoryview | str | Path,
    document: WordDocument,
) -> tuple[WordImageEvidence, ...]:
    """Resolve embedded-image evidence without exposing OOXML package details."""
    if isinstance(file_content, (str, Path)):
        data = Path(file_content).read_bytes()
    else:
        data = bytes(file_content)

    images: list[WordImageEvidence] = []
    with ZipFile(BytesIO(data), "r") as archive:
        names = set(archive.namelist())
        for evidence in document.visual_evidence:
            target_part = evidence.target_part
            if evidence.kind != "embedded_image" or not target_part or target_part not in names:
                continue
            mime_type = mimetypes.guess_type(target_part)[0] or "application/octet-stream"
            encoded = base64.b64encode(archive.read(target_part)).decode("ascii")
            images.append(
                WordImageEvidence(
                    evidence=evidence,
                    data_url=f"data:{mime_type};base64,{encoded}",
                )
            )
    return tuple(images)
