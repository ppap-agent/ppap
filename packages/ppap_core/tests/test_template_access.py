"""Cached templates are not an alternate local permission system."""

from ppap_core.identity import Principal, Resource, can_read, can_review


def test_template_cache_never_inherits_generic_file_grants():
    principal = Principal("user", "tenant", "feishu", "app", "tenant-key", "subject", "member")
    resource = Resource("template", "tenant", "template")
    assert not can_read(principal, resource, {"read", "review"})
    assert not can_review(principal, resource, {"read", "review"})
