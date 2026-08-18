from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import quote, unquote

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings


@dataclass(frozen=True)
class StoredSchedule:
    blob_name: str
    original_name: str
    content: bytes
    uploaded_at: datetime
    uploaded_by: str


class ScheduleStore:
    def __init__(self, container_client: object):
        self.container_client = container_client

    @classmethod
    def from_connection_string(
        cls, connection_string: str, container_name: str = "schedules"
    ) -> "ScheduleStore":
        service = BlobServiceClient.from_connection_string(connection_string)
        container = service.get_container_client(container_name)
        try:
            container.create_container()
        except ResourceExistsError:
            pass
        return cls(container)

    def save_schedule(
        self,
        season: str,
        original_name: str,
        content: bytes,
        uploaded_by: str,
    ) -> StoredSchedule:
        if not content:
            raise ValueError("Die Spielplandatei ist leer.")
        now = datetime.now(timezone.utc)
        digest = hashlib.sha256(content).hexdigest()[:12]
        timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        blob_name = f"{_safe_segment(season)}/{timestamp}-{digest}.csv"
        metadata = {
            "originalname": quote(original_name, safe=""),
            "uploadedat": now.isoformat(),
            "uploadedby": quote(uploaded_by, safe=""),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        self.container_client.get_blob_client(blob_name).upload_blob(
            content,
            overwrite=False,
            metadata=metadata,
            content_settings=ContentSettings(content_type="text/csv"),
        )
        return StoredSchedule(
            blob_name=blob_name,
            original_name=original_name,
            content=content,
            uploaded_at=now,
            uploaded_by=uploaded_by,
        )

    def latest_schedule(self, season: str) -> StoredSchedule | None:
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
        uploaded_at = _as_datetime(
            metadata.get("uploadedat") or latest.last_modified
        )
        content = self.container_client.get_blob_client(
            latest.name
        ).download_blob().readall()
        return StoredSchedule(
            blob_name=str(latest.name),
            original_name=unquote(metadata.get("originalname", "Spielplan.csv")),
            content=content,
            uploaded_at=uploaded_at,
            uploaded_by=unquote(metadata.get("uploadedby", "")),
        )


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "schedule"


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)
