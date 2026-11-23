"""Connection states are read from their own dates and said in local time; a connection is told by when it was granted."""

from datetime import UTC, datetime

from api.mcp_pages import connection_state, local_time

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def entry(**dates):
    return {"created_at": "2026-11-22T10:00:00+00:00", "expires_at": "2026-11-29T22:11:53.459536+00:00", "revoked_at": None, **dates}


def test_an_expired_connection_is_not_shown_as_valid():
    assert connection_state(entry(expires_at="2026-10-30T18:39:43.266589+00:00"), NOW) == ("expired", "11 月 22 日 18:00 授权", "已过期（10 月 31 日）")


def test_a_revoked_connection_says_when():
    assert connection_state(entry(revoked_at="2026-11-22T11:00:00+00:00"), NOW) == ("revoked", "11 月 22 日 18:00 授权", "已撤销（11 月 22 日）")


def test_a_valid_connection_says_when_it_was_granted_and_ends_in_local_time():
    assert connection_state(entry(), NOW) == ("active", "11 月 22 日 18:00 授权", "11 月 30 日 06:11 到期")


def test_another_year_is_named():
    assert local_time("2025-12-31T17:00:00+00:00", NOW) == "1 月 1 日 01:00"  # already 2026 in Shanghai
    assert local_time("2025-06-01T00:00:00+00:00", NOW, clock=False) == "2025 年 6 月 1 日"
