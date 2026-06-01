import json
import unittest
from pathlib import Path

from dink_check.main import _has_bookable_courts, select_field_within_range
from dink_check.models import ApiResponse

FIXTURE = Path(__file__).parent / "fixtures" / "availabilities_2026-06-02.json"
VENUE_FIXTURE = Path(__file__).parent / "fixtures" / "availabilities_venue_wrapped.json"
PISTA_16_ID = "d3566cef-3e43-4ba0-93b6-2a3f02c6a4ea"
PISTA_17_ID = "4fae03ee-7864-4750-ad01-e63c9a893715"


class BookingTests(unittest.TestCase):
    def test_parse_api_response(self):
        data = ApiResponse(**json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.assertFalse(data.overDailyQuota)
        self.assertEqual(len(data.availabilities), 9)
        self.assertEqual(data.availabilities[0].name, "PISTA 16")
        self.assertEqual(data.availabilities[0].booking_field_id, PISTA_16_ID)

    def test_select_field_uses_first_bookable_court(self):
        data = ApiResponse(**json.loads(FIXTURE.read_text(encoding="utf-8")))
        selected = select_field_within_range(data.availabilities, "90", "21:00")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.field, PISTA_16_ID)
        self.assertEqual(selected.time, "16:00")
        self.assertEqual(selected.duration, 90)
        self.assertEqual(selected.price, 0.0)
        self.assertEqual(selected.startAt, "2026-06-02T16:00:00")

    def test_select_field_can_pick_later_court_when_first_excluded(self):
        courts = ApiResponse(
            **json.loads(FIXTURE.read_text(encoding="utf-8"))
        ).availabilities
        without_pista_16 = courts[1:]
        selected = select_field_within_range(without_pista_16, "90", "21:00")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.field, PISTA_17_ID)
        self.assertEqual(selected.time, "16:00")

    def test_select_field_returns_none_when_slot_exceeds_end_time(self):
        data = ApiResponse(**json.loads(FIXTURE.read_text(encoding="utf-8")))
        selected = select_field_within_range(data.availabilities, "90", "16:00")
        self.assertIsNone(selected)

    def test_venue_wrapped_availability_has_no_booking_field_id(self):
        data = ApiResponse(**json.loads(VENUE_FIXTURE.read_text(encoding="utf-8")))
        court = data.availabilities[0]
        self.assertIsNone(court.booking_field_id)
        self.assertEqual(
            court.display_name,
            "Instalación Municipal de Vóley Playa - Beachbol Valencia",
        )

    def test_venue_only_response_is_not_bookable(self):
        data = ApiResponse(**json.loads(VENUE_FIXTURE.read_text(encoding="utf-8")))
        self.assertFalse(_has_bookable_courts(data.availabilities))
        self.assertIsNone(
            select_field_within_range(data.availabilities, "90", "21:00")
        )


if __name__ == "__main__":
    unittest.main()
