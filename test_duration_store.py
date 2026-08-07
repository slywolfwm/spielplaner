from duration_store import DurationStore


class FakeTableClient:
    def __init__(self):
        self.entities: dict[tuple[str, str], dict[str, object]] = {}

    def query_entities(self, query_filter: str):
        season = query_filter.split("'")[1]
        return [
            entity
            for (partition_key, _), entity in self.entities.items()
            if partition_key == season
        ]

    def upsert_entity(self, entity: dict[str, object], mode: object):
        key = (str(entity["PartitionKey"]), str(entity["RowKey"]))
        self.entities[key] = entity


def test_save_and_load_team_durations():
    store = DurationStore(FakeTableClient())

    saved = store.save_durations(
        "2026-27",
        [("team-a", "A-Jugend", 70, 10), ("team-b", "B-Jugend", 60, 15)],
        "user-id",
    )
    durations = store.list_durations("2026-27")

    assert saved == 2
    assert durations["team-a"].minutes == 70
    assert durations["team-a"].extra_minutes == 10
    assert durations["team-b"].minutes == 60
    assert durations["team-b"].extra_minutes == 15


def test_old_duration_leaves_stoppage_buffer_for_team_default():
    table = FakeTableClient()
    table.entities[("2026-27", "old")] = {
        "PartitionKey": "2026-27",
        "RowKey": "old",
        "TeamKey": "team-a",
        "TeamLabel": "A-Jugend",
        "Minutes": 70,
    }

    duration = DurationStore(table).list_durations("2026-27")["team-a"]

    assert duration.extra_minutes is None
