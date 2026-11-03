"""Native XLSX formula descriptors remain readable without inventing a cache."""

from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook


def workbook(tmp_path, formula, cached):
    path = tmp_path / "formulas.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Process"
    sheet.append(["Calculated result"])
    sheet["A2"] = formula
    book.create_sheet("Reference")["A1"] = "Required source remains present"
    book.save(path)
    book.close()
    if cached is not None:
        # Excel stores cached results in <v>; openpyxl does not calculate them.
        # Build that real OOXML state, then exercise the normal XLSX reader.
        with ZipFile(path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        xml = ET.fromstring(entries["xl/worksheets/sheet1.xml"])
        cell = next(item for item in xml.iter(ns + "c") if item.get("r") == "A2")
        value = cell.find(ns + "v")
        if value is None:
            value = ET.SubElement(cell, ns + "v")
        value.text = str(cached)
        entries["xl/worksheets/sheet1.xml"] = ET.tostring(xml)
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
    return path
