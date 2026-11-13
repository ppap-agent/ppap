"""Uninterpreted native spreadsheet objects cannot prove empty business fields."""

import argparse
import hashlib
import json
from copy import deepcopy
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from audit.document_tools import ParserLimitError, ReadLimits, TableReader, _print_view, effective, snapshot
from audit.evidence import EvidenceBudget, cell_locator, resolve
from audit.models import ExcelRange
from openpyxl import Workbook

SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"


def marker(name, row, column, row_offset=0, column_offset=0):
    return f"<{name}><col>{column - 1}</col><colOff>{column_offset}</colOff><row>{row - 1}</row><rowOff>{row_offset}</rowOff></{name}>"


def anchor(first=(2, 2), last=(3, 3), *, offsets=(0, 0), first_offsets=(0, 0), kind="twoCellAnchor"):
    start = marker("from", *first, *first_offsets)
    end = marker("to", *last, *offsets) if kind == "twoCellAnchor" else '<ext cx="10000" cy="10000"/>'
    return f'<{kind}>{start}{end}<sp><nvSpPr><cNvPr id="1" name="Shape"/><cNvSpPr/></nvSpPr><spPr/></sp><clientData/></{kind}>'


def workbook(tmp_path, anchors=None, *, cells=None, merge=None):
    path = tmp_path / "source.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Process"
    sheet["A1"], sheet["F8"] = "Field", "End"
    for address, value in (cells or {}).items():
        sheet[address] = value
    if merge:
        sheet.merge_cells(merge)
    book.create_sheet("Other")["F8"] = "End"
    book.save(path)
    book.close()
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    sheet_xml = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    ET.SubElement(sheet_xml, f"{{{SS}}}drawing", {f"{{{REL}}}id": "visual"})
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet_xml)
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = f'<Relationships xmlns="{PKG}"><Relationship Id="visual" Type="{REL}/drawing" Target="../drawings/drawing1.xml"/></Relationships>'.encode()
    parts["xl/drawings/drawing1.xml"] = f'<wsDr xmlns="{XDR}">{"".join(anchors or [anchor()])}</wsDr>'.encode()
    rewrite(path, parts)
    return path


