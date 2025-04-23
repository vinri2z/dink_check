import time
from datetime import datetime, timedelta
from typing import Optional

import requests
import logging
from os import environ

from pydantic.v1 import ValidationError

from models import ApiResponse, VolleyField, ReservationRequest

GET_BASE_URL = "https://dink.social/api/reservations/availabilities/locations/acc9fc91-d427-11ef-8000-000000000000"
POST_BASE_URL = "https://dink.social/api/v2/reservations"
PLAYER_NUMBER = environ.get("PLAYER_NUMBER", "4")
DATE = environ.get("DATE", "2025-04-24")
START_TIME = environ.get("START_TIME", "09:00")
END_TIME = environ.get("END_TIME", "23:59")
DURATION = environ.get("DURATION", "60")
BEARER_TOKEN = environ.get("BEARER_TOKEN")
AUTH_HEADER = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
}

# Configure logger
logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, CRITICAL
    format="%(asctime)s - %(levelname)s - %(message)s",  # timestamp + level + message
    datefmt="%Y-%m-%d %H:%M:%S",  # time format
)


def main():
    logging.info("App started")
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

        if fields is not None:
            logging.info(f"Found free fields: {fields}")
            selected_field = select_field_within_range(fields, duration, end_time)

            if selected_field is not None:
                logging.info(f"Found a valid field: {selected_field}")
                selected_field.numberOfPlayers = int(player_number)
                is_booked = book_field(field_to_book=selected_field)
                if is_booked:
                    logging.info(f"Booked field at {selected_field.time}")
                else:
                    logging.error(
                        f"Something went wrong booking field at {selected_field.time}"
                    )
            else:
                logging.error("No field available today")

            break

        logging.info("No fields available, retrying in 60s")
        time.sleep(60)


def find_free_field(
    url, player_number, date, start_time, duration
) -> Optional[list[VolleyField]]:
    try:
        response = requests.get(
            url,
            params={
                "number_of_players": player_number,
                "sport": "beach-volley",
                "day": date,
                "time": start_time,
                "duration": duration,
            },
            headers=AUTH_HEADER,
        )
        data = ApiResponse(**response.json())
    except requests.exceptions.RequestException as e:
        logging.error("Cannot reach API")
        logging.error(e)
        raise
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
            if ( slot.startAt + timedelta(minutes=int(duration)) ).time() < (datetime.strptime(
                end_time, "%H:%M"
            )).time():
                return ReservationRequest(
                    field=field.id,
                    time=slot.startAt.strftime("%H:%M"),
                    duration=int(duration),
                    startAt=slot.startAt.isoformat() + 'Z',
                )


def book_field(field_to_book: ReservationRequest) -> bool:
    try:
        response = requests.post(
            POST_BASE_URL, json=field_to_book.model_dump(), headers=AUTH_HEADER
        )
    except Exception as error:
        logging.error(error)
        return False
    
    if response.status_code != 201:
        logging.error(response.text)

    return response.status_code == 201


if __name__ == "__main__":
    main()
