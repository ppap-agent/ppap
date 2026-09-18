from __future__ import annotations

import base64
import logging
import os
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

logger = logging.getLogger(__name__)


class DefaultExcelOps:
    """Default workbook operations for RuleContext/Fluent/Resolver."""

    def __init__(self) -> None:
        self._merged_range_lookup_cache: dict[int, dict[tuple[int, int], Any]] = {}
        self._effective_row_columns_cache: dict[int, dict[int, tuple[int, ...]] | None] = {}
        self._checkbox_controls_cache: dict[str, list[tuple[int, int, bool]]] = {}
        self._sheet_images_base64_cache: dict[int, list[str]] = {}
        self._sheet_stamp_signal_cache: dict[int, tuple[bool, bool]] = {}

    def _build_merged_range_lookup(self, ws: Worksheet) -> dict[tuple[int, int], Any]:
        cache_key = id(ws)
        cached = self._merged_range_lookup_cache.get(cache_key)
        if cached is not None:
            return cached

        lookup: dict[tuple[int, int], Any] = {}
        for merged_range in ws.merged_cells.ranges:
            for rr in range(merged_range.min_row, merged_range.max_row + 1):
                for cc in range(merged_range.min_col, merged_range.max_col + 1):
                    lookup[(rr, cc)] = merged_range

        self._merged_range_lookup_cache[cache_key] = lookup
        return lookup

    def get_detailed_merge_info(self, ws: Worksheet, row: int, col: int) -> Any:
        lookup = self._build_merged_range_lookup(ws)
        return lookup.get((row, col))

    def _build_effective_row_columns(self, ws: Worksheet) -> dict[int, tuple[int, ...]] | None:
        cache_key = id(ws)
        if cache_key in self._effective_row_columns_cache:
            return self._effective_row_columns_cache[cache_key]

        cells = getattr(ws, "_cells", None)
        if not isinstance(cells, dict):
            logger.warning(
                "Worksheet [%s] does not expose sparse cells; falling back to declared row width",
                ws.title,
            )
            self._effective_row_columns_cache[cache_key] = None
            return None

        columns_by_row: dict[int, set[int]] = {}
        for (row_index, col_index), cell in cells.items():
            if cell.value is not None:
                columns_by_row.setdefault(int(row_index), set()).add(int(col_index))

        for merged_range in ws.merged_cells.ranges:
            anchor_value = ws.cell(merged_range.min_row, merged_range.min_col).value
            if anchor_value is None:
                continue
            for row_index in range(merged_range.min_row, merged_range.max_row + 1):
                columns_by_row.setdefault(row_index, set()).add(merged_range.min_col)

        result = {row_index: tuple(sorted(column_indexes)) for row_index, column_indexes in columns_by_row.items()}
        self._effective_row_columns_cache[cache_key] = result
        return result

    def iter_effective_row_values(
        self,
        ws: Worksheet,
        row: int,
    ) -> Iterator[str]:
        columns_by_row = self._build_effective_row_columns(ws)
        column_indexes = range(1, ws.max_column + 1) if columns_by_row is None else columns_by_row.get(row, ())
        for col_index in column_indexes:
            yield self.get_effective_cell_value(ws, row, col_index)

    def get_effective_cell_value(
        self,
        ws: Worksheet,
        row: int,
        col: int,
        strip: bool = True,
        lower: bool = False,
        remove_whitespace: bool = False,
    ) -> str:
        merged = self.get_detailed_merge_info(ws, row, col)
        if merged:
            value = ws.cell(merged.min_row, merged.min_col).value
        else:
            value = ws.cell(row=row, column=col).value

        if value is None:
            return ""

        text = str(value)
        if remove_whitespace:
            text = "".join(text.split())
        if strip:
            text = text.strip()
        if lower:
            text = text.lower()
        return text

    def is_filled(self, x: Any) -> bool:
        if x is None:
            return False
        s = str(x).replace("\u00a0", "").strip().lower()
        return s not in ("", "nan", "none")

    def _xml_local_name(self, tag: str) -> str:
        return tag.split("}", 1)[-1]

    def _normalize_zip_target(self, base_dir: str, target: str) -> str:
        normalized_target = str(target or "").strip()
        if not normalized_target:
            return ""
        if normalized_target.startswith("/"):
            normalized_target = normalized_target.lstrip("/")
        if normalized_target.startswith("xl/"):
            return posixpath.normpath(normalized_target)

        merged = posixpath.normpath(posixpath.join(base_dir, normalized_target))
        while merged.startswith("../"):
            merged = merged[3:]
        if not merged.startswith("xl/"):
            merged = posixpath.normpath(posixpath.join("xl", merged))
        return merged.lstrip("/")

    def _resolve_sheet_xml_path(self, zf: zipfile.ZipFile, sheet_title: str) -> str:
        ns = {
            "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "pkg_rel": "http://schemas.openxmlformats.org/package/2006/relationships",
        }
        workbook_xml = ET.fromstring(zf.read("xl/workbook.xml"))
        workbook_rels_xml = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib.get("Id", ""): rel.attrib.get("Target", "") for rel in workbook_rels_xml.findall("pkg_rel:Relationship", ns)}

        rel_id_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        for sheet in workbook_xml.findall("main:sheets/main:sheet", ns):
            if sheet.attrib.get("name", "") != sheet_title:
                continue
            rel_id = sheet.attrib.get(rel_id_attr, "")
            target = rel_map.get(rel_id, "")
            resolved = self._normalize_zip_target("xl", target)
            if resolved in zf.namelist():
                return resolved
            break
        return ""

    def _sheet_rels_path(self, sheet_xml_path: str) -> str:
        parent = posixpath.dirname(sheet_xml_path)
        filename = posixpath.basename(sheet_xml_path)
        return posixpath.join(parent, "_rels", f"{filename}.rels")

    def get_checkbox_controls(
        self,
        ws: Worksheet,
        source_path: str | None = None,
    ) -> list[tuple[int, int, bool]]:
        xlsx_path = str(source_path or "")
        if not xlsx_path.lower().endswith((".xlsx", ".xlsm")) or not os.path.exists(xlsx_path):
            return []
        cache_key = f"{xlsx_path}::{ws.title}"
        cached = self._checkbox_controls_cache.get(cache_key)
        if cached is not None:
            return cached

        controls: list[tuple[int, int, bool]] = []
        ns_rel = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}

        try:
            with zipfile.ZipFile(xlsx_path, "r") as zf:
                sheet_xml_path = self._resolve_sheet_xml_path(zf, ws.title)
                if not sheet_xml_path:
                    self._checkbox_controls_cache[cache_key] = []
                    return []

                sheet_xml = ET.fromstring(zf.read(sheet_xml_path))
                rels_path = self._sheet_rels_path(sheet_xml_path)
                rel_map: dict[str, str] = {}
                if rels_path in zf.namelist():
                    rels_xml = ET.fromstring(zf.read(rels_path))
                    rel_map = {rel.attrib.get("Id", ""): rel.attrib.get("Target", "") for rel in rels_xml.findall("rel:Relationship", ns_rel)}

                seen: set[tuple[int, int, bool]] = set()
                for control in sheet_xml.iter():
                    if self._xml_local_name(control.tag) != "control":
                        continue

                    rel_id = control.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
                    target = self._normalize_zip_target(posixpath.dirname(sheet_xml_path), rel_map.get(rel_id, ""))

                    row_idx: int | None = None
                    col_idx: int | None = None
                    for node in control.iter():
                        node_name = self._xml_local_name(node.tag)
                        node_text = (node.text or "").strip()
                        if node_name == "row" and row_idx is None and node_text.isdigit():
                            row_idx = int(node_text) + 1
                        elif node_name == "col" and col_idx is None and node_text.isdigit():
                            col_idx = int(node_text) + 1
                        if row_idx is not None and col_idx is not None:
                            break
                    if row_idx is None or col_idx is None:
                        continue

                    checked = False
                    if target and target in zf.namelist():
                        ctrl_prop_root = ET.fromstring(zf.read(target))
                        checked_attr = str(ctrl_prop_root.attrib.get("checked", "")).strip().lower()
                        val_attr = str(ctrl_prop_root.attrib.get("val", "")).strip().lower()
                        checked = checked_attr in {"checked", "1", "true"} or val_attr in {"1", "true", "checked"}

                    record = (row_idx, col_idx, checked)
                    if record not in seen:
                        seen.add(record)
                        controls.append(record)

        except Exception as exc:
            logger.warning("Failed to parse checkbox controls from %s[%s]: %s", xlsx_path, ws.title, exc)
            controls = []

        controls.sort(key=lambda item: (item[0], item[1], 0 if item[2] else 1))
        self._checkbox_controls_cache[cache_key] = controls
        return controls

    def get_sheet_images_base64(self, ws: Worksheet) -> list[str]:
        cache_key = id(ws)
        cached = self._sheet_images_base64_cache.get(cache_key)
        if cached is not None:
            return cached

        encoded_images: list[str] = []
        images = getattr(ws, "_images", []) or []
        for image in images:
            data = getattr(image, "_ppap_cached_data", None)
            if not data:
                data_getter = getattr(image, "_data", None)
                if callable(data_getter):
                    try:
                        data = data_getter()
                        if data:
                            setattr(image, "_ppap_cached_data", data)
                    except Exception as exc:
                        data = getattr(image, "_ppap_cached_data", None)
                        if not data:
                            logger.warning("Failed to read worksheet image bytes [%s]: %s", ws.title, exc)
                            data = None
            if not data:
                continue
            encoded = base64.b64encode(data).decode("utf-8")
            encoded_images.append(f"data:image/jpeg;base64,{encoded}")

        self._sheet_images_base64_cache[cache_key] = encoded_images
        return encoded_images

    def detect_sheet_stamp_signals(self, ws: Worksheet) -> tuple[bool, bool]:
        cache_key = id(ws)
        cached = self._sheet_stamp_signal_cache.get(cache_key)
        if cached is not None:
            return cached

        has_seal = False
        has_signature_or_stamp = False
        images = self.get_sheet_images_base64(ws)
        if images:
            try:
                from ppap_vision.detectors import detect_seal, detect_signature_or_stamp

                for image in images:
                    if not has_seal and detect_seal(image):
                        has_seal = True
                    if not has_signature_or_stamp and detect_signature_or_stamp(image):
                        has_signature_or_stamp = True
                    if has_seal and has_signature_or_stamp:
                        break
            except Exception as exc:
                logger.warning("Worksheet stamp detection failed [%s]: %s", ws.title, exc)
                has_seal = False
                has_signature_or_stamp = False

        result = (has_seal, has_signature_or_stamp)
        self._sheet_stamp_signal_cache[cache_key] = result
        return result
