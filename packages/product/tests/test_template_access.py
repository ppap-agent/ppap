"""Cached templates are not an alternate local permission system."""

from product.identity import Principal, Resource, can_modify, can_read


def test_a_template_is_never_read_or_changed_as_an_audit_resource():
    principal = Principal("user", "tenant", "feishu", "app", "tenant-key", "subject", "member")
    resource = Resource("template", "tenant", "template", created_by="user")
    assert not can_read(principal, resource)
    assert not can_modify(principal, resource)
