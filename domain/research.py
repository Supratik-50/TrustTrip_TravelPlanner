from enum import StrEnum


class TripIntent(StrEnum):
    NEW_TRIP = "new_trip"
    FOLLOW_UP = "follow_up"
    GLOBAL_FLIGHT_RESEARCH = "global_flight_research"
    BOOKING_OPERATION = "booking_operation"
