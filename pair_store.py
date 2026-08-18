from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from azure.data.tables import TableServiceClient, UpdateMode

from priorities import DEFAULT_PAIR_PRIORITY, normalize_priority


@dataclass(frozen=True)
class StoredPair:
    row_key: str
    team_a: str
    team_b: str
    priority: str = DEFAULT_PAIR_PRIORITY
    created_by: str = ""
    created_at: str = ""

    @property
    def label(self) -> str:
        return f"[{self.priority}] {self.team_a} ↔ {self.team_b}"


class PairStore:
    def __init__(self, table_client: object):
        self.table_client = table_client

    @classmethod
    def from_connection_string(
        cls, connection_string: str, table_name: str = "teampairs"
    ) -> "PairStore":
        service = TableServiceClient.from_connection_string(connection_string)
        return cls(service.create_table_if_not_exists(table_name))

    def list_pairs(self, season: str) -> list[StoredPair]:
        safe_season = season.replace("'", "''")
        entities = self.table_client.query_entities(
            query_filter=f"PartitionKey eq '{safe_season}'"
        )
        pairs = [
            StoredPair(
                row_key=str(entity["RowKey"]),
                team_a=str(entity["TeamA"]),
                team_b=str(entity["TeamB"]),
                priority=normalize_priority(entity.get("Priority")),
                created_by=str(entity.get("CreatedBy", "")),
                created_at=str(entity.get("CreatedAt", "")),
            )
            for entity in entities
        ]
        return sorted(pairs, key=lambda pair: pair.label.casefold())

    def save_pairs(
        self,
        season: str,
        pairs: Iterable[tuple[str, str] | tuple[str, str, str]],
        created_by: str,
    ) -> int:
        saved = 0
        seen_row_keys: set[str] = set()
        for pair in pairs:
            team_a, team_b = pair[:2]
            priority = normalize_priority(pair[2] if len(pair) == 3 else None)
            team_a, team_b = normalize_pair(team_a, team_b)
            row_key = pair_row_key(team_a, team_b)
            if row_key in seen_row_keys:
                continue
            seen_row_keys.add(row_key)
            self.table_client.upsert_entity(
                {
                    "PartitionKey": season,
                    "RowKey": row_key,
                    "TeamA": team_a,
                    "TeamB": team_b,
                    "Priority": priority,
                    "CreatedBy": created_by,
                    "CreatedAt": datetime.now(timezone.utc).isoformat(),
                },
                mode=UpdateMode.REPLACE,
            )
            saved += 1
        return saved

    def delete_pair(self, season: str, row_key: str) -> None:
        self.table_client.delete_entity(partition_key=season, row_key=row_key)

    def replace_pairs(
        self,
        season: str,
        pairs: Iterable[tuple[str, str] | tuple[str, str, str]],
        created_by: str,
    ) -> int:
        normalized: dict[str, tuple[str, str, str]] = {}
        for pair in pairs:
            team_a, team_b = normalize_pair(pair[0], pair[1])
            priority = normalize_priority(pair[2] if len(pair) == 3 else None)
            normalized[pair_row_key(team_a, team_b)] = (
                team_a,
                team_b,
                priority,
            )

        self.save_pairs(season, normalized.values(), created_by)
        existing_keys = {pair.row_key for pair in self.list_pairs(season)}
        for row_key in existing_keys.difference(normalized):
            self.delete_pair(season, row_key)
        return len(normalized)


def normalize_pair(team_a: str, team_b: str) -> tuple[str, str]:
    if team_a == team_b:
        raise ValueError("Eine Mannschaft kann nicht mit sich selbst kombiniert werden.")
    return tuple(sorted((team_a, team_b), key=str.casefold))


def pair_row_key(team_a: str, team_b: str) -> str:
    normalized = normalize_pair(team_a, team_b)
    value = "\0".join(normalized).encode("utf-8")
    return hashlib.sha256(value).hexdigest()
