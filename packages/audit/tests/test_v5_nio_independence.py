"""All five NIO artifacts run with historical customer imports unavailable."""

import ast
import builtins

import pytest
from audit.standard_suites import _CHECKERS_ROOT


@pytest.mark.parametrize(
    "entry,clause,sheets",
    [
        ("N01", "N01:5", {"测量报告": [["测点号", "类型"], ["P1", "CC"]]}),
        (
            "N02",
            "N02:1",
            {"PFMEA": [[name, "原件内容"] for name in ("公司名称", "车型项目", "零件名称", "零件号", "制造地址", "PFMEA开始日期", "PFMEA修订日期", "跨职能团队")]},
        ),
        ("N03", "N03:2", {"控制计划": [["特殊特性分类", "产品", "过程"], ["[CC]", "孔径", None]]}),
        ("N04", "N04:1", {"特殊特性清单": [["P1 特殊特性清单"]]}),
        ("N05", "N05:6", {"过程流程图": [["工序名称"], ["IQC"]]}),
    ],
)
def test_nio_leaf_program_and_resources_are_independent_of_old_routes(nio_execute, monkeypatch, entry, clause, sheets):
    original_import = builtins.__import__

    def no_historical_checker(name, *args, **kwargs):
        if name.startswith(("audit.checkers.v2", "audit.checkers.v3", "audit.checkers.v4")):
            pytest.fail("A standalone NIO leaf must not import historical customer business classes: " + name)
        return original_import(name, *args, **kwargs)

    leaf = _CHECKERS_ROOT / "v5" / "蔚来" / entry
    for filename in ("checker.py", "resources.py"):
        tree = ast.parse((leaf / filename).read_bytes())
        assert not any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("audit.checkers.") for node in ast.walk(tree))
        assert not any(isinstance(node, ast.Import) and any(alias.name.startswith("audit.checkers.") for alias in node.names) for node in ast.walk(tree))
    monkeypatch.setattr(builtins, "__import__", no_historical_checker)
    assert nio_execute(entry, clause, sheets)["status"] == "PASS"
