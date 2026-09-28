from ppap_corpus_contracts.v2_assets_shared import APP_ROOT, build_cases


def test_canonical_v2_required_variants_reconcile() -> None:
    expected_csv = APP_ROOT / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
    for ftype in ("excel", "pdf", "scanned"):
        cases = build_cases(
            dataset_root=dataset,
            expected_csv=expected_csv,
            ftype_filter=ftype,
        )
        assert cases
        assert all(case.file_path.is_file() for case in cases)
