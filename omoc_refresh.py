from contextlib import suppress
from datetime import date, datetime, time, timedelta, timezone
import os
from random import Random
import sys
from time import sleep
from uuid import UUID
from zoneinfo import ZoneInfo

from azure.core.exceptions import AzureError
from azure.storage.queue import QueueClient

from hall_booking_store import HallBookingRefreshInProgress, HallBookingStore
from omoc import OmocClient


SEASON = "2026-27"
BERLIN = ZoneInfo("Europe/Berlin")
RETRY_DELAY = timedelta(minutes=5)


def nightly_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(1), BERLIN).astimezone(timezone.utc)
    end = datetime.combine(day, time(5), BERLIN).astimezone(timezone.utc)
    # Keep one random minute per night even when the App Service restarts.
    minutes = (end - start) // timedelta(minutes=1)
    return start + timedelta(minutes=Random(day.toordinal()).randrange(minutes)), end


def refresh_hall_bookings(
    store: HallBookingStore,
    client: OmocClient,
    season: str,
    updated_by: str,
    *,
    due_since: datetime | None = None,
):
    start_year = int(season.split("-", 1)[0])
    date_from, date_to = date(start_year, 7, 1), date(start_year + 1, 6, 30)
    lease = store.acquire_refresh_lease()
    try:
        if due_since is not None:
            latest = store.latest_bookings(season)
            if (latest and latest.updated_at >= due_since
                    and latest.date_from <= date_from and latest.date_to >= date_to):
                return None
        bookings = client.fetch_bookings(date_from, date_to)
        lease.renew()
        return store.save_bookings(season, bookings, date_from, date_to, updated_by)
    finally:
        with suppress(AzureError):
            lease.release()


def process_refresh_request(queue: QueueClient, store: HallBookingStore, client: OmocClient) -> None:
    message = next(iter(queue.receive_messages(max_messages=1, visibility_timeout=180)), None)
    if message is None:
        return
    try:
        request_id = UUID(message.content)
    except ValueError:
        queue.delete_message(message)
        return
    try:
        refresh_hall_bookings(store, client, SEASON, "Spieltagsplaner",
                              due_since=message.inserted_on)
        result = b"SUCCEEDED"
    except HallBookingRefreshInProgress:
        queue.update_message(message, visibility_timeout=10)
        return
    except Exception as error:
        print(f"OMOC-Abruf auf Anforderung fehlgeschlagen: {type(error).__name__}", flush=True)
        result = b"FAILED"
    store.container_client.get_blob_client(f"_refresh/{request_id}.status").upload_blob(
        result, overwrite=True
    )
    queue.delete_message(message)


def run_requested(connection: str, client: OmocClient) -> None:
    queue = QueueClient.from_connection_string(connection, "hall-booking-refresh",
                                               retry_total=0, connection_timeout=3, read_timeout=5)
    store = None
    while True:
        try:
            if store is None:
                store = booking_store(connection)
            process_refresh_request(queue, store, client)
        except AzureError as error:
            print(f"OMOC-Auftragswarteschlange nicht erreichbar: {type(error).__name__}", flush=True)
        sleep(10)


def booking_store(connection: str) -> HallBookingStore:
    return HallBookingStore.from_connection_string(
        connection, os.getenv("AZURE_HALL_BOOKING_CONTAINER_NAME") or "hall-bookings"
    )


def run_nightly(connection: str, client: OmocClient) -> None:
    store = None
    while True:
        now = datetime.now(timezone.utc)
        day = now.astimezone(BERLIN).date()
        start, end = nightly_window(day)
        if now >= end:
            start, end = nightly_window(day + timedelta(days=1))
        if now < start:
            print(f"OMOC-Nachtlauf geplant für {start.astimezone(BERLIN).isoformat()}", flush=True)
            sleep((start - now).total_seconds())
            continue
        try:
            if store is None:
                store = booking_store(connection)
            snapshot = refresh_hall_bookings(store, client, SEASON, "Automatischer Nachtlauf",
                                             due_since=start)
            if snapshot is not None:
                print(f"OMOC-Nachtlauf abgeschlossen: {len(snapshot.bookings)} Buchungen.", flush=True)
            next_start, _ = nightly_window(day + timedelta(days=1))
        except Exception as error:
            print(f"OMOC-Nachtlauf fehlgeschlagen: {type(error).__name__}", flush=True)
            next_start = min(end, datetime.now(timezone.utc) + RETRY_DELAY)
        sleep(max(0, (next_start - datetime.now(timezone.utc)).total_seconds()))


if __name__ == "__main__":
    connection = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    credentials = [os.getenv(key) for key in ("OMOC_BOOKINGS_URL", "OMOC_API_USERNAME", "OMOC_API_PASSWORD")]
    if connection and all(credentials):
        run = run_requested if sys.argv[1:] == ["--requests"] else run_nightly
        run(connection, OmocClient(*credentials))
    else:
        print("OMOC-Abruf: Konfiguration unvollständig.", flush=True)
