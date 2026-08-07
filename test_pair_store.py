from pair_store import PairStore, normalize_pair, pair_row_key


class FakeTableClient:
    def __init__(self):
        self.entities: dict[tuple[str, str], dict[str, str]] = {}

    def query_entities(self, query_filter: str):
        season = query_filter.split("'")[1]
        return [
            entity
            for (partition_key, _), entity in self.entities.items()
            if partition_key == season
        ]

    def upsert_entity(self, entity: dict[str, str], mode: object):
        key = (entity["PartitionKey"], entity["RowKey"])
        self.entities[key] = entity

    def delete_entity(self, partition_key: str, row_key: str):
        del self.entities[(partition_key, row_key)]


def test_pair_key_is_independent_of_selection_order():
    assert pair_row_key("Team A", "Team B") == pair_row_key("Team B", "Team A")
    assert normalize_pair("Team B", "Team A") == ("Team A", "Team B")


def test_save_list_and_delete_pair():
    table = FakeTableClient()
    store = PairStore(table)

    saved = store.save_pairs(
        "2026-27",
        [("Team B", "Team A"), ("Team A", "Team B")],
        "user-id",
    )
    pairs = store.list_pairs("2026-27")

    assert saved == 1
    assert len(pairs) == 1
    assert pairs[0].team_a == "Team A"
    assert pairs[0].team_b == "Team B"
    assert pairs[0].created_by == "user-id"

    store.delete_pair("2026-27", pairs[0].row_key)
    assert store.list_pairs("2026-27") == []
