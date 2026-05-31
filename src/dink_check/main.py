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
            time.sleep(60)
            continue

        logging.info(f"Found free fields: {fields}")
        selected_field = select_field_within_range(fields, duration, end_time)

        if selected_field is None:
            logging.info("No bookable slot yet, retrying in 60s")
            time.sleep(60)
            continue

        logging.info(f"Found a valid field: {selected_field}")
        selected_field.numberOfPlayers = int(player_number)
        is_booked, auth_failed = book_field(auth=auth, field_to_book=selected_field)
        if auth_failed:
            logging.error("Stopping because authentication failed.")
            break
        if is_booked:
            logging.info(f"Booked field at {selected_field.time}")
            break

        logging.warning(
            f"Booking failed for field at {selected_field.time}, retrying in 60s"
        )
        time.sleep(60)


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
            return find_free_field(
                auth,
                url,
                player_number,
                date,
                start_time,
                duration,
                allow_retry=False,
            )
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
        data = ApiResponse(**response_body)
    except ValidationError as error:
        logging.error("Validation failed")
        logging.error(error)
        raise

    if len(data.availabilities) > 0:
        return data.availabilities, False

    return None, False


def select_field_within_range(
    fields: list[VolleyField], duration: str, end_time: str
) -> Optional[ReservationRequest]:
    for field in fields:
        for slot in field.slots:
            if (slot.startAt + timedelta(minutes=int(duration))).time() < (
                datetime.strptime(end_time, "%H:%M")
            ).time():
                return ReservationRequest(
                    field=field.location.id,
                    time=slot.startAt.strftime("%H:%M"),
                    duration=int(duration),
                    startAt=slot.startAt.isoformat() + "Z",
                )


def book_field(
    auth: AuthSession,
    field_to_book: ReservationRequest,
    *,
    allow_retry: bool = True,
) -> tuple[bool, bool]:
    try:
        response = requests.post(
            POST_BASE_URL,
            json=field_to_book.model_dump(),
            headers=auth.headers(),
            timeout=30,
        )
    except requests.RequestException as error:
        logging.error(error)
        return False, False

    if response.status_code == 401 and allow_retry:
        try:
            response_body = response.json()
        except ValueError:
            response_body = {}
        message = _api_message(response_body, response.text)
        if message == "INVALID_TOKEN" and auth.refresh():
            return book_field(auth, field_to_book, allow_retry=False)
        _log_auth_error(message)
        return False, True

    if response.status_code != 201:
        logging.error(response.text)

    return response.status_code == 201, False


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
