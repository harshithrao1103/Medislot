"""Tests for exact consultation-duration availability validation."""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "lambdas" / "_shared"),
    str(ROOT / "lambdas" / "doctor_api"),
]

from doctor_common import ApiError
from doctor_api_lambda import validate_availability_duration


@pytest.mark.parametrize(
    "start,end,duration",
    [
        ("09:00", "10:00", 15),
        ("09:00", "10:00", 30),
        ("09:00", "09:45", 15),
        ("09:00", "10:30", 45),
        ("09:00", "11:00", 60),
        ("09:16", "09:31", 15),
    ],
)
def test_accepts_exact_multiple_windows(start, end, duration):
    validate_availability_duration(
        dt.time.fromisoformat(start),
        dt.time.fromisoformat(end),
        duration,
    )


@pytest.mark.parametrize(
    "start,end,duration",
    [
        ("09:16", "09:30", 15),
        ("09:00", "09:31", 15),
        ("09:00", "09:29", 15),
        ("09:00", "09:30", 45),
        ("09:00", "09:59", 30),
    ],
)
def test_rejects_short_or_remainder_windows(start, end, duration):
    with pytest.raises(ApiError):
        validate_availability_duration(
            dt.time.fromisoformat(start),
            dt.time.fromisoformat(end),
            duration,
        )
