from datetime import datetime
from types import SimpleNamespace

import pytest

from schedule_store import ScheduleStore


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
            "last_modified": datetime.fromisoformat(metadata["uploadedat"]),
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


def test_schedule_upload_is_versioned_and_latest_keeps_metadata():
    store = ScheduleStore(FakeContainer())

    first = store.save_schedule(
        "2026-27", "Plan älter.csv", b"first", "user-a"
    )
    second = store.save_schedule(
        "2026-27", "Plan aktuell.csv", b"second", "user-b"
    )
    latest = store.latest_schedule("2026-27")

    assert first.blob_name != second.blob_name
    assert latest is not None
    assert latest.original_name == "Plan aktuell.csv"
    assert latest.content == b"second"
    assert latest.uploaded_by == "user-b"


def test_empty_schedule_cannot_be_saved():
    store = ScheduleStore(FakeContainer())

    with pytest.raises(ValueError):
        store.save_schedule("2026-27", "leer.csv", b"", "user")
