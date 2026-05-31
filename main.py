import base64
import json
import logging
import time
from datetime import datetime, timedelta
from os import environ
from typing import Optional

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from models import ApiResponse, ReservationRequest, VolleyField

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
BEARER_TOKEN = (environ.get("BEARER_TOKEN") or "").strip()
FINGERPRINT = (environ.get("FINGERPRINT") or "").strip()


def _jwt_expiry(token: str) -> Optional[datetime]:
    try:
        payload_segment = token.split(".")[1]
        payload_segment += "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        exp = payload.get("exp")
        return datetime.fromtimestamp(exp) if isinstance(exp, (int, float)) else None
    except (IndexError, ValueError, json.JSONDecodeError, OSError):
        return None


def build_auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {
        "accept": "*/*",
        "accept-language": "es",
        "x-application": "mobile",
        "x-app-bundle-id": "it.dink.www",
        "x-platform": "ios",
        "origin": "capacitor://localhost",
        "user-agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ),
    }
    if BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {BEARER_TOKEN}"
    if FINGERPRINT:
        headers["x-fingerprint"] = FINGERPRINT
    return headers


def validate_auth_config() -> None:
    if not BEARER_TOKEN:
        logging.error("BEARER_TOKEN is missing from .env")
        return

    token_expiry = _jwt_expiry(BEARER_TOKEN)
    if token_expiry and datetime.now() >= token_expiry:
        logging.error(
            "BEARER_TOKEN expired at %s. Capture a fresh token via mitmweb.",
            token_expiry.strftime("%Y-%m-%d %H:%M:%S"),
        )

    if not FINGERPRINT:
        logging.error(
            "FINGERPRINT is missing from .env. Copy the x-fingerprint header from "
            "the same mitmweb request as BEARER_TOKEN."
        )
        return


# Configure logger
logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, CRITICAL
    format="%(asctime)s - %(levelname)s - %(message)s",  # timestamp + level + message
    datefmt="%Y-%m-%d %H:%M:%S",  # time format
)


def main():
    logging.info("App started")
    validate_auth_config()
    check_availability_and_book_field(
        player_number=PLAYER_NUMBER,
        date=DATE,
        start_time=START_TIME,
        end_time=END_TIME,
        duration=DURATION,
    )
    logging.info("Closing application")


def check_availability_and_book_field(
    player_number: str, date: str, start_time: str, end_time: str, duration: str
):
    while True:
        fields = find_free_field(
            url=GET_BASE_URL,
            player_number=player_number,
            date=date,
            start_time=start_time,
            duration=duration,
        )

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
        is_booked = book_field(field_to_book=selected_field)
        if is_booked:
            logging.info(f"Booked field at {selected_field.time}")
            break

        logging.warning(
            f"Booking failed for field at {selected_field.time}, retrying in 60s"
        )
        time.sleep(60)


def find_free_field(
    url, player_number, date, start_time, duration
) -> Optional[list[VolleyField]]:
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
    headers = build_auth_headers()
    try:
        response = requests.get(url, params=params, headers=headers)
        response_body = response.json()
    except requests.exceptions.RequestException as e:
        logging.error("Cannot reach API")
        logging.error(e)
        raise

    if response.status_code != 200:
        message = (
            response_body.get("message", response.text)
            if isinstance(response_body, dict)
            else response.text
        )
        logging.error(
            "Availability request failed (%s): %s",
            response.status_code,
            message,
        )
        if response.status_code == 401:
            if message == "MISSING_TOKEN":
                logging.error("Set BEARER_TOKEN in your .env file.")
            elif message == "MISSING_FINGERPRINT":
                logging.error(
                    "Set FINGERPRINT in your .env file (copy the X-Fingerprint "
                    "header from mitmweb alongside BEARER_TOKEN)."
                )
            elif message in {"INVALID_TOKEN", "INVALID_FINGERPRINT"}:
                logging.error(
                    "Refresh BEARER_TOKEN and FINGERPRINT from the same Dink app "
                    "request in mitmweb, then retry."
                )
        return None

    try:
        data = ApiResponse(**response_body)
    except ValidationError as e:
        logging.error("Validation failed")
        logging.error(e)
        raise

    if len(data.availabilities) > 0:
        return data.availabilities

    return None


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


def book_field(field_to_book: ReservationRequest) -> bool:
    try:
        response = requests.post(
            POST_BASE_URL,
            json=field_to_book.model_dump(),
            headers=build_auth_headers(),
        )
    except Exception as error:
        logging.error(error)
        return False

    if response.status_code != 201:
        logging.error(response.text)

    return response.status_code == 201


if __name__ == "__main__":
    main()
