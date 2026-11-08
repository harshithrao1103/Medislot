"""Regression checks for the patient doctor availability UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_patient_doctor_page_uses_selectable_slots_and_availability_states():
    template = (ROOT / "doctor_dashboard/templates/patient_doctor.html").read_text()
    assert "class=\"time-slot {% if slot.free_seats > 0 %}is-available" in template
    assert "is-booked" in template
    assert "slot.free_seats" in template
    assert "Fully booked" in template
    assert "appointmentSummary" in template
    assert "booking is not implemented yet" in template
    assert "<table>" not in template


def test_patient_route_enriches_slots_with_ist_display_fields():
    app = (ROOT / "doctor_dashboard/app.py").read_text()
    assert "slot['local_date']" in app
    assert "slot['local_date_label']" in app
    assert "slot['local_time']" in app
    assert "slot['free_seats'] = int(slot.get('freeSeats', 0) or 0)" in app
    assert "consultation_types = {" in app
