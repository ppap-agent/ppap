"""How many more readings one worker takes; a full pool never stops it from taking audits."""

from worker import formalization_room


def test_a_worker_keeps_room_for_a_persons_reading_and_stops_only_readings_when_full():
    assert formalization_room([]) == "any"
    assert formalization_room([True]) == "any"
    # Two batch readings in flight: only a person's own reading may join them.
    assert formalization_room([True, True]) == "manual"
    assert formalization_room([True, True, False]) == "manual"
    assert formalization_room([False, False, False]) == "any"
    assert formalization_room([True, False, False, False]) == "none"
