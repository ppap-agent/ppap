"""Native DrawingML placement for R4 fields that can be filled by text or a shape."""

from __future__ import annotations

from posixpath import dirname, join, normpath
from xml.etree import ElementTree
from zipfile import ZipFile


def drawing_regions(path, title):
    """Inclusive 1-based ranges of drawing anchors in this sheet; None for unknown coverage."""
    relation_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with ZipFile(path) as archive:

        def targets(part):
            relations = join(dirname(part), "_rels", part.rsplit("/", 1)[-1] + ".rels")
            if relations not in archive.namelist():
                return {}
            return {
                node.attrib["Id"]: normpath(join(dirname(part), value)) if not value.startswith("/") else value.lstrip("/")
                for node in ElementTree.fromstring(archive.read(relations))
                if node.attrib.get("TargetMode") != "External" and (value := node.attrib.get("Target"))
            }

        workbook = "xl/workbook.xml"
        sheets = ElementTree.fromstring(archive.read(workbook)).findall(".//{*}sheet")
        ref = next((sheet.get(relation_id) for sheet in sheets if sheet.get("name") == title), None)
        sheet_part = targets(workbook).get(ref)
        if sheet_part is None:
            return None
        relationships = targets(sheet_part)
        data = ElementTree.fromstring(archive.read(sheet_part))
        anchors = []
        if (legacy := data.find("{*}legacyDrawing")) is not None:
            part = relationships.get(legacy.get(relation_id))
            if part is None:
                return None
            vml = ElementTree.fromstring(archive.read(part))
            for shape in vml.iterfind(".//{*}shape"):
                client = shape.find("{*}ClientData")
                if client is None:
                    return None
                marker = client.findtext("{*}Anchor")
                if not marker:
                    return None
                cells = [int(value.strip()) for value in marker.split(",")]
                if len(cells) != 8:
                    return None
                anchors.append((cells[2] + 1, cells[0] + 1, cells[6] + 1, cells[4] + 1))
        if (drawing := data.find("{*}drawing")) is None:
            return anchors
        part = relationships.get(drawing.get(relation_id))
        if part is None:
            return None
        for anchor in ElementTree.fromstring(archive.read(part)).iter():
            kind = anchor.tag.rsplit("}", 1)[-1]
            if kind not in {"twoCellAnchor", "oneCellAnchor", "absoluteAnchor"}:
                continue  # AlternateContent is a container, not an independent unlocated drawing.
            start, end = anchor.find("{*}from"), anchor.find("{*}to")
            if start is None or end is None:
                return None  # An absolute or size-only shape may cover more than its anchor cell.
            anchors.append(tuple(int(marker.findtext("{*}" + key)) + 1 for marker in (start, end) for key in ("row", "col")))
        return anchors
