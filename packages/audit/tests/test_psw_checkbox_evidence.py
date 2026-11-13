"""Explicit checkbox glyphs need one source-located disposition row."""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from audit.engine.checkers.psw_checkbox_evidence import read_disposition_facts
from audit.engine.checkers.psw_visual_evidence import evaluate_psw_observation

_SHA = "a" * 64


def row():
    # A minimal native text layer with the geometry of an authorized page-4 disposition row,
    # with no customer document content.
    return [
        {"index": 118, "text": "认可情况：", "pdf_bbox": [52.977749, 700.375183, 85.963898, 708.873657], "source": "native_pdf"},
        {"index": 119, "text": "□批准", "pdf_bbox": [109.953819, 699.375366, 137.442276, 709.873474], "source": "native_pdf"},
        {"index": 120, "text": "□条件批准", "pdf_bbox": [257.891693, 699.375366, 298.874481, 707.87384], "source": "native_pdf"},
        {"index": 122, "text": "□拒绝", "pdf_bbox": [398.832489, 698.875427, 425.821167, 709.373596], "source": "native_pdf"},
    ]


def read(texts, *, witness=None):
    return read_disposition_facts(texts, witness or Mock(), page=4, source_sha256=_SHA)


def test_explicit_empty_boxes_are_false_and_rule_eight_is_ng():
    witness = Mock()
    facts = read(row(), witness=witness)
    assert facts == {"disposition_visible": True, "approved_checked": False, "conditional_checked": False, "rejected_checked": False}
    assert evaluate_psw_observation(8, facts).status == "NG"
    assert witness.call_count == 4
    for call in witness.call_args_list:
        locator = call.args[0]
        assert locator["page"] == 4
        assert locator["source_sha256"] == _SHA
        assert locator["coordinate_system"] == "pdf_points_unrotated"
        assert call.kwargs["visual"] is False


@pytest.mark.parametrize("mark", ["☑", "☒"])
def test_attached_checked_glyph_is_known_without_inferring_other_fields(mark):
    texts = row()
    texts[1]["text"] = mark + "批准"
    facts = read(texts)
    assert facts["approved_checked"] is True
    # Approval alone does not prove the independent submission-result boxes.
    assert evaluate_psw_observation(8, facts).status == "UNABLE"


def test_option_text_alone_and_an_unattached_mark_are_unknown():
    texts = row()
    texts[1]["text"] = "批准"
    texts.append({**texts[1], "index": 200, "text": "□", "pdf_bbox": [100, 699, 108, 708]})
    assert read(texts)["approved_checked"] is None


@pytest.mark.parametrize("change", ["another_section", "another_anchor", "duplicate_option", "conflicting_option", "duplicate_id"])
def test_ambiguous_or_unscoped_marks_do_not_establish_a_state(change):
    texts = row()
    if change == "another_section":
        texts[1]["pdf_bbox"] = [110, 620, 138, 630]
    elif change == "another_anchor":
        texts.append({**texts[0], "index": 201, "pdf_bbox": [50, 500, 85, 510]})
    elif change == "duplicate_id":
        texts.append(deepcopy(texts[1]))
    else:
        texts.append({**texts[1], "index": 201, "text": "□批准" if change == "duplicate_option" else "☑批准"})
    assert read(texts)["approved_checked"] is None


@pytest.mark.parametrize("bbox", [None, [0, 0, 0, 1], [0, -1, 1, 2], [0, 0, float("inf"), 1], [0, 0, 1], [0, 0, True, 1]])
def test_invalid_geometry_is_not_a_checkbox_fact(bbox):
    texts = row()
    texts[1]["pdf_bbox"] = bbox
    assert read(texts)["approved_checked"] is None


def test_no_anchor_or_missing_option_does_not_prove_absence():
    assert all(value is None for value in read(row()[1:]).values())
    assert read(row()[:2])["conditional_checked"] is None
    assert read([])["disposition_visible"] is None
