from pydantic import BaseModel
from typing import List, Literal
from datetime import datetime


# Model for the Slot object
class Slot(BaseModel):
    startAt: datetime
    price: float


# Model for the nested location object on each availability
class Location(BaseModel):
    id: str
    name: str


# Model for the Availability object (now grouped by location)
class VolleyField(BaseModel):
    location: Location
    slots: List[Slot]


# Model for the main response
class ApiResponse(BaseModel):
    availabilities: List[VolleyField]


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
