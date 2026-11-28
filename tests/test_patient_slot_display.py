"""Regression checks for the patient doctor availability UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_patient_doctor_page_uses_slot_cards_and_availability_states():
    template = (ROOT / "doctor_dashboard/templates/patient_doctor.html").read_text()
    assert 'class="time-slot ' in template
    assert "slot.booked_by_current_patient" in template
    assert "is-booked-by-you" in template
    assert "✓ You have booked" in template
    assert "is-booked" in template
    assert "slot.free_seats" in template
    assert "Fully booked" in template
    assert "consultation_fee" in template
    assert "consultation_duration" in template
    assert "data-book-slot" in template
    assert "Book appointment" in template
    assert "appointmentSummary" not in template
    assert "booking is not implemented yet" not in template
    assert "Consultations &amp; Fees" not in template
    assert "<table>" not in template


def test_patient_route_enriches_slots_with_ist_display_fields():
    app = (ROOT / "doctor_dashboard/app.py").read_text()
    assert "slot['local_date']" in app
    assert "slot['local_date_label']" in app
    assert "slot['local_time']" in app
    assert "slot['free_seats'] = int(slot.get('freeSeats', 0) or 0)" in app
    assert "consultation_types = {" in app



def test_patient_doctor_search_cards_render_private_profile_photos():
    template = (ROOT / "doctor_dashboard/templates/patient_search.html").read_text()
    assert "doctor.profile_photo_url" in template
    assert "doctor-search-photo" in template
    patient_api = (ROOT / "lambdas/patient_domain/patient_domain_lambda.py").read_text()
    assert 'd.photo_key' in patient_api
    assert 'generate_presigned_url' in patient_api
    assert 'item["profile_photo_url"]' in patient_api


def test_patient_slots_api_keeps_fully_booked_slots_visible():
    patient_api = (ROOT / "lambdas/patient_domain/patient_domain_lambda.py").read_text()
    assert "# Keep fully booked slots visible to patients." in patient_api
    assert "if free_seats <= 0:\n            continue" not in patient_api


def test_patient_cancel_template_displays_refund_policy_and_estimate():
    template = (ROOT / "doctor_dashboard/templates/patient_appointments.html").read_text()
    assert "More than 24 hours before: 100%" in template
    assert "12–24 hours before: 75%" in template
    assert "3–12 hours before: 50%" in template
    assert "Less than 3 hours before: 0%" in template
    assert "refund_estimate_amount" in template


def test_doctor_completion_button_is_disabled_until_eligible():
    template = (ROOT / "doctor_dashboard/templates/doctor_appointments.html").read_text()
    assert "not item.get('completion_eligible')" in template
    api = (ROOT / "lambdas/appointment_api/appointment_api_lambda.py").read_text()
    assert "Appointment can be marked completed only after its scheduled end time" in api
