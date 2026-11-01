"""V3 POC oracle through the Standard product round."""

from __future__ import annotations

import pytest
from audit.rule_profile import RULE_PROFILE_V3
from corpus_contracts.poc_oracle import POC_SEQ_TO_DOC_TYPE, build_bundle_map, load_poc_cases
from corpus_contracts.product_round import (
    compare_file,
    execute_product_round,
    failing_verdicts,
    format_verdicts,
)
from product import run_round_from_bytes


@pytest.mark.usefixtures("ocr_transport_diagnostics")
def test_v3_product_round_matches_oracle() -> None:
    bundles = build_bundle_map(load_poc_cases())
    failures = []
    for bundle_id, cases in sorted(bundles.items()):
        files = [(case.file_name, case.file_path, POC_SEQ_TO_DOC_TYPE[case.file_seq].get_type_name()) for case in cases]
        result, versions, setup_error = execute_product_round(
            files,
            profile=RULE_PROFILE_V3,
            round_id=bundle_id,
            runner=run_round_from_bytes,
        )
        for case in cases:
            expected = {index: item.status for index, item in case.expected.items()}
            failures.extend(
                failing_verdicts(
                    compare_file(
                        file_name=case.file_name,
                        version_id=versions.get(case.file_name, ""),
                        expected=expected,
                        result=result,
                        setup_error=setup_error,
                    )
                )
            )
    if failures:
        pytest.fail(format_verdicts(failures))
