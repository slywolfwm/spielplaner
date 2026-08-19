from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd

from hall_booking_store import HallBookingStore


class FakeDownload:
    def __init__(self, content: bytes):
        self.content = content

    def readall(self):
        return self.content


class FakeBlobClient:
    def __init__(self, container: "FakeContainer", name: str):
        self.container = container
        self.name = name

    def upload_blob(
        self,
        content: bytes,
        overwrite: bool,
        metadata: dict[str, str],
        content_settings: object,
    ):
        self.container.blobs[self.name] = {
            "content": content,
            "metadata": metadata,
            "last_modified": datetime.fromisoformat(metadata["updatedat"]),
        }

    def download_blob(self):
        return FakeDownload(self.container.blobs[self.name]["content"])


class FakeContainer:
    def __init__(self):
        self.blobs: dict[str, dict[str, object]] = {}

    def get_blob_client(self, name: str):
        return FakeBlobClient(self, name)

    def list_blobs(self, name_starts_with: str, include: list[str]):
        return [
            SimpleNamespace(
                name=name,
                metadata=value["metadata"],
                last_modified=value["last_modified"],
            )
            for name, value in self.blobs.items()
            if name.startswith(name_starts_with)
        ]


def booking_frame(number: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Buchungsnummer": number,
                "Buchungsbeginn": datetime(2026, 9, 12, 10, 0),
                "Buchungsende": datetime(2026, 9, 12, 18, 0),
                "Raum-IDs": frozenset({"7702", "7703"}),
            }
        ]
    )


def test_hall_booking_snapshots_are_versioned_and_keep_metadata():
    store = HallBookingStore(FakeContainer())

    first = store.save_bookings(
        "2026-27",
        booking_frame("A"),
        date(2026, 9, 1),
        date(2027, 4, 30),
        "User A",
    )
    second = store.save_bookings(
        "2026-27",
        booking_frame("B"),
        date(2026, 9, 1),
        date(2027, 5, 1),
        "User B",
    )
    latest = store.latest_bookings("2026-27")

    assert first.blob_name != second.blob_name
    assert latest is not None
    assert latest.updated_by == "User B"
    assert latest.date_to == date(2027, 5, 1)
    assert latest.bookings.iloc[0]["Buchungsnummer"] == "B"
    assert latest.bookings.iloc[0]["Raum-IDs"] == frozenset({"7702", "7703"})


def test_empty_hall_booking_snapshot_can_be_saved():
    store = HallBookingStore(FakeContainer())
    empty = pd.DataFrame(
        columns=["Buchungsnummer", "Buchungsbeginn", "Buchungsende", "Raum-IDs"]
    )

    store.save_bookings(
        "2026-27", empty, date(2026, 9, 1), date(2027, 4, 30), "User"
    )
    latest = store.latest_bookings("2026-27")

    assert latest is not None
    assert latest.bookings.empty


def test_manual_bookings_are_stored_separately_from_omoc_snapshots():
    store = HallBookingStore(FakeContainer())
    period_start = date(2026, 9, 1)
    period_end = date(2027, 4, 30)

    store.save_bookings(
        "2026-27", booking_frame("OMOC"), period_start, period_end, "OMOC User"
    )
    store.save_manual_bookings(
        "2026-27",
        booking_frame("MANUELL"),
        period_start,
        period_end,
        "Manual User",
    )

    omoc = store.latest_bookings("2026-27")
    manual = store.latest_manual_bookings("2026-27")

    assert omoc is not None
    assert manual is not None
    assert omoc.bookings.iloc[0]["Buchungsnummer"] == "OMOC"
    assert manual.bookings.iloc[0]["Buchungsnummer"] == "MANUELL"
    assert manual.updated_by == "Manual User"
