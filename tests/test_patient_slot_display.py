"""Regression checks for patient-friendly consultation details on doctor slots."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_patient_doctor_page_displays_consultation_details_not_internal_id():
    template = (ROOT / "doctor_dashboard/templates/patient_doctor.html").read_text()
    assert "<th>Consultation type</th>" in template
    assert "<th>Duration</th>" in template
    assert "<th>Fee</th>" in template
    assert "Consultation type ID" not in template
    assert "slot.consultation_type_name" in template
    assert "slot.consultation_duration" in template
    assert "slot.consultation_fee" in template


def test_patient_route_maps_slot_consultation_id_to_profile_details():
    app = (ROOT / "doctor_dashboard/app.py").read_text()
    assert "consultation_types = {" in app
    assert "str(slot.get('consultationTypeId'))" in app
    assert "slot['consultation_type_name']" in app
    assert "slot['consultation_duration']" in app
    assert "slot['consultation_fee']" in app
