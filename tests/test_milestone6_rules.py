import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lambdas', 'milestone6'))
from milestone6_rules import fifo_waiting_entries, offer_is_expired, dispatch_id, reschedule_allowed


def test_waitlist_fifo_uses_join_timestamp_and_entry_id():
    rows = [
        {'SK': 'ENTRY#2026-11-27T10:02:00Z#b', 'status': 'WAITING'},
        {'SK': 'ENTRY#2026-11-27T10:01:00Z#c', 'status': 'LEFT'},
        {'SK': 'ENTRY#2026-11-27T10:01:00Z#a', 'status': 'WAITING'},
    ]
    assert [r['SK'] for r in fifo_waiting_entries(rows)] == [
        'ENTRY#2026-11-27T10:01:00Z#a', 'ENTRY#2026-11-27T10:02:00Z#b'
    ]


def test_offer_expiry_boundary_is_inclusive():
    instant = dt.datetime(2026, 11, 27, 10, 15, tzinfo=dt.timezone.utc)
    assert offer_is_expired('2026-11-27T10:15:00+00:00', instant)
    assert not offer_is_expired('2026-11-27T10:15:01+00:00', instant)


def test_dispatch_id_is_stable_and_recipient_specific():
    first = dispatch_id('appt-1', 'AppointmentBooked', 'patient-1')
    assert first == dispatch_id('appt-1', 'AppointmentBooked', 'patient-1')
    assert first != dispatch_id('appt-1', 'AppointmentBooked', 'doctor-1')
    assert first != dispatch_id('appt-1', 'AppointmentRescheduled', 'patient-1')


def test_reschedule_rules_enforce_two_attempts_and_six_hour_cutoff():
    now = dt.datetime(2026, 11, 27, 10, tzinfo=dt.timezone.utc)
    exactly_six = now + dt.timedelta(hours=6)
    assert reschedule_allowed('CONFIRMED', 1, exactly_six, now) == (True, 'OK')
    assert reschedule_allowed('CONFIRMED', 2, exactly_six, now) == (False, 'LIMIT_REACHED')
    assert reschedule_allowed('CONFIRMED', 0, now + dt.timedelta(hours=5, minutes=59), now) == (False, 'CUTOFF_REACHED')
    assert reschedule_allowed('CANCELLED_BY_PATIENT', 0, exactly_six, now) == (False, 'NOT_CONFIRMED')
