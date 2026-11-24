"""UAES revision 4 recognition of explicitly equivalent integrated test names."""

import re
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl.utils.cell import get_column_letter

from audit.checker_lib.uaes.flowchart import placed_drawings
from audit.engine.rule_engine import Decision, RuleContext, Table

from .process_flow import UaesProcessFlowRevision3, compact

_AIRTIGHT_NAMES = frozenset({"气密", "气密性检测", "气检"})


class UaesProcessFlowRevision4(UaesProcessFlowRevision3):
    @staticmethod
    def station_name_key(name: str) -> str:
        # Whole names only: do not erase the tested object from a qualified name.
        if compact(name) in _AIRTIGHT_NAMES:
            return "气密"
        return UaesProcessFlowRevision3.station_name_key(name)

    def station_row_name(self, name: str, mode: str) -> str:
        # A dedicated row already anchors the number. A complete registered operation
        # name in the adjacent mode field can identify the station, not an arbitrary failure description.
        return name or (mode if compact(mode) in _AIRTIGHT_NAMES else "")

    def node_annotations(self, ctx: RuleContext):
        path = str(self.audit_file.file_url)
        if Path(path).suffix.lower() not in {".xlsx", ".xlsm"}:
            return
        for sheet in Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE):
            try:
                drawings = placed_drawings(path, sheet.title)
            except (OSError, ValueError, KeyError, BadZipFile, ParseError):
                continue
            for drawing in drawings:
                if drawing.place is None or not drawing.text.strip():
                    continue
                row, col, _, _ = drawing.place
                note = drawing.text.strip()
                disposition = bool(re.fullmatch(r"不(?:合格|良)(?:品|件)(?:处理|处置|隔离|返工|返修|报废)", compact(note)))
                yield f"“{sheet.title}”{get_column_letter(col)}{row}处流程图标注", note, (sheet, row, col), disposition
