from datetime import datetime, timedelta, timezone

from travel_time_store import TravelTimeStore


class MissingEntityError(Exception):
    status_code = 404


class FakeTableClient:
    def __init__(self):
        self.entities = {}

    def get_entity(self, partition_key: str, row_key: str):
        try:
            return self.entities[(partition_key, row_key)]
        except KeyError as exc:
            raise MissingEntityError from exc

    def upsert_entity(self, entity, mode):
        self.entities[(entity["PartitionKey"], entity["RowKey"])] = entity


def test_travel_time_cache_is_directional_and_expires():
    table = FakeTableClient()
    store = TravelTimeStore(table)
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    departure = datetime(2026, 10, 11, 12, 30)
    store.save("A", "B", departure, 60, 80, 42000, now + timedelta(days=1))

    cached = store.get("A", "B", departure, now)

    assert cached is not None
    assert cached.planning_minutes == 80
    assert store.get("B", "A", departure, now) is None
    assert store.get("A", "B", departure, now + timedelta(days=2)) is None
