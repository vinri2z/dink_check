import logging
import time
from datetime import datetime, timedelta
from os import environ
from typing import Optional

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from .auth import AuthSession
from .models import ApiResponse, ReservationRequest, VolleyField

load_dotenv()
GET_BASE_URL = "https://dink.social/api/reservations/availabilities"
POST_BASE_URL = "https://dink.social/api/v2/reservations"
PLAYER_NUMBER = environ.get("PLAYER_NUMBER", "4")
DATE = environ.get("DATE", "2025-04-24")
START_TIME = environ.get("START_TIME", "09:00")
END_TIME = environ.get("END_TIME", "23:59")
DURATION = environ.get("DURATION", "60")
PLACE_DISTANCE = environ.get("PLACE_DISTANCE", "100")
PLACE_LATITUDE = environ.get("PLACE_LATITUDE", "39.4712827")
PLACE_LONGITUDE = environ.get("PLACE_LONGITUDE", "-0.3405378")
KEEPALIVE_INTERVAL = int(environ.get("DINK_KEEPALIVE_INTERVAL", "300"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main():
    logging.info("App started")
    auth = AuthSession.from_env()
    if not auth.validate():
        return
    if not _verify_credentials_at_startup(auth):
        return

    check_availability_and_book_field(
        auth=auth,
        player_number=PLAYER_NUMBER,
        date=DATE,
        start_time=START_TIME,
        end_time=END_TIME,
        duration=DURATION,
    )
    logging.info("Closing application")


def check_availability_and_book_field(
    auth: AuthSession,
    player_number: str,
    date: str,
    start_time: str,
    end_time: str,
    duration: str,
):
    last_keepalive = datetime.now()
    while True:
        if not auth.ensure_valid():
            logging.error("Stopping because credentials could not be refreshed.")
            break

        fields, auth_failed = find_free_field(
            auth=auth,
            url=GET_BASE_URL,
            player_number=player_number,
            date=date,
            start_time=start_time,
            duration=duration,
        )

        if auth_failed:
            logging.error("Stopping because authentication failed.")
            break

        if fields is None:
            logging.info("No availabilities from API, retrying in 60s")
            last_keepalive, should_continue = _wait_with_keepalive(auth, last_keepalive)
            if not should_continue:
                logging.error("Stopping because keepalive probe could not refresh credentials.")
                break
            continue

        logging.info(f"Found free fields: {fields}")
        if not _has_bookable_courts(fields):
            logging.error(
                "API returned venue-level availabilities without per-court ids. "
                "Compare GET /availabilities query params (day, time, duration, geo) "
                "with a successful request in mitmweb."
            )
            break

        selected_field = select_field_within_range(fields, duration, end_time)

        if selected_field is None:
            logging.info("No bookable slot yet, retrying in 60s")
            last_keepalive, should_continue = _wait_with_keepalive(auth, last_keepalive)
            if not should_continue:
                logging.error("Stopping because keepalive probe could not refresh credentials.")
                break
            continue

        logging.info(f"Found a valid field: {selected_field}")
        selected_field.numberOfPlayers = int(player_number)
        is_booked, auth_failed, fatal = book_field(
            auth=auth, field_to_book=selected_field
        )
        if auth_failed:
            logging.error("Stopping because authentication failed.")
            break
        if fatal:
            logging.error("Stopping because booking used an invalid field id.")
            break
        if is_booked:
            logging.info(f"Booked field at {selected_field.time}")
            break

        logging.warning(
            f"Booking failed for field at {selected_field.time}, retrying in 60s"
        )
        last_keepalive, should_continue = _wait_with_keepalive(auth, last_keepalive)
        if not should_continue:
            logging.error("Stopping because keepalive probe could not refresh credentials.")
            break


def _verify_credentials_at_startup(auth: AuthSession) -> bool:
    probe_result = auth.probe()
    if probe_result == "valid":
        logging.info("Keepalive probe confirmed credentials are accepted by the server.")
        return True
    if probe_result == "unknown":
        return True

    logging.warning("Startup keepalive probe rejected credentials; attempting refresh.")
    if auth.refresh() and auth.probe() == "valid":
        logging.info("Credentials refreshed and accepted by keepalive probe.")
        return True

    logging.error(
        "Credentials rejected by server. Re-capture BEARER_TOKEN and FINGERPRINT from "
        "the same Dink app request via mitmweb, or keep the app open with "
        "scripts/capture_dink_session.py running."
    )
    return False


def _wait_with_keepalive(auth: AuthSession, last_keepalive: datetime) -> tuple[datetime, bool]:
    now = datetime.now()
    if (now - last_keepalive).total_seconds() >= KEEPALIVE_INTERVAL:
        if not auth.keepalive():
            return last_keepalive, False
        return datetime.now(), True
    time.sleep(60)
    return last_keepalive, True


def find_free_field(
    auth: AuthSession,
    url,
    player_number,
    date,
    start_time,
    duration,
    *,
    allow_retry: bool = True,
) -> tuple[Optional[list[VolleyField]], bool]:
    params = {
        "number_players": player_number,
        "sport": "beach-volley",
        "place_distance": PLACE_DISTANCE,
        "place_latitude": PLACE_LATITUDE,
        "place_longitude": PLACE_LONGITUDE,
        "day": date,
        "time": start_time,
        "duration": duration,
    }

    data, auth_failed = _request_availabilities(auth, url, params)
    if auth_failed:
        return None, True
    if data is None:
        return None, False

    courts, auth_failed = _resolve_bookable_courts(
        auth, data.availabilities, player_number, date, start_time, duration
    )
    if auth_failed:
        return None, True

    if len(courts) > 0:
        return courts, False

    return None, False


def _request_availabilities(
    auth: AuthSession,
    url: str,
    params: dict,
    *,
    allow_retry: bool = True,
) -> tuple[Optional[ApiResponse], bool]:
    try:
        response = requests.get(
            url, params=params, headers=auth.headers(), timeout=30
        )
        response_body = response.json()
    except requests.exceptions.RequestException as error:
        logging.error("Cannot reach API")
        logging.error(error)
        raise

    if response.status_code == 401 and allow_retry:
        message = _api_message(response_body)
        if message == "INVALID_TOKEN" and auth.refresh():
            return _request_availabilities(auth, url, params, allow_retry=False)
        _log_auth_error(message)
        return None, True

    if response.status_code != 200:
        message = _api_message(response_body, response.text)
        logging.error(
            "Availability request failed (%s): %s",
            response.status_code,
            message,
        )
        if response.status_code == 401:
            _log_auth_error(message)
            return None, True
        return None, False

    try:
        return ApiResponse(**response_body), False
    except ValidationError as error:
        logging.error("Validation failed")
        logging.error(error)
        raise


def _resolve_bookable_courts(
    auth: AuthSession,
    availabilities: list[VolleyField],
    player_number: str,
    date: str,
    start_time: str,
    duration: str,
) -> tuple[list[VolleyField], bool]:
    """Expand venue-level rows into per-court availabilities.

    The geo ``/availabilities`` endpoint groups results by venue (a row with a
    ``location`` and no court ``id``). Per-court ids — required for booking —
    only come from ``/availabilities/locations/{location_id}``.
    """
    courts: list[VolleyField] = []
    seen_locations: set[str] = set()
    for entry in availabilities:
        if entry.booking_field_id is not None:
            courts.append(entry)
            continue
        location = entry.location
        if location is None or location.id in seen_locations:
            continue
        seen_locations.add(location.id)
        location_courts, auth_failed = _fetch_location_courts(
            auth, location.id, player_number, date, start_time, duration
        )
        if auth_failed:
            return courts, True
        courts.extend(location_courts)
    return courts, False


def _fetch_location_courts(
    auth: AuthSession,
    location_id: str,
    player_number: str,
    date: str,
    start_time: str,
    duration: str,
) -> tuple[list[VolleyField], bool]:
    url = f"{GET_BASE_URL}/locations/{location_id}"
    params = {
        "number_players": player_number,
        "sport": "beach-volley",
        "day": date,
        "time": start_time,
        "duration": duration,
    }
    data, auth_failed = _request_availabilities(auth, url, params)
    if data is None:
        return [], auth_failed
    return [
        court for court in data.availabilities if court.booking_field_id is not None
    ], auth_failed


def _format_start_at(start_at: datetime) -> str:
    iso = start_at.isoformat()
    if start_at.tzinfo is not None:
        return iso.replace("+00:00", "Z")
    return iso


def _has_bookable_courts(fields: list[VolleyField]) -> bool:
    return any(court.booking_field_id is not None for court in fields)


def select_field_within_range(
    fields: list[VolleyField], duration: str, end_time: str
) -> Optional[ReservationRequest]:
    end = datetime.strptime(end_time, "%H:%M").time()
    for court in fields:
        field_id = court.booking_field_id
        if field_id is None:
            logging.debug(
                "Skipping venue-only availability: %s", court.display_name
            )
            continue
        for slot in court.slots:
            if (slot.startAt + timedelta(minutes=int(duration))).time() < end:
                return ReservationRequest(
                    field=field_id,
                    time=slot.startAt.strftime("%H:%M"),
                    duration=int(duration),
                    startAt=_format_start_at(slot.startAt),
                    price=slot.price,
                )
    return None


def book_field(
    auth: AuthSession,
    field_to_book: ReservationRequest,
    *,
    allow_retry: bool = True,
) -> tuple[bool, bool, bool]:
    try:
        response = requests.post(
            POST_BASE_URL,
            json=field_to_book.model_dump(),
            headers=auth.headers(),
            timeout=30,
        )
    except requests.RequestException as error:
        logging.error(error)
        return False, False, False

    if response.status_code == 401 and allow_retry:
        try:
            response_body = response.json()
        except ValueError:
            response_body = {}
        message = _api_message(response_body, response.text)
        if message == "INVALID_TOKEN" and auth.refresh():
            return book_field(auth, field_to_book, allow_retry=False)
        _log_auth_error(message)
        return False, True, False

    if response.status_code != 201:
        logging.error(response.text)
        if response.status_code == 404 and _is_invalid_field_id_error(response.text):
            logging.error(
                "POST /v2/reservations expects availabilities[].id (court), "
                "not a venue/location id."
            )
            return False, False, True

    return response.status_code == 201, False, False


def _is_invalid_field_id_error(response_text: str) -> bool:
    lowered = response_text.lower()
    return "location field" in lowered or "not found" in lowered


def _api_message(response_body, fallback: str = "") -> str:
    if isinstance(response_body, dict):
        message = response_body.get("message", fallback)
        return message if isinstance(message, str) else fallback
    return fallback


def _log_auth_error(message: str) -> None:
    if message == "MISSING_TOKEN":
        logging.error("Set BEARER_TOKEN in your .env file.")
    elif message == "MISSING_FINGERPRINT":
        logging.error(
            "Set FINGERPRINT in your .env file (copy the x-fingerprint header from "
            "mitmweb alongside BEARER_TOKEN)."
        )
    elif message in {"INVALID_TOKEN", "INVALID_FINGERPRINT"}:
        logging.error(
            "Refresh credentials via DINK_REFRESH_URL, mitmweb session capture, or "
            "update BEARER_TOKEN and FINGERPRINT from the same Dink app request."
        )


if __name__ == "__main__":
    main()