def image_workbook(tmp_path, *, cells=None, two_cell=False):
    from base64 import b64decode
    from io import BytesIO

    from openpyxl.drawing.image import Image
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor

    path = tmp_path / "image.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Process"
    sheet["F8"] = "End"
    for address, value in (cells or {}).items():
        sheet[address] = value
    image = Image(BytesIO(b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==")))
    if two_cell:
        image.anchor = TwoCellAnchor(_from=AnchorMarker(col=1, row=1), to=AnchorMarker(col=4, row=3))
        sheet.add_image(image)
    else:
        sheet.add_image(image, "B2")
    book.create_sheet("Other")["F8"] = "End"
    book.save(path)
    book.close()
    return path


def linked_image_workbook(tmp_path, *, cached, target="https://example.invalid/image.png", count=1, cells=None):
    path = image_workbook(tmp_path, cells=cells, two_cell=True)
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    drawing = ET.fromstring(parts["xl/drawings/drawing1.xml"])
    source = drawing[0]
    drawing.remove(source)
    rels = ET.fromstring(parts["xl/drawings/_rels/drawing1.xml.rels"])
    if not cached:
        rels.remove(next(item for item in rels if item.get("Id") == "rId1"))
        del parts["xl/media/image1.png"]
    for index in range(count):
        node = deepcopy(source)
        blip = next(node.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"))
        if not cached:
            del blip.attrib[f"{{{REL}}}embed"]
        identity = f"linked{index}"
        blip.set(f"{{{REL}}}link", identity)
        drawing.append(node)
        ET.SubElement(rels, f"{{{PKG}}}Relationship", {"Id": identity, "Type": f"{REL}/image", "Target": target, "TargetMode": "External"})
    parts["xl/drawings/drawing1.xml"] = ET.tostring(drawing)
    parts["xl/drawings/_rels/drawing1.xml.rels"] = ET.tostring(rels)
    rewrite(path, parts)
    return path


def rewrite(path, parts):
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)


def change_part(path, part, value):
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    if value is None:
        parts.pop(part)
    else:
        parts[part] = value
    rewrite(path, parts)


def test_only_empty_native_scalars_with_visual_overlap_are_unknown(tmp_path):
    observed = snapshot(workbook(tmp_path, [anchor(last=(3, 5))], cells={"B2": "native", "C2": 0, "D2": False, "B3": "=1+1"}))
    table = observed["tables"][0]
    assert effective(table, 2, 2)["value"] == "native"
    assert effective(table, 2, 3)["value"] == 0
    assert effective(table, 2, 4)["value"] is False
    assert all(effective(table, 2, column)["state"] == "known" for column in (2, 3, 4))
    assert effective(table, 3, 2)["state"] == "unknown_formula_cache"
    # The zero-offset end is a boundary: row 3 / column 5 do not overlap.
    assert effective(table, 2, 5)["state"] == "known"
    assert effective(table, 3, 3)["state"] == "known"
    assert effective(table, 6, 5) == {"value": None, "state": "known", "cell": "E6", "anchor": "E6", "merge": None}
    assert observed["tables"][0]["visual_evidence"][0]["content_observed"] is False
    assert effective(observed["tables"][1], 2, 2)["state"] == "known"


def test_visual_blank_is_lazy_source_addressed_and_merge_aliases_inherit_unknown(tmp_path):
    observed = snapshot(workbook(tmp_path, [anchor(first=(3, 3), last=(5, 5))], merge="B2:D3"))
    table = observed["tables"][0]
    assert "C3" not in table["cells"]
    assert len(table["cells"]) < 5
    assert effective(table, 2, 2)["state"] == "unknown_visual_evidence"
    assert effective(table, 3, 4)["state"] == "unknown_visual_evidence"
    assert effective(table, 4, 4)["state"] == "unknown_visual_evidence"
    assert effective(table, 4, 5)["state"] == "known"
    location = cell_locator(".xlsx", "Process", 3, 4)
    assert not resolve(observed, location).known
    assert effective(table, 3, 4)["source"]["visual_evidence"] == [0]


def test_unlocated_visual_only_changes_empty_cells_on_its_own_sheet(tmp_path):
    observed = snapshot(workbook(tmp_path, [anchor(kind="absoluteAnchor")], cells={"B2": False}))
    table = observed["tables"][0]
    assert table["visual_evidence"][0]["unresolved_reason"] == "unsupported_anchor_geometry"
    assert effective(table, 7, 5)["state"] == "unknown_visual_evidence"
    assert effective(table, 2, 2)["state"] == "known"
    assert effective(observed["tables"][1], 7, 5)["state"] == "known"


@pytest.mark.parametrize("defect", ["bad_xml", "missing_drawing", "missing_rel", "external", "escape", "duplicate_rel", "invalid_marker", "wrong_root"])
def test_broken_visual_sources_fail_closed(tmp_path, defect):
    path = workbook(tmp_path)
    rels = "xl/worksheets/_rels/sheet1.xml.rels"
    if defect == "bad_xml":
        change_part(path, "xl/drawings/drawing1.xml", b"<broken")
    elif defect == "wrong_root":
        change_part(path, "xl/drawings/drawing1.xml", b"<wrongRoot/>")
    elif defect == "missing_drawing":
        change_part(path, "xl/drawings/drawing1.xml", None)
    elif defect == "missing_rel":
        change_part(path, rels, None)
    elif defect == "invalid_marker":
        change_part(path, "xl/drawings/drawing1.xml", f'<wsDr xmlns="{XDR}">{anchor(first=(0, 2))}</wsDr>'.encode())
    else:
        relation = f'<Relationship Id="visual" Type="{REL}/drawing" Target="../drawings/drawing1.xml"/>'
        if defect == "external":
            relation = relation.replace('Target="../drawings/drawing1.xml"', 'Target="https://example.invalid/drawing.xml" TargetMode="External"')
        if defect == "escape":
            relation = relation.replace("../drawings/drawing1.xml", "../../../outside.xml")
        change_part(path, rels, f'<Relationships xmlns="{PKG}">{relation * (2 if defect == "duplicate_rel" else 1)}</Relationships>'.encode())
    with pytest.raises((ValueError, KeyError, ET.ParseError)):
        snapshot(path)


def test_visual_metadata_and_queries_use_existing_budgets(tmp_path):
    path = workbook(tmp_path, [anchor() for _ in range(4)])
    with pytest.raises(ParserLimitError):
        snapshot(path, limits=ReadLimits(blocks=3))
    observed = snapshot(path)
    with pytest.raises(ValueError, match="budget"):
        resolve(observed, cell_locator(".xlsx", "Process", 2, 2), EvidenceBudget(remaining=0))
    budget = EvidenceBudget(remaining=100)
    reader = TableReader(observed["tables"][0], budget)
    reader.cell(7, 5)
    assert budget.remaining > 0
    # One metadata index plus the selected cells fits this budget. Re-scanning
    # all visual objects for every ordinary blank would exceed it.
    ordinary = resolve(observed, ExcelRange(kind="xlsx_range", sheet="Process", start="A4", end="F8"), EvidenceBudget(remaining=100))
    assert ordinary.known
    with pytest.raises(ValueError, match="budget"):
        resolve(observed, ExcelRange(kind="xlsx_range", sheet="Process", start="A4", end="F8"), EvidenceBudget(remaining=20))


@pytest.mark.parametrize("cached", [None, False, True])
def test_compact_view_pages_recover_all_visual_source_metadata(tmp_path, capsys, cached):
    path = workbook(tmp_path, [anchor() for _ in range(24)]) if cached is None else linked_image_workbook(tmp_path, cached=cached, count=24)
    observed = snapshot(path)
    records, fragments, offset = [], {}, 0
    while True:
        args = argparse.Namespace(offset=offset, max_bytes=4096, table="Process", columns="B", rows="2", pages="1", document="target")
        _print_view(observed, args)
        raw = capsys.readouterr().out
        assert len(raw.encode()) <= 4096
        lines = [json.loads(line) for line in raw.splitlines()]
        for line in lines[1:]:
            if line["kind"] == "json_fragment":
                fragments.setdefault(line["record_id"], {})[line["part"]] = line["text"]
                if len(fragments[line["record_id"]]) == line["parts"]:
                    records.append(json.loads("".join(fragments[line["record_id"]][part] for part in range(line["parts"]))))
            else:
                records.append(line)
        offset = lines[0]["next_offset"]
        if offset is None:
            break
    table = next(record for record in records if record["kind"] == "table")
    assert table["visual_evidence"] == observed["tables"][0]["visual_evidence"]
    assert next(record for record in records if record["kind"] == "cell")["state"] == "unknown_visual_evidence"


def test_zero_height_line_covers_the_cells_on_both_sides_of_its_boundary(tmp_path):
    observed = snapshot(workbook(tmp_path, [anchor(first=(2, 2), last=(2, 4))]))
    visual = observed["tables"][0]["visual_evidence"][0]
    assert visual["range"] is None
    assert visual["bounds"] == [1, 2, 2, 3]
    assert effective(observed["tables"][0], 2, 3)["state"] == "unknown_visual_evidence"
    assert effective(observed["tables"][0], 3, 3)["state"] == "known"


def test_one_cell_anchor_covers_the_cells_its_extent_can_reach(tmp_path):
    observed = snapshot(workbook(tmp_path, [anchor(kind="oneCellAnchor")]))
    table = observed["tables"][0]
    visual = table["visual_evidence"][0]
    assert visual["extent_emu"] == {"row": 10000, "column": 10000}
    assert visual["bounds"] == [2, 2, 2, 2]
    assert effective(table, 2, 2)["state"] == "unknown_visual_evidence"
    assert effective(table, 7, 5)["state"] == "known"


def test_alternate_content_uses_the_anchor_inside_each_alternative(tmp_path):
    inner = anchor().replace("<twoCellAnchor>", f'<twoCellAnchor xmlns="{XDR}">')
    wrapped = f'<AlternateContent xmlns="http://schemas.openxmlformats.org/markup-compatibility/2006"><Choice Requires="a14">{inner}</Choice></AlternateContent>'
    observed = snapshot(workbook(tmp_path, [wrapped]))
    visual = observed["tables"][0]["visual_evidence"][0]
    assert (visual["kind"], visual["range"]) == ("twoCellAnchor", [2, 2, 2, 2])
    assert effective(observed["tables"][0], 7, 5)["state"] == "known"


@pytest.mark.parametrize("endpoint", ["from", "to"])
@pytest.mark.parametrize("axis", ["row", "column"])
@pytest.mark.parametrize("offset", [-27273042329600, -1, 10**12, 27273042316900])
def test_offsets_outside_the_measured_cell_are_native_hints_not_cell_coverage(tmp_path, endpoint, axis, offset):
    offsets = (offset, 0) if axis == "row" else (0, offset)
    placement = anchor(**{"first_offsets" if endpoint == "from" else "offsets": offsets})
    path = workbook(tmp_path, [placement], cells={"B2": "native", "C2": 0, "D2": False, "B3": "=1+1"})
    original = path.read_bytes()
    observed = snapshot(path)
    table = observed["tables"][0]
    visual = table["visual_evidence"][0]
    assert visual[endpoint][f"{axis}_offset_emu"] == offset
    assert visual["range"] is None
    if offset < 0:
        # A placement before its own cell has no lower bound on its reach.
        assert visual["unresolved_reason"] == "unsupported_anchor_geometry"
        assert effective(table, 7, 5)["state"] == "unknown_visual_evidence"
    else:
        # An offset past every measured cell reaches the sheet edge on its own axis only.
        assert visual["unresolved_reason"] == "imprecise_anchor_geometry"
        assert visual["bounds"] == ([2, 2, 1048576, 2] if axis == "row" else [2, 2, 2, 16384])
        assert effective(table, *((7, 2) if axis == "row" else (2, 7)))["state"] == "unknown_visual_evidence"
        assert effective(table, 7, 5)["state"] == "known"
    assert all(effective(table, 2, column)["state"] == "known" for column in (2, 3, 4))
    assert effective(table, 3, 2)["state"] == "unknown_formula_cache"
    assert effective(observed["tables"][1], 7, 5)["state"] == "known"
    assert path.read_bytes() == original
    assert observed["source"]["sha256"] == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("kind", ["oneCellAnchor", "absoluteAnchor", "legacyDrawing", "control", "oleObject", "picture"])
def test_unsupported_visual_kind_cannot_claim_precise_cell_coverage(tmp_path, kind):
    # Source-looking markers on an unsupported object do not establish geometry.
    path = workbook(tmp_path, [anchor().replace("twoCellAnchor", kind)])
    observed = snapshot(path)
    assert observed["tables"][0]["visual_evidence"][0]["range"] is None


def sized_workbook(tmp_path, anchors, *, column_width=None, row_height=None, hidden_row=False, cells=None):
    path = workbook(tmp_path, anchors, cells=cells)
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    sheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    data = sheet.find(f"{{{SS}}}sheetData")
    if column_width is not None:
        columns = ET.Element(f"{{{SS}}}cols")
        ET.SubElement(columns, f"{{{SS}}}col", {"min": "2", "max": "2", "width": str(column_width), "customWidth": "1"})
        sheet.insert(list(sheet).index(data), columns)
    if row_height is not None or hidden_row:
        attributes = {"r": "2", **({"ht": str(row_height), "customHeight": "1"} if row_height is not None else {}), **({"hidden": "1"} if hidden_row else {})}
        row = next((node for node in data if node.get("r") == "2"), None)
        if row is None:
            row = ET.Element(f"{{{SS}}}row")
            data.insert(1, row)
        row.attrib.update(attributes)
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet)
    rewrite(path, parts)
    return path


def test_image_inside_one_measured_cell_leaves_other_blank_cells_known(tmp_path):
    placement = anchor((2, 2), (2, 2), first_offsets=(1000, 1000), offsets=(100000, 200000))
    observed = snapshot(sized_workbook(tmp_path, [placement], column_width=20, row_height=20))
    table = observed["tables"][0]
    visual = table["visual_evidence"][0]
    assert visual["range"] == [2, 2, 2, 2]
    assert visual["measured_emu"] == {"from_row": 254000, "from_column": 952500, "to_row": 254000, "to_column": 952500}
    assert "unresolved_reason" not in visual
    assert effective(table, 2, 2)["state"] == "unknown_visual_evidence"
    assert effective(table, 3, 2)["state"] == "known"
    assert effective(table, 2, 3)["state"] == "known"


def test_end_offset_includes_its_cell_and_a_boundary_end_does_not(tmp_path):
    offset_end = snapshot(sized_workbook(tmp_path, [anchor((2, 2), (3, 3), first_offsets=(1, 1), offsets=(1, 1))]))
    assert offset_end["tables"][0]["visual_evidence"][0]["range"] == [2, 2, 3, 3]
    boundary_end = snapshot(sized_workbook(tmp_path, [anchor((2, 2), (3, 3), first_offsets=(1, 1))]))
    assert boundary_end["tables"][0]["visual_evidence"][0]["range"] == [2, 2, 2, 2]


@pytest.mark.parametrize(
    "placement,size,bounds",
    [
        (anchor((2, 2), (2, 2), first_offsets=(1, 1), offsets=(300000, 1000)), {"row_height": 20}, [2, 2, 3, 2]),
        (anchor((2, 2), (2, 2), first_offsets=(1, 1), offsets=(1000, 1000000)), {"column_width": 20}, [2, 2, 2, 3]),
        (anchor((2, 2), (2, 2), first_offsets=(1, 1), offsets=(1000, 1000)), {"hidden_row": True}, [2, 2, 1048576, 2]),
        (anchor((2, 2), (2, 2), first_offsets=(500, 1), offsets=(100, 1000)), {"row_height": 20}, [2, 2, 2, 2]),
    ],
    ids=["taller-than-row", "wider-than-column", "hidden-row", "reversed-in-cell"],
)
def test_offsets_that_do_not_fit_cover_every_cell_they_can_reach(tmp_path, placement, size, bounds):
    observed = snapshot(sized_workbook(tmp_path, [placement], **size))
    table = observed["tables"][0]
    visual = table["visual_evidence"][0]
    assert visual["range"] is None
    assert "measured_emu" not in visual
    assert visual["unresolved_reason"] == "imprecise_anchor_geometry"
    # Measured sizes are lower bounds: the reach may overcount cells, never undercount.
    assert visual["bounds"] == bounds
    assert effective(table, min(bounds[2], 7), bounds[3])["state"] == "unknown_visual_evidence"
    assert effective(table, 7, 5)["state"] == "known"


@pytest.mark.parametrize("offset", [-27273042329601, 27273042316901])
def test_anchor_offsets_outside_native_coordinate_bounds_fail_closed(tmp_path, offset):
    path = workbook(tmp_path, [anchor(offsets=(offset, 0))])
    with pytest.raises(ValueError, match="anchor marker"):
        snapshot(path)


@pytest.mark.parametrize("hyperlink", [None, "hlinkClick", "hlinkHover", "missing_relation", "external_content", "blip_hyperlink", "navigation_content"])
def test_native_image_and_navigation_relationships_preserve_visual_sources_without_fetch(tmp_path, monkeypatch, hyperlink):
    from openpyxl import load_workbook

    path = image_workbook(tmp_path)
    if hyperlink:
        with ZipFile(path) as archive:
            parts = {item.filename: archive.read(item) for item in archive.infolist()}
        drawing = ET.fromstring(parts["xl/drawings/drawing1.xml"])
        properties = next(drawing.iter(f"{{{XDR}}}cNvPr"))
        link_kind = "hlinkHover" if hyperlink == "hlinkHover" else "hlinkClick"
        ET.SubElement(properties, f"{{http://schemas.openxmlformats.org/drawingml/2006/main}}{link_kind}", {f"{{{REL}}}id": "link"})
        parts["xl/drawings/drawing1.xml"] = ET.tostring(drawing)
        rels = ET.fromstring(parts["xl/drawings/_rels/drawing1.xml.rels"])
        if hyperlink != "missing_relation":
            ET.SubElement(rels, f"{{{PKG}}}Relationship", {"Id": "link", "Type": f"{REL}/hyperlink", "Target": "https://example.invalid/destination", "TargetMode": "External"})
        if hyperlink == "external_content":
            image_relation = next(item for item in rels if item.get("Id") == "rId1")
            image_relation.set("Target", "https://example.invalid/image.png")
            image_relation.set("TargetMode", "External")
        if hyperlink == "blip_hyperlink":
            image_relation = next(item for item in rels if item.get("Id") == "rId1")
            image_relation.attrib.update({"Type": f"{REL}/hyperlink", "Target": "https://example.invalid/image.png", "TargetMode": "External"})
        if hyperlink == "navigation_content":
            next(item for item in rels if item.get("Id") == "link").set("Type", f"{REL}/image")
        parts["xl/drawings/_rels/drawing1.xml.rels"] = ET.tostring(rels)
        rewrite(path, parts)
    network_calls = []

    def unexpected_network(*args, **kwargs):
        network_calls.append(args)
        raise AssertionError("Document inspection must not fetch external links")

    monkeypatch.setattr("socket.socket.connect", unexpected_network)
    if hyperlink in {"missing_relation", "external_content", "blip_hyperlink", "navigation_content"}:
        with pytest.raises(ValueError):
            snapshot(path)
        assert not network_calls
        return
    native = load_workbook(path)
    assert len(native.active._images) == 1
    native.close()
    observed = snapshot(path)
    assert not network_calls
    visual = observed["tables"][0]["visual_evidence"][0]
    assert visual["references"] == [{"relationship_id": "rId1", "part": "xl/media/image1.png"}]
    assert visual["content_observed"] is False
    assert effective(observed["tables"][0], 2, 2)["state"] == "unknown_visual_evidence"


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("scheme", ["https", "file"])
def test_linked_images_remain_explicitly_unavailable_without_external_reads(tmp_path, monkeypatch, cached, scheme):
    import builtins

    external_path = tmp_path / "external-private.png"
    external_path.write_bytes(b"must not be read")
    target = "https://example.invalid/image.png" if scheme == "https" else external_path.as_uri()
    path = linked_image_workbook(tmp_path, cached=cached, target=target, cells={"B2": "native", "C2": 0, "D2": False, "B3": "=1+1"})
    original = path.read_bytes()
    calls = []
    original_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if str(file) in {str(external_path), target}:
            calls.append("file")
            raise AssertionError("Document inspection must not read an external file")
        return original_open(file, *args, **kwargs)

    def unexpected_network(*args, **kwargs):
        calls.append("network")
        raise AssertionError("Document inspection must not fetch an external image")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr("socket.socket.connect", unexpected_network)
    monkeypatch.setattr("socket.socket.connect_ex", unexpected_network)
    observed = snapshot(path)
    table = observed["tables"][0]
    visual = table["visual_evidence"][0]
    expected = [{"relationship_id": "rId1", "part": "xl/media/image1.png"}] if cached else []
    assert visual["references"] == [*expected, {"relationship_id": "linked0", "state": "external_unavailable"}]
    assert visual["source"] == {"part": "xl/drawings/drawing1.xml", "anchor_index": 1}
    assert visual["content_observed"] is False
    assert target not in json.dumps(observed)
    assert effective(table, 3, 3)["state"] == "unknown_visual_evidence"
    assert effective(table, 6, 5)["state"] == "known"
    assert all(effective(table, 2, column)["state"] == "known" for column in (2, 3, 4))
    assert effective(table, 3, 2)["state"] == "unknown_formula_cache"
    assert effective(observed["tables"][1], 3, 3)["state"] == "known"
    assert path.read_bytes() == original
    assert observed["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert not calls


@pytest.mark.parametrize("defect", ["missing_relation", "hyperlink_type", "internal_hyperlink_type", "missing_cache", "invalid_cache_path"])
def test_linked_image_does_not_hide_invalid_relationships_or_internal_cache(tmp_path, defect):
    path = linked_image_workbook(tmp_path, cached=True)
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    rels = ET.fromstring(parts["xl/drawings/_rels/drawing1.xml.rels"])
    if defect == "missing_relation":
        rels.remove(next(item for item in rels if item.get("Id") == "linked0"))
    elif defect == "hyperlink_type":
        next(item for item in rels if item.get("Id") == "linked0").set("Type", f"{REL}/hyperlink")
    elif defect == "internal_hyperlink_type":
        next(item for item in rels if item.get("Id") == "rId1").set("Type", f"{REL}/hyperlink")
    elif defect == "missing_cache":
        del parts["xl/media/image1.png"]
    else:
        next(item for item in rels if item.get("Id") == "rId1").set("Target", "../../../private.png")
    parts["xl/drawings/_rels/drawing1.xml.rels"] = ET.tostring(rels)
    rewrite(path, parts)
    with pytest.raises(ValueError):
        snapshot(path)


def test_comment_popup_does_not_make_the_worksheet_grid_unresolved(tmp_path):
    from openpyxl.comments import Comment

    path = tmp_path / "comment.xlsx"
    book = Workbook()
    sheet = book.active
    sheet["A1"].comment = Comment("Annotation", "Author")
    sheet["D4"] = "End"
    book.save(path)
    book.close()
    observed = snapshot(path)
    assert not observed["tables"][0].get("visual_evidence")
    assert effective(observed["tables"][0], 2, 2)["state"] == "known"


def test_local_checker_reads_only_bounded_examples_with_one_visual_index(tmp_path, monkeypatch):
    from audit import checker_tools

    observed = snapshot(workbook(tmp_path, [anchor(kind="absoluteAnchor")]))
    table = observed["tables"][0]
    table["rows"] = 120
    reads = []
    indices = []

    class DisplayReader(TableReader):
        def __init__(self, table, budget):
            indices.append(table)
            super().__init__(table, budget)

        def cell(self, row, column):
            reads.append((row, column))
            return super().cell(row, column)

    monkeypatch.setattr(checker_tools, "TableReader", DisplayReader)
    request = checker_tools.CheckRequest.model_validate(
        {
            "binding": {
                "id": "body",
                "document_id": "target",
                "source_sha256": observed["source"]["sha256"],
                "table": "Process",
                "header_rows": [1],
                "first_row": 2,
                "last_row": 120,
                "columns": {"field": "B"},
                "mapping_basis": "Column B is the declared field.",
                "boundary_basis": "Rows 2 to 120 are the declared body.",
            },
            "checks": [{"id": "local", "kind": "field_nonempty", "field": "field"}],
        }
    )
    check = checker_tools.check_fields(observed, request).checks[0]
    assert (check.local_condition_status, check.total, check.checked, check.unknown) == ("UNABLE", 119, 0, 119)
    assert len(check.unknown_rows) == 119
    assert len(check.unknown_examples) == len(reads) == 64
    assert len(indices) == 1


@pytest.mark.parametrize("defect", ["missing_relation", "external", "missing_part", "wrong_root"])
def test_vml_image_relationships_fail_closed(tmp_path, defect):
    path = workbook(tmp_path)
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    sheet_xml = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    ET.SubElement(sheet_xml, f"{{{SS}}}legacyDrawing", {f"{{{REL}}}id": "legacy"})
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet_xml)
    rels = ET.fromstring(parts["xl/worksheets/_rels/sheet1.xml.rels"])
    ET.SubElement(rels, f"{{{PKG}}}Relationship", {"Id": "legacy", "Type": f"{REL}/vmlDrawing", "Target": "../drawings/legacy.vml"})
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = ET.tostring(rels)
    parts["xl/drawings/legacy.vml"] = f'<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:r="{REL}"><v:shape id="shape"><v:imagedata r:id="image"/></v:shape></xml>'.encode()
    if defect == "wrong_root":
        parts["xl/drawings/legacy.vml"] = b"<wrongRoot/>"
    elif defect != "missing_relation":
        target = 'Target="https://example.invalid/image.png" TargetMode="External"' if defect == "external" else 'Target="../media/missing.png"'
        parts["xl/drawings/_rels/legacy.vml.rels"] = f'<Relationships xmlns="{PKG}"><Relationship Id="image" Type="{REL}/image" {target}/></Relationships>'.encode()
    rewrite(path, parts)
    with pytest.raises(ValueError):
        snapshot(path)


@pytest.mark.parametrize("kind", ["rect", "oval", "line", "group", "image"])
def test_vml_native_primitives_are_unresolved_visual_sources(tmp_path, kind):
    path = tmp_path / "legacy.xlsx"
    book = Workbook()
    sheet = book.active
    sheet["D4"] = "End"
    book.save(path)
    book.close()
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    sheet_xml = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    ET.SubElement(sheet_xml, f"{{{SS}}}legacyDrawing", {f"{{{REL}}}id": "legacy"})
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet_xml)
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = f'<Relationships xmlns="{PKG}"><Relationship Id="legacy" Type="{REL}/vmlDrawing" Target="../drawings/legacy.vml"/></Relationships>'.encode()
    parts["xl/drawings/legacy.vml"] = f'<xml xmlns:v="urn:schemas-microsoft-com:vml"><v:{kind} id="native"/></xml>'.encode()
    rewrite(path, parts)
    observed = snapshot(path)
    assert effective(observed["tables"][0], 2, 2)["state"] == "unknown_visual_evidence"


