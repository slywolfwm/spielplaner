from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
from urllib.parse import quote, unquote

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings
import pandas as pd

from omoc import BOOKING_COLUMNS


@dataclass(frozen=True)
class StoredHallBookings:
    blob_name: str
    bookings: pd.DataFrame
    date_from: date
    date_to: date
    updated_at: datetime
    updated_by: str


class HallBookingStore:
    def __init__(self, container_client: object):
        self.container_client = container_client

    @classmethod
    def from_connection_string(
        cls, connection_string: str, container_name: str = "hall-bookings"
    ) -> "HallBookingStore":
        service = BlobServiceClient.from_connection_string(connection_string)
        container = service.get_container_client(container_name)
        try:
            container.create_container()
        except ResourceExistsError:
            pass
        return cls(container)

    def save_bookings(
        self,
        season: str,
        bookings: pd.DataFrame,
        date_from: date,
        date_to: date,
        updated_by: str,
    ) -> StoredHallBookings:
        if date_to < date_from:
            raise ValueError("Das Enddatum liegt vor dem Startdatum.")
        content = _serialize_bookings(bookings)
        now = datetime.now(timezone.utc)
        digest = hashlib.sha256(content).hexdigest()[:12]
        timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        blob_name = f"{_safe_segment(season)}/{timestamp}-{digest}.json"
        metadata = {
            "datefrom": date_from.isoformat(),
            "dateto": date_to.isoformat(),
            "updatedat": now.isoformat(),
            "updatedby": quote(updated_by, safe=""),
        }
        self.container_client.get_blob_client(blob_name).upload_blob(
            content,
            overwrite=False,
            metadata=metadata,
            content_settings=ContentSettings(
                content_type="application/json; charset=utf-8"
            ),
        )
        return StoredHallBookings(
            blob_name=blob_name,
            bookings=_deserialize_bookings(content),
            date_from=date_from,
            date_to=date_to,
            updated_at=now,
            updated_by=updated_by,
        )

    def latest_bookings(self, season: str) -> StoredHallBookings | None:
        prefix = f"{_safe_segment(season)}/"
        blobs = list(
            self.container_client.list_blobs(
                name_starts_with=prefix,
                include=["metadata"],
            )
        )
        if not blobs:
            return None
        latest = max(blobs, key=lambda blob: blob.last_modified)
        metadata = latest.metadata or {}
        content = self.container_client.get_blob_client(
            latest.name
        ).download_blob().readall()
        return StoredHallBookings(
            blob_name=str(latest.name),
            bookings=_deserialize_bookings(content),
            date_from=date.fromisoformat(metadata["datefrom"]),
            date_to=date.fromisoformat(metadata["dateto"]),
            updated_at=_as_datetime(metadata.get("updatedat") or latest.last_modified),
            updated_by=unquote(metadata.get("updatedby", "")),
        )


def _serialize_bookings(bookings: pd.DataFrame) -> bytes:
    missing = set(BOOKING_COLUMNS).difference(bookings.columns)
    if missing:
        raise ValueError(
            f"Fehlende Hallenbuchungsspalten: {', '.join(sorted(missing))}"
        )
    rows = []
    for booking in bookings.to_dict("records"):
        rows.append(
            {
                "booking_number": str(booking["Buchungsnummer"]),
                "starts_at": pd.Timestamp(booking["Buchungsbeginn"]).isoformat(),
                "ends_at": pd.Timestamp(booking["Buchungsende"]).isoformat(),
                "room_ids": sorted(str(value) for value in booking["Raum-IDs"]),
            }
        )
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _deserialize_bookings(content: bytes) -> pd.DataFrame:
    rows = []
    for booking in json.loads(content.decode("utf-8")):
        rows.append(
            {
                "Buchungsnummer": str(booking["booking_number"]),
                "Buchungsbeginn": pd.Timestamp(booking["starts_at"]).to_pydatetime(),
                "Buchungsende": pd.Timestamp(booking["ends_at"]).to_pydatetime(),
                "Raum-IDs": frozenset(str(value) for value in booking["room_ids"]),
            }
        )
    return pd.DataFrame(rows, columns=BOOKING_COLUMNS)


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "bookings"


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)
