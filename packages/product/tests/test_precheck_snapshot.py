from types import SimpleNamespace

from product import precheck_snapshot


class Store:
    """Reads an original only through the caller's principal, as AuthStore does."""

    def __init__(self, bodies):
        self.bodies, self.reads = bodies, []

    def read_file_version(self, principal, file_id, version_id):
        self.reads.append((principal, file_id, version_id))
        return SimpleNamespace(id=version_id, sha256="same", original_filename=f"{version_id}.xlsx")

    def version_bytes(self, version):
        return self.bodies[version.id]


def row(version_id, *, preferred=None):
    return {
        "file": {"id": version_id, "file_id": "f-" + version_id, "original_filename": "控制计划.xlsx"},
        "selection": "selected",
        "validation": {"state": "valid"},
        "preferred_rule_version_id": preferred,
        "match": {
            "state": "matched",
            "document_type": "控制计划",
            "rule": {"id": "rule-1", "body": {"customers": ["理想"]}},
            "metadata": {"customer": "LI", "part_number": "P1", "part": "壳体"},
        },
    }


def test_content_passes_the_user_choice_and_keys_its_cache_by_it(monkeypatch):
    calls = []
    monkeypatch.setattr(precheck_snapshot, "precheck_content", lambda body, name, key, *, customer, named: calls.append(named) or [])
    precheck_snapshot._CACHE.clear()
    store = Store({"a": b"x", "b": b"x"})
    # Same bytes, entry and customer: a hand-picked rule still reads separately from a recognized one.
    precheck_snapshot._content(store, "alice", {"customer_preference": "理想", "files": [row("a", preferred="rule-1")]})
    precheck_snapshot._content(store, "alice", {"customer_preference": "理想", "files": [row("b")]})
    precheck_snapshot._content(store, "alice", {"customer_preference": "理想", "files": [row("b")]})
    assert calls == [True, False]
    assert {principal for principal, *_ in store.reads} == {"alice"}


def test_precheck_reads_same_name_copies_through_the_principal(monkeypatch):
    seen = {}

    def precheck_task(task, content, read):
        seen["bytes"] = [read(item) for item in task["files"]]
        return {"warnings": []}

    monkeypatch.setattr(precheck_snapshot, "precheck_task", precheck_task)
    monkeypatch.setattr(precheck_snapshot, "_content", lambda store, principal, task: {})
    store = Store({"a": b"first", "b": b"second"})
    snapshot = precheck_snapshot.current_precheck(store, "alice", {"files": [row("a"), row("b")]})
    assert seen["bytes"] == [b"first", b"second"]
    assert store.reads == [("alice", "f-a", "a"), ("alice", "f-b", "b")]
    assert snapshot["precheck"] == {"warnings": []} and snapshot["generated_at"]


def test_file_warnings_are_the_audited_files_own_and_absent_without_a_snapshot():
    snapshot = {"precheck": {"warnings": [{"file_version_id": "a", "code": "W02"}, {"file_version_id": "b", "code": "W01"}]}}
    assert precheck_snapshot.file_warnings(snapshot, "a") == [{"file_version_id": "a", "code": "W02"}]
    assert precheck_snapshot.file_warnings(None, "a") == []


def test_the_cover_customer_is_the_one_the_files_write_never_the_chosen_rule_set():
    written, silent = row("a"), row("b")
    written["match"]["metadata"] = {"customer": "联合汽车电子有限公司", "part_number": "P1", "part": "壳体"}
    silent["match"]["metadata"] = {"customer": None, "part_number": "P1", "part": "壳体"}
    assert precheck_snapshot.cover_recognition({"customer_preference": "UAES", "files": [written, silent]})["customer"] == "联合汽车电子有限公司"
    assert precheck_snapshot.cover_recognition({"customer_preference": "UAES", "files": [silent]})["customer"] is None