def test_vml_client_anchor_bounds_the_object_to_its_cells(tmp_path):
    path = tmp_path / "legacy.xlsx"
    book = Workbook()
    book.active["F8"] = "End"
    book.save(path)
    book.close()
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    sheet_xml = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    ET.SubElement(sheet_xml, f"{{{SS}}}legacyDrawing", {f"{{{REL}}}id": "legacy"})
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet_xml)
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = f'<Relationships xmlns="{PKG}"><Relationship Id="legacy" Type="{REL}/vmlDrawing" Target="../drawings/legacy.vml"/></Relationships>'.encode()
    client = '<x:ClientData ObjectType="Checkbox"><x:Anchor>1, 2, 1, 3, 1, 20, 1, 15</x:Anchor></x:ClientData>'
    parts["xl/drawings/legacy.vml"] = f'<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape id="box">{client}</v:shape></xml>'.encode()
    rewrite(path, parts)
    observed = snapshot(path)
    table = observed["tables"][0]
    assert table["visual_evidence"][0]["bounds"] == [2, 2, 2, 2]
    assert effective(table, 2, 2)["state"] == "unknown_visual_evidence"
    assert effective(table, 7, 5)["state"] == "known"


def test_vml_xml_nodes_use_the_existing_visual_block_budget(tmp_path):
    path = workbook(tmp_path)
    with ZipFile(path) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    sheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    sheet.find(f"{{{SS}}}drawing").tag = f"{{{SS}}}legacyDrawing"
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet)
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = f'<Relationships xmlns="{PKG}"><Relationship Id="visual" Type="{REL}/vmlDrawing" Target="../drawings/legacy.vml"/></Relationships>'.encode()
    formulas = '<v:f eqn="val #0"/>' * 10
    parts["xl/drawings/legacy.vml"] = f'<xml xmlns:v="urn:schemas-microsoft-com:vml"><v:shape id="shape"><v:formulas>{formulas}</v:formulas></v:shape></xml>'.encode()
    rewrite(path, parts)
    with pytest.raises(ParserLimitError, match="visual XML nodes"):
        snapshot(path, limits=ReadLimits(blocks=3))
    observed = snapshot(path, limits=ReadLimits(blocks=13))
    assert effective(observed["tables"][0], 2, 2)["state"] == "unknown_visual_evidence"
