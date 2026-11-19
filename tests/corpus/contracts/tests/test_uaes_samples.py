"""UAES 样本批次：零件目录整批，缺陷样本换掉对应正常件后单独成批。"""

from corpus_contracts.uaes_samples import batches


def test_each_part_runs_once_and_each_defect_sample_replaces_its_normal_files():
    found = batches()
    normal = [batch for batch in found if not batch.defect]
    defects = [batch for batch in found if batch.defect]
    assert len(normal) == 10 and len(defects) == 4
    assert sum(len(batch.files) for batch in found) == 58
    a40 = next(batch for batch in normal if batch.part == "F01RD0YA40_机加工壳体")
    for batch in defects:
        # 同批不会同时出现正常件与它的缺陷件；其余正常件照常同批提交。
        assert set(batch.others) < set(a40.files) and not set(batch.files) & set(a40.files)
        assert len(batch.others) + len(set(a40.files) - set(batch.others)) == len(a40.files)
    folder = next(batch for batch in defects if batch.defect.startswith("MSA-test-"))
    assert len(folder.files) == 7 and not any(path.parent.name == "06 测量系统分析评价" for path in folder.others)
    assert next(batch for batch in normal if batch.part == "F01R10Y776_机加壳体").references
