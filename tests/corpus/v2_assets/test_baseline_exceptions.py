from pathlib import Path


def test_corpus_conftest_does_not_blanket_xfail_files() -> None:
    assert "BASELINE_XFAIL_IDS" not in source
    assert "pytest.mark.xfail" not in source
    import ppap_corpus_contracts.baseline_exceptions as baseline_exceptions
    
    exceptions = Path(baseline_exceptions.__file__).read_text()
    assert "BaselineRuleBehavior" in exceptions
    assert "ExecutionOutcome" in exceptions
    assert "suggestions" not in exceptions
