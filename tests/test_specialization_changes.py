"""Static regression checks for the specialization API and forms."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_dropdowns_use_ids():
    for name in ('register.html', 'profile.html'):
        template = (ROOT / 'doctor_dashboard/templates' / name).read_text()
        assert 'select name="specialization_id"' in template
        assert 'name="specialization"' not in template

def test_profile_uses_update_and_foreign_key():
    api = (ROOT / 'lambdas/doctor_api/doctor_api_lambda.py').read_text()
    schema = (ROOT / 'database/schema.sql').read_text()
    assert 'UPDATE doctors SET ' in api
    assert 'WHERE user_id=%s' in api
    assert 'fk_doctors_specialization' in schema
    assert 'specialization_id BIGINT NOT NULL' in schema

def test_public_specializations_route():
    cf = (ROOT / 'Cloudformation/Application_Stack.yaml').read_text()
    assert 'PathPart: specializations' in cf
    assert 'SpecializationsGet:' in cf

def test_patient_search_uses_specialization_dropdown_and_id_filter():
    template = (ROOT / 'doctor_dashboard/templates/patient_search.html').read_text()
    api = (ROOT / 'lambdas/patient_domain/patient_domain_lambda.py').read_text()
    app = (ROOT / 'doctor_dashboard/app.py').read_text()
    assert '<select name="specialization_id"' in template
    assert 'specialization.specialization_name' in template
    assert "filters['specialization_id']" in app
    assert "clauses.append('d.specialization_id=%s')" in api
