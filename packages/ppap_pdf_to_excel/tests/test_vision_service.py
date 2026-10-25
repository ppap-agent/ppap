"""Multimodal page extraction keeps order, bounded concurrency and refusal safety."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import pytest
from ppap_pdf_to_excel.vision import page as vision_service


def _pdf(tmp_path, pages=2):
    path = tmp_path / "pages.pdf"
    with fitz.open() as document:
        for _ in range(pages):
            document.new_page()
        document.save(path)
    return str(path)


def test_page_requests_overlap_but_results_preserve_pdf_page_order(tmp_path):
    both_started = asyncio.Event()
    seen = []

    async def read_page(image, page_number, page_count, native_tables):
        assert image.startswith(bytes((137, 80, 78, 71)))
        assert page_count == 2
        seen.append(page_number)
        if len(seen) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=5)
        return {"tables": [{"rows": [[str(page_number)]], "merges": []}], "paragraphs": [], "seals": []}

    with patch.object(vision_service, "_read_page_async", side_effect=read_page):
        pages = vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path))
    assert sorted(seen) == [1, 2]
    assert [page["tables"][0]["rows"][0][0] for page in pages] == ["1", "2"]


@pytest.mark.parametrize(
    "payload",
    [
        {"tables": [], "paragraphs": [], "seals": ["ok"], "extra": 1},
        {"tables": [{"rows": [["ok", 12]], "merges": []}], "paragraphs": [], "seals": []},
        {"tables": "broken", "paragraphs": [], "seals": []},
    ],
)
def test_schema_deviations_fail_closed(payload):
    with pytest.raises(ValueError, match="Invalid Responses"):
        vision_service._validate_page(payload)


def test_reads_png_image_using_responses_schema(tmp_path):
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return {"tables": [], "paragraphs": ["readable text"], "seals": []}

    with patch.object(vision_service, "call_llm_async", side_effect=create):
        results = vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path, 1))
    assert results[0]["paragraphs"] == ["readable text"]
    assert calls[0]["json_mode"] is True
    assert calls[0]["retry_count"] == 1
    assert calls[0]["response_schema"]["name"] == "ppap_multimodal_pdf_page"


def test_multimodal_merge_rejects_fake_or_overlapping_evidence():
    valid = {
        "tables": [{"rows": [["外观"], [""], [""]], "merges": [{"start_row": 0, "start_col": 0, "end_row": 2, "end_col": 0}]}],
        "paragraphs": [],
        "seals": [],
    }
    assert vision_service._validate_page(valid) == valid
    overlapping = {
        **valid,
        "tables": [
            {
                "rows": [["外观"], [""], [""]],
                "merges": [
                    {"start_row": 0, "start_col": 0, "end_row": 2, "end_col": 0},
                    {"start_row": 1, "start_col": 0, "end_row": 2, "end_col": 0},
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="overlapping"):
        vision_service._validate_page(overlapping)
    invalid_cover = {
        **valid,
        "tables": [
            {
                "rows": [["外观"], ["另一个真实单元格"], [""]],
                "merges": [{"start_row": 0, "start_col": 0, "end_row": 2, "end_col": 0}],
            }
        ],
    }
    with pytest.raises(ValueError, match=r"nonempty evidence .*table=1, anchor=\(0,0\), cell=\(1,0\)"):
        vision_service._validate_page(invalid_cover)


def test_invalid_merge_diagnostic_identifies_pdf_page_without_exposing_cell_text():
    invalid = {
        "tables": [{"rows": [["private value"], ["other evidence"]], "merges": [{"start_row": 0, "start_col": 0, "end_row": 1, "end_col": 0}]}],
        "paragraphs": [],
        "seals": [],
    }

    async def respond(**kwargs):
        return invalid

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        with pytest.raises(vision_service.ResponsesEvidenceError, match=r"Responses page 2/3: Invalid Responses merge covers nonempty evidence") as error:
            asyncio.run(vision_service._read_page_async(b"image", 2, 3, []))
    assert error.value.code == "responses_invalid_schema"
    assert "private value" not in str(error.value)
    assert "other evidence" not in str(error.value)


def test_page_error_preserves_invalid_json_type_without_contents():
    failure = vision_service.ResponsesEvidenceError("responses_invalid_json", "Responses invalid JSON (output_chars=34)")

    async def fail(**kwargs):
        raise failure

    with patch.object(vision_service, "call_llm_async", side_effect=fail):
        with pytest.raises(vision_service.ResponsesEvidenceError, match="Responses page 4/4") as error:
            asyncio.run(vision_service._read_page_async(b"image", 4, 4, []))
    assert error.value.code == "responses_invalid_json"


def test_failed_page_cancels_sibling_provider_request_without_waiting_for_deadline(tmp_path):
    started = asyncio.Event()
    seen: list[int] = []
    cancelled: list[bool] = []

    async def respond(**kwargs):
        number = len(seen) + 1
        seen.append(number)
        if number == 1:
            await started.wait()
            return {"tables": [{"rows": [["private", "other"]], "merges": [{"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1}]}], "paragraphs": [], "seals": []}
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    before = time.monotonic()
    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        with pytest.raises(vision_service.ResponsesEvidenceError, match="merge covers nonempty evidence"):
            vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path, pages=2))
    assert time.monotonic() - before < 2
    assert seen == [1, 2]
    assert cancelled == [True]


def test_pdf_vision_batch_works_inside_existing_event_loop(tmp_path):
    async def respond(**kwargs):
        return {"tables": [], "paragraphs": ["visible"], "seals": []}

    async def inside_loop():
        return vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path, pages=1))

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        result = asyncio.run(inside_loop())
    assert result == [{"tables": [], "paragraphs": ["visible"], "seals": []}]


def _vector_grid_pdf(tmp_path, *, merged_header=False):
    path = tmp_path / ("true-merge.pdf" if merged_header else "separate-cells.pdf")
    with fitz.open() as document:
        page = document.new_page(width=360, height=220)
        for y in (30, 70, 110, 150):
            page.draw_line((20, y), (320, y))
        for x in (20, 220, 320):
            page.draw_line((x, 30), (x, 150))
        page.draw_line((120, 70 if merged_header else 30), (120, 150))
        source = [
            ("ALPHA", (26, 49)),
            ("GAMMA", (235, 49)),
            ("A2", (26, 89)),
            ("B2", (140, 89)),
            ("C2", (240, 89)),
            ("A3", (26, 129)),
            ("B3", (140, 129)),
            ("C3", (240, 129)),
        ]
        if not merged_header:
            source.append(("BETA", (140, 49)))
        for word, where in source:
            page.insert_text(where, word, fontsize=10)
        document.save(path)
    return str(path)


def _contradictory_table():
    return {
        "tables": [
            {
                "rows": [["ALPHA", "BETA", "GAMMA"], ["A2", "B2", "C2"], ["A3", "B3", "C3"]],
                "merges": [{"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1}],
            }
        ],
        "paragraphs": [],
        "seals": [],
    }


def test_native_vector_grid_corrects_model_merge_without_losing_text(tmp_path):
    path = _vector_grid_pdf(tmp_path)
    with fitz.open(path) as pdf:
        tables = vision_service._native_table_candidates(pdf[0])
    assert len(tables) == 1
    assert tables[0]["merges"] == []
    evidence = _contradictory_table()

    async def respond(**kwargs):
        return evidence

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        result = vision_service.VisionLayoutService().analyze_full_pdf(path)
    assert result[0]["tables"][0]["rows"] == evidence["tables"][0]["rows"]
    assert result[0]["tables"][0]["merges"] == []
    assert evidence["tables"][0]["merges"] != []


def test_real_pdf_merged_geometry_must_not_erase_independent_model_text(tmp_path):
    path = _vector_grid_pdf(tmp_path, merged_header=True)
    with fitz.open(path) as pdf:
        tables = vision_service._native_table_candidates(pdf[0])
    assert len(tables) == 1
    assert tables[0]["merges"] == [{"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1}]

    async def respond(**kwargs):
        return _contradictory_table()

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        with pytest.raises(vision_service.ResponsesEvidenceError, match=r"page 1/1: Invalid Responses merge covers nonempty evidence"):
            vision_service.VisionLayoutService().analyze_full_pdf(path)


def test_without_source_geometry_conflicting_merge_still_fails_closed(tmp_path):
    async def respond(**kwargs):
        return _contradictory_table()

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        with pytest.raises(vision_service.ResponsesEvidenceError, match="merge covers nonempty evidence"):
            vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path, pages=1))


def test_real_async_responses_cancellation_closes_sibling_provider(tmp_path):
    from ppap_pdf_to_excel.vision import client as response_client

    start_sibling = asyncio.Event()
    called: list[int] = []
    cancelled: list[int] = []
    closed: list[int] = []

    class StubClient:
        def __init__(self):
            self.identity = len(called) + 1
            self.responses = self

        async def create(self, **kwargs):
            number = len(called) + 1
            called.append(number)
            if number == 1:
                await start_sibling.wait()
                return SimpleNamespace(status="completed", output_text=json.dumps(_contradictory_table()))
            start_sibling.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(number)
                raise

        async def close(self):
            closed.append(self.identity)

    before = time.monotonic()
    with (
        patch.object(response_client, "_get_llm_client", side_effect=StubClient),
        patch.object(response_client, "_get_llm_model_name", return_value="unit-test"),
    ):
        with pytest.raises(vision_service.ResponsesEvidenceError, match="merge covers nonempty evidence"):
            vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path, pages=2))
    assert time.monotonic() - before < 3
    assert called == [1, 2]
    assert cancelled == [2]
    assert sorted(closed) == [1, 2]


def test_four_pdf_pages_never_exceed_two_concurrent_model_requests(tmp_path):
    active = 0
    peak = 0
    completed = []

    async def respond(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            completed.append(1)
            return {"tables": [], "paragraphs": ["visible"], "seals": []}
        finally:
            active -= 1

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        pages = vision_service.VisionLayoutService().analyze_full_pdf(_pdf(tmp_path, pages=4))
    assert len(pages) == len(completed) == 4
    assert peak == 2


def test_flattened_model_grid_uses_native_pdf_text_without_losing_any_fragment(tmp_path):
    path = _vector_grid_pdf(tmp_path)
    evidence = _contradictory_table()
    # The model may create a spurious blank header row; PDF physical rows
    # and source text, rather than the model's invented row shape, then own the grid.
    evidence["tables"][0]["rows"].insert(3, ["", "", ""])

    async def respond(**kwargs):
        return evidence

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        pages = vision_service.VisionLayoutService().analyze_full_pdf(path)
    assert pages[0]["tables"][0]["rows"] == [["ALPHA", "BETA", "GAMMA"], ["A2", "B2", "C2"], ["A3", "B3", "C3"]]
    assert pages[0]["tables"][0]["merges"] == []


def test_source_cell_alignment_rejects_unmatched_or_ambiguous_model_evidence(caplog):
    source = [["ALPHA", "BETA", "GAMMA"], ["A2", "B2", "C2"], ["A3", "B3", "C3"]]
    table = _contradictory_table()["tables"][0]
    table["rows"][0][1] = "private hallucinated evidence"
    with caplog.at_level("WARNING"):
        result = vision_service._reconcile_physical_merges(
            {"tables": [table], "paragraphs": [], "seals": []},
            [{"rows": source, "merges": []}],
        )
    assert result is None
    assert "missing_or_ambiguous_source_text" in caplog.text
    assert "private hallucinated evidence" not in caplog.text


def test_source_cell_alignment_keeps_original_merged_cell_text():
    native = [
        ["ALPHA BETA", None, "GAMMA"],
        ["A2", "B2", "C2"],
        ["A3", "B3", "C3"],
    ]
    attested = {"tables": [_contradictory_table()["tables"][0]], "paragraphs": [], "seals": []}
    repaired = vision_service._reconcile_physical_merges(
        attested,
        [{"rows": native, "merges": [{"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1}]}],
    )
    assert repaired is not None
    assert repaired["tables"][0]["rows"][0] == ["ALPHA BETA", "", "GAMMA"]
    assert repaired["tables"][0]["merges"] == [{"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1}]


def test_staggered_source_columns_preserve_native_colspans_without_model_guess(tmp_path):
    path = tmp_path / "staggered.pdf"
    with fitz.open() as document:
        page = document.new_page(width=360, height=220)
        for y in (30, 70, 110, 150):
            page.draw_line((20, y), (320, y))
        for x in (20, 220, 320):
            page.draw_line((x, 30), (x, 150))
        page.draw_line((170, 30), (170, 70))
        page.draw_line((120, 70), (120, 150))
        for word, position in (
            ("ALPHA", (26, 49)),
            ("BETA", (177, 49)),
            ("GAMMA", (238, 49)),
            ("A2", (26, 89)),
            ("B2", (145, 89)),
            ("C2", (235, 89)),
            ("A3", (26, 129)),
            ("B3", (145, 129)),
            ("C3", (235, 129)),
        ):
            page.insert_text(position, word, fontsize=10)
        document.save(path)

    with fitz.open(path) as original:
        sources = vision_service._native_table_candidates(original[0])
    assert len(sources) == 1
    assert sources[0]["rows"] == [
        ["ALPHA", "", "BETA", "GAMMA"],
        ["A2", "B2", "", "C2"],
        ["A3", "B3", "", "C3"],
    ]
    assert {"start_row": 0, "start_col": 0, "end_row": 0, "end_col": 1} in sources[0]["merges"]
    assert {"start_row": 1, "start_col": 1, "end_row": 1, "end_col": 2} in sources[0]["merges"]

    async def respond(**kwargs):
        return _contradictory_table()

    with patch.object(vision_service, "call_llm_async", side_effect=respond):
        pages = vision_service.VisionLayoutService().analyze_full_pdf(str(path))
    assert pages[0]["tables"][0] == sources[0]
