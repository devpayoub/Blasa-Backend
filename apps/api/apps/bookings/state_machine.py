class BookingStatus:
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED_BY_PASSENGER = "CANCELLED_BY_PASSENGER"
    CANCELLED_BY_DRIVER = "CANCELLED_BY_DRIVER"
    NO_SHOW = "NO_SHOW"

    CHOICES = [
        (REQUESTED, "Requested"),
        (ACCEPTED, "Accepted"),
        (CONFIRMED, "Confirmed"),
        (COMPLETED, "Completed"),
        (REJECTED, "Rejected"),
        (CANCELLED_BY_PASSENGER, "Cancelled by passenger"),
        (CANCELLED_BY_DRIVER, "Cancelled by driver"),
        (NO_SHOW, "No show"),
    ]


TERMINAL = {
    BookingStatus.COMPLETED,
    BookingStatus.REJECTED,
    BookingStatus.CANCELLED_BY_PASSENGER,
    BookingStatus.CANCELLED_BY_DRIVER,
    BookingStatus.NO_SHOW,
}

S = BookingStatus
TRANSITIONS = {
    (S.REQUESTED, "accept"): S.ACCEPTED,
    (S.REQUESTED, "reject"): S.REJECTED,
    (S.REQUESTED, "cancel_passenger"): S.CANCELLED_BY_PASSENGER,
    (S.REQUESTED, "cancel_driver"): S.CANCELLED_BY_DRIVER,
    (S.ACCEPTED, "confirm"): S.CONFIRMED,
    (S.ACCEPTED, "cancel_passenger"): S.CANCELLED_BY_PASSENGER,
    (S.ACCEPTED, "cancel_driver"): S.CANCELLED_BY_DRIVER,
    (S.CONFIRMED, "complete"): S.COMPLETED,
    (S.CONFIRMED, "no_show"): S.NO_SHOW,
    (S.CONFIRMED, "cancel_passenger"): S.CANCELLED_BY_PASSENGER,
    (S.CONFIRMED, "cancel_driver"): S.CANCELLED_BY_DRIVER,
}


class InvalidTransition(Exception):
    pass


def next_status(current: str, action: str) -> str:
    if current in TERMINAL:
        raise InvalidTransition(f"Booking is in terminal state {current}")
    try:
        return TRANSITIONS[(current, action)]
    except KeyError:
        raise InvalidTransition(f"Action '{action}' not allowed from {current}")
