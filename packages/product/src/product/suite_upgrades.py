"""Carry immutable enterprise file changes onto an explicitly selected official base."""

from __future__ import annotations

from dataclasses import dataclass

from product.suites import ResolvedSuite


@dataclass(frozen=True)
class SuiteUpgrade:
    files: dict[str, bytes]
    deleted_paths: tuple[str, ...]
    related_suite_keys: tuple[str, ...]


def carry_enterprise_changes(source: ResolvedSuite, old_base: ResolvedSuite, new_base: ResolvedSuite) -> SuiteUpgrade:
    """File-level overrides remain exact, including conflicting enterprise edits.

    Unmodified/reverted paths inherit the new official bytes. This deliberately
    does not claim to merge Python or reinterpret rules; the existing development
    loop must validate the candidate, and adoption is a separate command.
    """
    if old_base.revision.tenant_id is not None or new_base.revision.tenant_id is not None:
        raise ValueError("suite_upgrade_official_base_required")
    if len({item.revision.suite_key for item in (source, old_base, new_base)}) != 1:
        raise ValueError("suite_key_mismatch")
    files = {path: body for path, body in source.files.items() if source.object_digests[path] != old_base.object_digests.get(path)}
    deleted = tuple(sorted(path for path in old_base.files if path not in source.files and path in new_base.files))
    before, after = set(old_base.revision.related_suite_keys), set(source.revision.related_suite_keys)
    related = (set(new_base.revision.related_suite_keys) | (after - before)) - (before - after)
    return SuiteUpgrade(files, deleted, tuple(sorted(related)))
