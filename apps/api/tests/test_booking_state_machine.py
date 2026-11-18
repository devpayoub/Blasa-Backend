import pytest

from apps.bookings.state_machine import BookingStatus, InvalidTransition, next_status


def test_happy_path_transitions():
    status = BookingStatus.REQUESTED
    status = next_status(status, "accept")
    status = next_status(status, "confirm")
    status = next_status(status, "complete")
    assert status == BookingStatus.COMPLETED


def test_terminal_state_rejects_actions():
    with pytest.raises(InvalidTransition):
        next_status(BookingStatus.COMPLETED, "cancel_passenger")


def test_invalid_action_from_requested():
    with pytest.raises(InvalidTransition):
        next_status(BookingStatus.REQUESTED, "complete")
