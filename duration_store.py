from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from azure.data.tables import TableServiceClient, UpdateMode


@dataclass(frozen=True)
class StoredDuration:
    team_key: str
    team_label: str
    minutes: int
    extra_minutes: int | None


class DurationStore:
    def __init__(self, table_client: object):
        self.table_client = table_client

    @classmethod
    def from_connection_string(
        cls, connection_string: str, table_name: str = "teamdurations"
    ) -> "DurationStore":
        service = TableServiceClient.from_connection_string(connection_string)
        return cls(service.create_table_if_not_exists(table_name))

    def list_durations(self, season: str) -> dict[str, StoredDuration]:
        safe_season = season.replace("'", "''")
        entities = self.table_client.query_entities(
            query_filter=f"PartitionKey eq '{safe_season}'"
        )
        return {
            str(entity["TeamKey"]): StoredDuration(
                team_key=str(entity["TeamKey"]),
                team_label=str(entity["TeamLabel"]),
                minutes=int(entity["Minutes"]),
                extra_minutes=(
                    int(entity["ExtraMinutes"])
                    if "ExtraMinutes" in entity
                    else None
                ),
            )
            for entity in entities
        }

    def save_durations(
        self,
        season: str,
        durations: Iterable[tuple[str, str, int, int]],
        updated_by: str,
    ) -> int:
        saved = 0
        for team_key, team_label, minutes, extra_minutes in durations:
            if minutes <= 0:
                raise ValueError("Die Spieldauer muss größer als null sein.")
            if extra_minutes < 0:
                raise ValueError("Der Unterbrechungspuffer darf nicht negativ sein.")
            self.table_client.upsert_entity(
                {
                    "PartitionKey": season,
                    "RowKey": hashlib.sha256(team_key.encode("utf-8")).hexdigest(),
                    "TeamKey": team_key,
                    "TeamLabel": team_label,
                    "Minutes": minutes,
                    "ExtraMinutes": extra_minutes,
                    "UpdatedBy": updated_by,
                    "UpdatedAt": datetime.now(timezone.utc).isoformat(),
                },
                mode=UpdateMode.REPLACE,
            )
            saved += 1
        return saved
