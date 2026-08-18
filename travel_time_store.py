from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from zoneinfo import ZoneInfo

from azure.data.tables import TableServiceClient, UpdateMode


@dataclass(frozen=True)
class StoredTravelTime:
    source_minutes: int
    planning_minutes: int
    distance_meters: int
    valid_until: datetime


class TravelTimeStore:
    PARTITION_KEY = "azure-maps-v1"

    def __init__(self, table_client: object):
        self.table_client = table_client

    @classmethod
    def from_connection_string(
        cls, connection_string: str, table_name: str = "traveltimes"
    ) -> "TravelTimeStore":
        service = TableServiceClient.from_connection_string(connection_string)
        return cls(service.create_table_if_not_exists(table_name))

    def get(
        self,
        origin_key: object,
        destination_key: object,
        departure: object,
        now: datetime | None = None,
    ) -> StoredTravelTime | None:
        try:
            entity = self.table_client.get_entity(
                partition_key=self.PARTITION_KEY,
                row_key=travel_time_row_key(
                    origin_key, destination_key, departure
                ),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404 or isinstance(exc, KeyError):
                return None
            raise

        valid_until = _as_utc_datetime(entity["ValidUntil"])
        current = _as_utc_datetime(now or datetime.now(timezone.utc))
        if valid_until <= current:
            return None
        return StoredTravelTime(
            source_minutes=int(entity["SourceMinutes"]),
            planning_minutes=int(entity["PlanningMinutes"]),
            distance_meters=int(entity.get("DistanceMeters", 0)),
            valid_until=valid_until,
        )

    def save(
        self,
        origin_key: object,
        destination_key: object,
        departure: object,
        source_minutes: int,
        planning_minutes: int,
        distance_meters: int,
        valid_until: datetime,
    ) -> None:
        if source_minutes <= 0 or planning_minutes <= 0:
            raise ValueError("Fahrzeiten müssen größer als null sein.")
        departure_value = _departure_profile(departure)
        self.table_client.upsert_entity(
            {
                "PartitionKey": self.PARTITION_KEY,
                "RowKey": travel_time_row_key(
                    origin_key, destination_key, departure
                ),
                "OriginKey": str(origin_key),
                "DestinationKey": str(destination_key),
                "DepartureProfile": departure_value,
                "SourceMinutes": source_minutes,
                "PlanningMinutes": planning_minutes,
                "DistanceMeters": distance_meters,
                "ValidUntil": _as_utc_datetime(valid_until),
                "UpdatedAt": datetime.now(timezone.utc),
            },
            mode=UpdateMode.REPLACE,
        )


def travel_time_row_key(
    origin_key: object, destination_key: object, departure: object
) -> str:
    raw = "\0".join(
        (
            str(origin_key),
            str(destination_key),
            _departure_profile(departure),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _departure_profile(value: object) -> str:
    parsed = _as_utc_datetime(value)
    local = parsed.astimezone(ZoneInfo("Europe/Berlin")).replace(
        second=0, microsecond=0
    )
    return f"{local.weekday()}-{local:%H:%M}"


def _as_utc_datetime(value: object) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("Ungültiger Zeitstempel.")
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Europe/Berlin"))
    return value.astimezone(timezone.utc)
