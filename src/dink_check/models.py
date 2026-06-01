from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class Slot(BaseModel):
    startAt: datetime
    price: float


class Location(BaseModel):
    id: str
    name: str


class VolleyField(BaseModel):
    id: str | None = None
    name: str | None = None
    location: Location | None = None
    slots: list[Slot]

    @model_validator(mode="after")
    def require_court_or_location(self) -> "VolleyField":
        if self.id is None and self.location is None:
            raise ValueError("availability must have court id or location")
        return self

    @property
    def booking_field_id(self) -> str | None:
        return self.id

    @property
    def display_name(self) -> str:
        if self.name is not None:
            return self.name
        if self.location is not None:
            return self.location.name
        return "unknown"


class ApiResponse(BaseModel):
    overDailyQuota: bool = False
    overWeeklyQuota: bool = False
    availabilities: list[VolleyField]


class ReservationRequest(BaseModel):
    numberOfPlayers: int = 4
    type: Literal["reservation"] = "reservation"
    mode: Literal["save"] = "save"
    field: str
    sportId: Literal["beach-volley"] = "beach-volley"
    startAt: str
    time: str
    duration: int = 60
    price: float = 0
