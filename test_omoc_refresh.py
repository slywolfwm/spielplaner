from datetime import date, datetime, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

from omoc import BOOKING_COLUMNS, OmocError
from omoc_refresh import BERLIN, nightly_window, refresh_hall_bookings


def test_nightly_start_is_stable_and_inside_the_berlin_window_on_clock_changes():
    days = [date(2026, 3, 29), date(2026, 10, 25), date(2026, 11, 25), date(2026, 11, 26)]
    local_times = []
    for day in days:
        start, end = nightly_window(day)
        assert nightly_window(day) == (start, end)
        assert start < end
        local = start.astimezone(BERLIN)
        assert local.date() == day and 1 <= local.hour < 5
        assert end.astimezone(BERLIN).hour == 5
        local_times.append(local.time())
    assert len(set(local_times)) > 1


def test_nightly_refresh_publishes_one_complete_season_snapshot():
    store = Mock()
    store.latest_bookings.return_value = None
    store.save_bookings.return_value.updated_at = datetime(2026, 11, 25, 4, tzinfo=timezone.utc)
    store.save_bookings.return_value.date_from = date(2026, 7, 1)
    store.save_bookings.return_value.date_to = date(2027, 6, 30)
    client = Mock()
    bookings = pd.DataFrame(columns=BOOKING_COLUMNS)
    client.fetch_bookings.return_value = bookings
    due, _ = nightly_window(date(2026, 11, 25))

    snapshot = refresh_hall_bookings(store, client, "2026-27", "Nachtlauf", due_since=due)
    store.latest_bookings.return_value = snapshot
    assert refresh_hall_bookings(store, client, "2026-27", "Nachtlauf", due_since=due) is None

    client.fetch_bookings.assert_called_once_with(date(2026, 7, 1), date(2027, 6, 30))
    store.save_bookings.assert_called_once_with(
        "2026-27", bookings, date(2026, 7, 1), date(2027, 6, 30), "Nachtlauf"
    )


def test_failed_manual_refresh_leaves_the_previous_export_in_place():
    store = Mock()
    client = Mock()
    client.fetch_bookings.side_effect = OmocError("OMOC nicht erreichbar")
    with pytest.raises(OmocError):
        refresh_hall_bookings(store, client, "2026-27", "Bearbeiter")
    store.save_bookings.assert_not_called()
    store.acquire_refresh_lease.return_value.release.assert_called_once()
