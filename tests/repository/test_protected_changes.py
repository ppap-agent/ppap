import subprocess
import sys
from pathlib import Path

from scripts.check_protected_changes import problems

ROOT = Path(__file__).resolve().parents[2]
OPEN_ID = "ou_" + "0123456789abcdef" * 2


def test_an_old_migration_is_never_rewritten_without_a_stated_reason():
    edited = [("M", "migrations/0085_rule_questionnaires.sql", "migrations/0085_rule_questionnaires.sql")]
    assert problems(edited, set(), [], "fix questionnaires") == ["migrations/0085_rule_questionnaires.sql：已有迁移不能修改、改名或删除，请新增迁移"]
    assert problems([("R", "migrations/0085_a.sql", "migrations/0091_a.sql")], set(), [], "renumber")
    assert problems(edited, set(), [], "fix\n\nProtected-Change: only makes the old one safe to re-run") == []
    assert problems([("A", "migrations/0091_new.sql", "migrations/0091_new.sql"), ("M", "migrations/README.md", "migrations/README.md")], set(), [], "") == []


def test_protected_assets_may_move_but_not_disappear():
    checker = "packages/audit/src/audit/checkers/uaes/U01/checker.py"
    assert problems([("D", checker, checker)], set(), [], "cleanup") == [f"{checker}：受保护资产不能删除"]
    assert problems([], {"assets/corpus/uaes-oracle.csv"}, [], "cleanup") == ["assets/corpus/uaes-oracle.csv：受保护资产不能清空"]
    assert problems([("R", checker, "packages/audit/src/audit/checkers/uaes/U01/main.py"), ("D", "packages/audit/src/audit/checker_lib/excel.py", "")], set(), [], "") == []
    replaced = "assets/uaes/prechecks/PPAP-00_总纲_v0.14.md"
    assert problems([("D", replaced, replaced)], set(), [], "预检规则换新版\n\nProtected-Change: 客户资料包新版替换旧版") == []


def test_secrets_have_no_exception():
    added = [("tests/fixture.py", f'OPERATOR = "tenant:{OPEN_ID}"'), ("x.py", "key = 'sk-" + "A" * 24 + "'")]
    assert problems([], set(), added, "Protected-Change: test data") == ["tests/fixture.py：新增内容含飞书用户标识", "x.py：新增内容含模型 API Key"]
    assert problems([], set(), [("x.py", 'owner = "ou_owner"')], "") == []


def test_the_commit_hook_reads_the_staged_change(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations/0001_a.sql").write_text("CREATE TABLE a ();\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "a")
    (tmp_path / "migrations/0001_a.sql").write_text("CREATE TABLE a (b int);\n")
    (tmp_path / "migrations/0002_b.sql").write_text(f"-- {OPEN_ID}\n")
    git("add", ".")
    message = tmp_path / "MSG"
    message.write_text("change a\n")
    run = subprocess.run([sys.executable, str(ROOT / "scripts/check_protected_changes.py"), "--staged", str(message)], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 1
    assert run.stdout.splitlines()[:2] == ["migrations/0001_a.sql：已有迁移不能修改、改名或删除，请新增迁移", "migrations/0002_b.sql：新增内容含飞书用户标识"]
